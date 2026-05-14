"""엣지 서버 엔트리포인트.

스레드 구성 (NFR-1.1):
- MainThread: 부트스트랩, 시그널, 스케줄러 호스팅
- SensorThread: HC-SR04 50ms 폴링 (UltrasonicMonitor)
- CaptureThreads: VEHICLE_DETECTED 수신 시 이미지/오디오 동시 캡처 (각 1 스레드)
- SenderThread: 전송 큐 폴링 → ThreadPoolExecutor(max_workers=4)로 병렬 업로드
"""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone

import psutil
from apscheduler.schedulers.background import BackgroundScheduler

from src.capture.camera import CameraCapturer
from src.capture.microphone import MicRecorder
from src.communication.api_client import APIClient
from src.config import settings
from src.core.event_bus import (
    Event,
    EventBus,
    VEHICLE_DEPARTED,
    VEHICLE_DETECTED,
)
from src.core.queue_manager import (
    AUDIO_JOB,
    IMAGE_JOB,
    PlatePayload,
    TransmitJob,
    TransmitQueue,
)
from src.inference.plate_detector import make_plate_detector
from src.sensors.ultrasonic import UltrasonicMonitor
from src.utils.cache import cleanup_all_caches
from src.utils.leds import StatusLeds
from src.utils.logger import logger, setup_logging


STARTED_AT = time.monotonic()


def _system_metrics(queue: TransmitQueue) -> dict:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    return {
        "cpu_usage": psutil.cpu_percent(interval=None),
        "memory_usage_mb": round((vm.total - vm.available) / (1024 * 1024), 1),
        "disk_free_gb": round(du.free / (1024 ** 3), 2),
        "uptime_sec": int(time.monotonic() - STARTED_AT),
        "pending_queue_size": queue.qsize(),
    }


class EdgeServer:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._bus = EventBus()
        self._queue = TransmitQueue(maxsize=settings.queue_max_size)
        self._camera = CameraCapturer()
        self._mic = MicRecorder()
        self._plate_detector = make_plate_detector()
        self._api = APIClient()
        self._leds = StatusLeds()
        self._sensor = UltrasonicMonitor(self._bus)
        self._capture_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="Capture")
        self._http_pool = ThreadPoolExecutor(
            max_workers=settings.http_max_workers, thread_name_prefix="Sender"
        )
        self._sender_thread = threading.Thread(
            target=self._sender_loop, name="SenderThread", daemon=True
        )
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._device_status = {"sensor": "OK", "camera": "OK", "microphone": "OK"}
        self._device_status_lock = threading.Lock()

    # ---------- lifecycle ----------
    def start(self) -> None:
        setup_logging()
        logger.info(
            f"엣지 서버 시작 (device={settings.device_id}, host={socket.gethostname()})"
        )
        logger.info(f"메인 서버: {settings.api_base_url}")
        self._leds.mark_idle()

        self._bus.subscribe(VEHICLE_DETECTED, self._on_vehicle_detected)
        self._bus.subscribe(VEHICLE_DEPARTED, self._on_vehicle_departed)
        self._bus.start()
        self._sensor.start()
        self._sender_thread.start()

        self._scheduler.add_job(
            self._send_heartbeat,
            "interval",
            seconds=settings.heartbeat_interval_sec,
            id="heartbeat",
            next_run_time=datetime.now(timezone.utc),
        )
        self._scheduler.add_job(
            cleanup_all_caches,
            "interval",
            minutes=10,
            id="cache_cleanup",
        )
        self._scheduler.start()

        # Signal handling
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass  # 메인 스레드가 아닐 때

        logger.info("초기화 완료 — 차량 감지 대기 중")

    def run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                self._stop.wait(timeout=1.0)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._stop.is_set() and not self._scheduler.running:
            return
        logger.info("종료 시퀀스 시작")
        self._stop.set()
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass
        self._sensor.stop()
        self._bus.stop()
        self._capture_pool.shutdown(wait=True, cancel_futures=False)
        self._http_pool.shutdown(wait=True, cancel_futures=False)
        try:
            self._camera.close()
        except Exception:
            pass
        try:
            self._api.close()
        except Exception:
            pass
        try:
            self._plate_detector.close()
        except Exception:
            pass
        try:
            self._leds.close()
        except Exception:
            pass
        logger.info("엣지 서버 정상 종료")

    # ---------- signal ----------
    def _on_signal(self, signum: int, _frame) -> None:
        logger.info(f"시그널 수신: {signum}")
        self._stop.set()

    # ---------- event handlers ----------
    def _on_vehicle_detected(self, event: Event) -> None:
        self._leds.mark_busy()
        # 이미지와 오디오를 동시(동기) 캡처 (FR-2.1.2)
        self._capture_pool.submit(self._capture_image_job, event)
        self._capture_pool.submit(self._capture_audio_job, event)

    def _on_vehicle_departed(self, _event: Event) -> None:
        self._leds.clear_busy()

    # ---------- capture jobs (CaptureThreads) ----------
    def _capture_image_job(self, event: Event) -> None:
        try:
            captured_at = event.timestamp
            jpeg, filename, _cache_path = self._camera.capture(event.event_id, captured_at)
            distance_cm = float(event.payload.get("distance_cm", 0.0))

            plates = self._run_plate_detection(jpeg, filename)

            job = TransmitJob(
                kind=IMAGE_JOB,
                event_id=event.event_id,
                endpoint="/drivethrough/vehicle-entry",
                form_fields={
                    "captured_at": captured_at.isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    ),
                    "distance_cm": f"{distance_cm:.2f}",
                },
                file_field="image",
                file_name=filename,
                file_bytes=jpeg,
                content_type="image/jpeg",
                plates=plates,
            )
            self._queue.put(job)
            self._set_device_status("camera", "OK")
        except Exception as exc:
            logger.exception(f"이미지 캡처 실패: {exc}")
            self._set_device_status("camera", "ERROR")
            self._leds.mark_error()

    def _run_plate_detection(self, jpeg: bytes, image_filename: str) -> list[PlatePayload]:
        if not getattr(self._plate_detector, "available", False):
            return []
        t0 = time.monotonic()
        try:
            detections = self._plate_detector.detect(jpeg)
        except Exception as exc:
            logger.warning(f"번호판 검출 실패 — 원본만 전송: {exc}")
            return []
        elapsed_ms = (time.monotonic() - t0) * 1000
        if not detections:
            logger.info(f"번호판 미검출 ({elapsed_ms:.0f}ms)")
            return []
        base = image_filename.rsplit(".", 1)[0]
        payloads = [
            PlatePayload(
                bbox_xyxy=det.bbox_xyxy,
                confidence=det.confidence,
                class_name=det.class_name,
                crop_jpeg=det.crop_jpeg,
                crop_filename=f"{base}_plate{idx}.jpg",
            )
            for idx, det in enumerate(detections)
        ]
        logger.info(
            f"번호판 {len(payloads)}건 검출 ({elapsed_ms:.0f}ms, "
            f"top_conf={max(p.confidence for p in payloads):.2f})"
        )
        return payloads

    def _capture_audio_job(self, event: Event) -> None:
        try:
            wav_bytes, filename, _cache_path, duration_sec = self._mic.record(event.event_id)
            recorded_at = event.timestamp
            job = TransmitJob(
                kind=AUDIO_JOB,
                event_id=event.event_id,
                endpoint="/drivethrough/voice-order",
                form_fields={
                    "recorded_at": recorded_at.isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    ),
                    "duration_ms": str(int(duration_sec * 1000)),
                    "sample_rate": str(settings.audio_sample_rate),
                },
                file_field="audio",
                file_name=filename,
                file_bytes=wav_bytes,
                content_type="audio/wav",
            )
            self._queue.put(job)
            self._set_device_status("microphone", "OK")
        except Exception as exc:
            logger.exception(f"오디오 캡처 실패: {exc}")
            self._set_device_status("microphone", "ERROR")
            self._leds.mark_error()

    # ---------- sender loop ----------
    def _sender_loop(self) -> None:
        logger.info(f"SenderThread 시작 (workers={settings.http_max_workers})")
        in_flight: list[Future] = []
        while not self._stop.is_set():
            # 끝난 future 회수
            in_flight = [f for f in in_flight if not f.done()]
            if len(in_flight) >= settings.http_max_workers:
                time.sleep(0.05)
                continue
            job = self._queue.get(timeout=0.3)
            if job is None:
                continue
            in_flight.append(self._http_pool.submit(self._dispatch_job, job))

    def _dispatch_job(self, job: TransmitJob) -> None:
        try:
            if job.kind == IMAGE_JOB:
                self._api.upload_vehicle_entry(
                    event_id=job.event_id,
                    captured_at=datetime.fromisoformat(
                        job.form_fields["captured_at"].replace("Z", "+00:00")
                    ),
                    distance_cm=float(job.form_fields["distance_cm"]),
                    image_bytes=job.file_bytes,
                    image_name=job.file_name,
                    plates=job.plates,
                )
            elif job.kind == AUDIO_JOB:
                self._api.upload_voice_order(
                    event_id=job.event_id,
                    recorded_at=datetime.fromisoformat(
                        job.form_fields["recorded_at"].replace("Z", "+00:00")
                    ),
                    duration_ms=int(job.form_fields["duration_ms"]),
                    audio_bytes=job.file_bytes,
                    audio_name=job.file_name,
                )
            else:
                logger.warning(f"unknown job kind: {job.kind}")
                return
            logger.info(f"전송 완료: {job.kind} event={job.event_id}")
            self._leds.clear_error()
        except Exception as exc:
            logger.error(
                f"전송 최종 실패: {job.kind} event={job.event_id} ({exc}) — 캐시 보존"
            )
            self._leds.mark_error()
        finally:
            self._queue.task_done()

    # ---------- heartbeat ----------
    def _send_heartbeat(self) -> None:
        try:
            self._api.heartbeat(
                metrics=_system_metrics(self._queue),
                device_status=self._current_device_status(),
            )
        except Exception as exc:
            logger.warning(f"heartbeat 전송 실패: {exc}")

    def _current_device_status(self) -> dict[str, str]:
        with self._device_status_lock:
            return dict(self._device_status)

    def _set_device_status(self, key: str, value: str) -> None:
        with self._device_status_lock:
            self._device_status[key] = value


def main() -> int:
    server = EdgeServer()
    try:
        server.start()
        server.run_forever()
        return 0
    except Exception:
        logger.exception("치명적 오류로 종료")
        server.shutdown()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
