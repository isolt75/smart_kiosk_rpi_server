"""HTTP 재시도 정책 (NFR-2.1.1).

tenacity 기반 지수 백오프 (1s → 2s → 4s → 8s → 16s, 최대 5회).
network/5xx에서만 재시도하고, 4xx는 즉시 실패한다.
"""

from __future__ import annotations

from typing import Callable, TypeVar

import requests
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.utils.logger import logger


T = TypeVar("T")


class RetriableError(Exception):
    """재시도 가능한 일시적 오류."""


def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, RetriableError)):
        return True
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status >= 500 or status == 429
    return False


def run_with_retry(func: Callable[[], T], *, context: str = "") -> T:
    """주어진 호출을 재시도 정책으로 감싸 실행한다."""
    retryer = Retrying(
        stop=stop_after_attempt(settings.retry_max_attempts),
        wait=wait_exponential(
            multiplier=settings.retry_initial_delay,
            max=settings.retry_max_delay,
        ),
        retry=retry_if_exception(_is_retriable),
        reraise=True,
    )
    try:
        for attempt in retryer:
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    logger.warning(
                        f"재시도 #{attempt.retry_state.attempt_number} ({context})"
                    )
                return func()
    except RetryError as exc:  # 사실상 도달 X (reraise=True) — 안전망
        raise exc.last_attempt.exception() from exc
    raise RuntimeError("unreachable")  # for type checker
