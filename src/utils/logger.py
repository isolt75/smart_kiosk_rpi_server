"""loguru 기반 로깅 초기화.

stdout(systemd가 captures)과 파일 로테이션을 동시에 출력한다.
ERROR 이상은 후속 hook(예: 메인 서버 로그 수집)에서 후처리 가능.
"""

from __future__ import annotations

import sys

from loguru import logger

from src.config import settings

_INITIALIZED = False


def setup_logging() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        backtrace=False,
        diagnose=False,
        enqueue=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "- <level>{message}</level>"
        ),
    )

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.log_dir / "edge-server.log",
            level=settings.log_level,
            rotation="50 MB",
            retention="14 days",
            compression="gz",
            enqueue=True,
            encoding="utf-8",
        )
        logger.add(
            settings.log_dir / "edge-server.error.log",
            level="ERROR",
            rotation="50 MB",
            retention="30 days",
            compression="gz",
            enqueue=True,
            encoding="utf-8",
        )
    except PermissionError:
        logger.warning(
            f"로그 디렉터리 {settings.log_dir} 쓰기 권한 없음 — stdout으로만 출력"
        )

    _INITIALIZED = True


__all__ = ["logger", "setup_logging"]
