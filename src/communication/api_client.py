"""메인 서버 REST 클라이언트 (FR-4 + API-1/2/3).

- HTTPS + JWT/API Key
- multipart/form-data 업로드 (이미지/오디오)
- JSON heartbeat
- 지수 백오프 (5회) — 실패 시 RetriableError 누적
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

import requests

from src.communication.retry import RetriableError, run_with_retry
from src.config import settings
from src.core.queue_manager import PlatePayload
from src.utils.logger import logger


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class APIClient:
    """thread-safe하지 않으므로 호출자에서 단일 스레드 또는 lock 보호."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Device-ID": settings.device_id,
                **settings.auth_header,
            }
        )
        self._timeout = (settings.http_connect_timeout, settings.http_read_timeout)
        self._base = settings.api_base_url.rstrip("/")
        self._verify = settings.verify_option()

    # ---------- 공용 ----------
    def _url(self, path: str) -> str:
        return f"{self._base}/{path.lstrip('/')}"

    def _check(self, response: requests.Response) -> dict[str, Any]:
        if response.status_code >= 500 or response.status_code == 429:
            raise RetriableError(f"server status={response.status_code}")
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error(
                f"HTTP {response.status_code} {response.url}: {response.text[:300]}"
            )
            raise
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # ---------- API-1 ----------
    def upload_vehicle_entry(
        self,
        *,
        event_id: str,
        captured_at: datetime,
        distance_cm: float,
        image_bytes: bytes,
        image_name: str,
        plates: Sequence[PlatePayload] = (),
    ) -> dict[str, Any]:
        """차량 진입 이미지 업로드.

        ``plates``가 비어있지 않으면 엣지에서 YOLO로 검출된 번호판 정보가
        함께 전송된다 (API v1.1 확장):
          - 추가 form 필드 ``plates_meta``: JSON 배열 (bbox/conf/class)
          - 추가 파일 ``plate_image_{i}``: 각 번호판의 crop JPEG
        메인 서버는 이 crop들에 OCR을 수행해 텍스트를 추출한다.
        """

        def _do() -> dict[str, Any]:
            self._session.headers["X-Timestamp"] = _iso_now()
            data = {
                "event_id": event_id,
                "device_id": settings.device_id,
                "captured_at": captured_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "distance_cm": f"{distance_cm:.2f}",
            }
            files: list[tuple[str, tuple[str, bytes, str]]] = [
                ("image", (image_name, image_bytes, "image/jpeg"))
            ]
            if plates:
                data["plates_meta"] = json.dumps(
                    [
                        {
                            "index": i,
                            "bbox_xyxy": list(p.bbox_xyxy),
                            "confidence": round(p.confidence, 4),
                            "class": p.class_name,
                            "crop_filename": p.crop_filename,
                        }
                        for i, p in enumerate(plates)
                    ],
                    ensure_ascii=False,
                )
                for i, plate in enumerate(plates):
                    files.append(
                        (
                            f"plate_image_{i}",
                            (plate.crop_filename, plate.crop_jpeg, "image/jpeg"),
                        )
                    )
            response = self._session.post(
                self._url("/drivethrough/vehicle-entry"),
                data=data,
                files=files,
                timeout=self._timeout,
                verify=self._verify,
            )
            return self._check(response)

        return run_with_retry(_do, context=f"vehicle-entry event={event_id}")

    # ---------- API-2 ----------
    def upload_voice_order(
        self,
        *,
        event_id: str,
        recorded_at: datetime,
        duration_ms: int,
        audio_bytes: bytes,
        audio_name: str,
    ) -> dict[str, Any]:
        def _do() -> dict[str, Any]:
            self._session.headers["X-Timestamp"] = _iso_now()
            response = self._session.post(
                self._url("/drivethrough/voice-order"),
                data={
                    "event_id": event_id,
                    "device_id": settings.device_id,
                    "recorded_at": recorded_at.isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    ),
                    "duration_ms": str(int(duration_ms)),
                    "sample_rate": str(settings.audio_sample_rate),
                },
                files={"audio": (audio_name, audio_bytes, "audio/wav")},
                timeout=self._timeout,
                verify=self._verify,
            )
            return self._check(response)

        return run_with_retry(_do, context=f"voice-order event={event_id}")

    # ---------- API-3 ----------
    def heartbeat(self, metrics: dict[str, Any], device_status: dict[str, str]) -> dict[str, Any]:
        body = {
            "device_id": settings.device_id,
            "timestamp": _iso_now(),
            "status": "HEALTHY" if all(v == "OK" for v in device_status.values()) else "DEGRADED",
            "metrics": metrics,
            "device_status": device_status,
        }

        def _do() -> dict[str, Any]:
            response = self._session.post(
                self._url("/edge/heartbeat"),
                json=body,
                timeout=self._timeout,
                verify=self._verify,
            )
            return self._check(response)

        return run_with_retry(_do, context="heartbeat")

    def close(self) -> None:
        self._session.close()
