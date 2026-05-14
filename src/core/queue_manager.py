"""전송 대기 큐 (FR-4.2).

큐는 thread-safe ``queue.Queue``이며 maxsize 초과 시 가장 오래된
항목을 버리고 신규 항목을 넣는 **drop-oldest** 정책을 적용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from typing import Any

from src.utils.logger import logger


IMAGE_JOB = "IMAGE"
AUDIO_JOB = "AUDIO"


@dataclass
class PlatePayload:
    """검출된 번호판 1개의 전송용 데이터."""

    bbox_xyxy: tuple[int, int, int, int]
    confidence: float
    class_name: str
    crop_jpeg: bytes
    crop_filename: str


@dataclass
class TransmitJob:
    """API 전송 작업 단위.

    ``payload``는 multipart로 직렬화될 form 필드 + 파일을 담는다.
    ``plates``는 IMAGE_JOB에서만 사용되며, 엣지에서 검출된 번호판
    crop들을 메인 서버에 함께 전달한다(하이브리드: 엣지 YOLO → 메인 OCR).
    """

    kind: str  # IMAGE_JOB / AUDIO_JOB
    event_id: str
    endpoint: str
    form_fields: dict[str, str]
    file_field: str
    file_name: str
    file_bytes: bytes
    content_type: str
    plates: list[PlatePayload] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attempts: int = 0


class TransmitQueue:
    def __init__(self, maxsize: int) -> None:
        self._q: "Queue[TransmitJob]" = Queue(maxsize=maxsize)

    def put(self, job: TransmitJob) -> None:
        while True:
            try:
                self._q.put_nowait(job)
                return
            except Full:
                # drop-oldest 정책 (FR-4.2.3)
                try:
                    dropped = self._q.get_nowait()
                    logger.warning(
                        f"전송 큐 가득 참 — 가장 오래된 작업 드롭: "
                        f"{dropped.kind} event={dropped.event_id}"
                    )
                except Empty:
                    # 경합으로 비게 된 경우 그냥 다음 루프에서 put 재시도
                    continue

    def get(self, timeout: float = 0.5) -> TransmitJob | None:
        try:
            return self._q.get(timeout=timeout)
        except Empty:
            return None

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()


__all__ = [
    "TransmitJob",
    "TransmitQueue",
    "PlatePayload",
    "IMAGE_JOB",
    "AUDIO_JOB",
]
