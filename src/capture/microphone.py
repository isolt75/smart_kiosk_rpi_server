"""마이크 캡처 모듈 (FR-3).

- ``sounddevice``로 16kHz/16bit/mono PCM 스트림 입력
- 차량 감지 시 녹음 시작, 최대 30초
- ``webrtcvad``로 2초간 무음 지속 시 자동 종료
- WAV 포맷으로 BytesIO 인코딩 + 로컬 캐시 저장
"""

from __future__ import annotations

import io
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import settings
from src.utils.logger import logger


def _ms_to_frames(ms: int, sr: int) -> int:
    return int(sr * ms / 1000)


class _NullStream:
    """sounddevice 미설치 환경용 폴백 — 무음 PCM 생성."""

    def __init__(self, samplerate: int, channels: int, blocksize: int) -> None:
        self._samplerate = samplerate
        self._channels = channels
        self._blocksize = blocksize
        self._closed = False

    def start(self) -> None:
        return None

    def read(self, frames: int):
        time.sleep(frames / self._samplerate)
        return bytes(frames * 2 * self._channels), False

    def stop(self) -> None:
        self._closed = True

    def close(self) -> None:
        self._closed = True


def _open_stream(samplerate: int, channels: int, blocksize: int):
    try:
        import sounddevice as sd  # type: ignore

        stream = sd.RawInputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            blocksize=blocksize,
        )
        stream.start()
        return stream
    except Exception as exc:
        logger.error(f"sounddevice 초기화 실패 — null 스트림 사용: {exc}")
        return _NullStream(samplerate, channels, blocksize)


def _make_vad():
    try:
        import webrtcvad  # type: ignore

        return webrtcvad.Vad(settings.audio_vad_aggressiveness)
    except Exception as exc:
        logger.warning(f"webrtcvad 미사용 ({exc}) — silence 감지 비활성")
        return None


class MicRecorder:
    """녹음 1회당 1 인스턴스 사용 (start → wait_until_done)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        settings.audio_cache_dir.mkdir(parents=True, exist_ok=True)

    def record(self, event_id: str) -> tuple[bytes, str, Path, float]:
        """녹음 → (wav_bytes, filename, cached_path, duration_sec) 반환."""
        sr = settings.audio_sample_rate
        ch = settings.audio_channels
        frame_ms = settings.audio_frame_duration_ms
        frame_size = _ms_to_frames(frame_ms, sr)  # 샘플 수
        block_bytes = frame_size * 2 * ch  # int16

        vad = _make_vad()
        stream = _open_stream(sr, ch, frame_size)

        max_frames = int(settings.audio_max_duration_sec * 1000 / frame_ms)
        silence_frames_required = int(settings.audio_vad_silence_sec * 1000 / frame_ms)

        started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()
        ring: deque[bytes] = deque()  # FR-3.2.1 ring buffer (1024 프레임 단위는
                                       # blocksize 기반 — 여기서는 frame 단위)
        silence_streak = 0

        try:
            with self._lock:
                for _ in range(max_frames):
                    data, _overflow = stream.read(frame_size)
                    if isinstance(data, memoryview):
                        data = bytes(data)
                    ring.append(data)
                    if vad is not None:
                        try:
                            is_speech = vad.is_speech(data, sr)
                        except Exception:
                            is_speech = True
                        silence_streak = 0 if is_speech else silence_streak + 1
                        if silence_streak >= silence_frames_required:
                            logger.debug("VAD 무음 종료 트리거")
                            break
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        duration = time.monotonic() - t0
        pcm = b"".join(ring)

        # FR-3.2.2 WAV 인코딩
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(ch)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm)
        wav_bytes = buf.getvalue()

        ts = started_at.strftime("%Y%m%d_%H%M%S") + f"{started_at.microsecond // 1000:03d}"
        filename = f"{event_id}_{ts}.wav"
        cache_path = settings.audio_cache_dir / filename
        try:
            cache_path.write_bytes(wav_bytes)
            try:
                cache_path.chmod(0o600)
            except Exception:
                pass
        except Exception as exc:
            logger.warning(f"오디오 캐시 저장 실패: {exc}")

        logger.info(
            f"오디오 녹음 완료: {filename} ({duration:.2f}s, {len(wav_bytes)} bytes)"
        )
        return wav_bytes, filename, cache_path, duration
