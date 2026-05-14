"""스레드 안전 이벤트 버스.

NFR-1.2.3에 따라 스레드 간 통신은 ``queue.Queue``로만 한다. 단순한
pub/sub로, subscribe한 핸들러들은 dispatcher 스레드에서 호출된다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Callable, Optional
from uuid import uuid4

from src.utils.logger import logger


VEHICLE_DETECTED = "VEHICLE_DETECTED"
VEHICLE_DEPARTED = "VEHICLE_DEPARTED"


@dataclass
class Event:
    type: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict = field(default_factory=dict)


class EventBus:
    def __init__(self, maxsize: int = 64) -> None:
        self._queue: "Queue[Event]" = Queue(maxsize=maxsize)
        self._subscribers: dict[str, list[Callable[[Event], None]]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def publish(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except Exception:
            logger.warning(f"event bus 가득 참 — 이벤트 드롭: {event.type}")

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._dispatch_loop, name="EventBus", daemon=True
        )
        self._thread.start()
        logger.info("EventBus 시작")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("EventBus 종료")

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except Empty:
                continue
            with self._lock:
                handlers = list(self._subscribers.get(event.type, ()))
            for handler in handlers:
                try:
                    handler(event)
                except Exception as exc:
                    logger.exception(f"이벤트 핸들러 오류: {handler.__name__}: {exc}")
