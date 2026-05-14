"""Pi Camera 캡처 모듈 (FR-2).

- ``picamera2``로 1920x1080 JPEG 캡처
- VEHICLE_DETECTED 수신 후 500ms 이내 캡처 (NFR-3 성능)
- 카메라는 항시 warm-up 유지 (FR-2.1.4)
- 캡처된 이미지는 BytesIO + 로컬 캐시 동시 저장 (FR-2.2.1~3)
"""

from __future__ import annotations

import io
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import settings
from src.utils.logger import logger


class _PiCameraBackend:
    def __init__(self) -> None:
        from picamera2 import Picamera2  # type: ignore

        self._cam = Picamera2()
        config = self._cam.create_still_configuration(
            main={
                "size": (settings.camera_width, settings.camera_height),
                "format": "RGB888",
            }
        )
        self._cam.configure(config)
        self._cam.start()
        logger.info(
            f"picamera2 warm-up 완료 "
            f"({settings.camera_width}x{settings.camera_height})"
        )

    def capture_jpeg(self) -> bytes:
        from PIL import Image  # picamera2 의존성에 포함

        array = self._cam.capture_array("main")
        img = Image.fromarray(array)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=settings.camera_jpeg_quality)
        return buf.getvalue()

    def close(self) -> None:
        try:
            self._cam.stop()
        except Exception:
            pass


class _NullCameraBackend:
    """picamera2 미설치 환경용 폴백 — 1x1 검은 JPEG 반환."""

    _BLANK_JPEG = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00" + b"\x08" * 64
        + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00"
        b"\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfa\xff\xd9"
    )

    def capture_jpeg(self) -> bytes:
        return self._BLANK_JPEG

    def close(self) -> None:
        return None


def _make_backend():
    try:
        return _PiCameraBackend()
    except Exception as exc:
        logger.error(f"picamera2 초기화 실패 — null 백엔드 사용: {exc}")
        return _NullCameraBackend()


class CameraCapturer:
    def __init__(self) -> None:
        # FR-2.1.4 — 카메라 핸들은 공유 자원이므로 lock 보호 (NFR-1.2.4)
        self._lock = threading.Lock()
        self._backend = _make_backend()
        settings.image_cache_dir.mkdir(parents=True, exist_ok=True)

    def capture(self, event_id: str, captured_at: Optional[datetime] = None) -> tuple[bytes, str, Path]:
        """이미지 캡처. (jpeg_bytes, filename, cached_path) 반환."""
        captured_at = captured_at or datetime.now(timezone.utc)
        ts = captured_at.strftime("%Y%m%d_%H%M%S") + f"{captured_at.microsecond // 1000:03d}"
        filename = f"{event_id}_{ts}.jpg"

        with self._lock:
            jpeg = self._backend.capture_jpeg()

        cache_path = settings.image_cache_dir / filename
        try:
            cache_path.write_bytes(jpeg)
            try:
                cache_path.chmod(0o600)
            except Exception:
                pass
        except Exception as exc:
            logger.warning(f"이미지 캐시 저장 실패: {exc}")

        logger.debug(f"이미지 캡처 완료: {filename} ({len(jpeg)} bytes)")
        return jpeg, filename, cache_path

    def close(self) -> None:
        with self._lock:
            self._backend.close()
