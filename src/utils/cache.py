"""로컬 캐시 LRU 정리 (FR-2.2.4 / FR-3.2.3).

조건:
- 디렉터리 합계 용량이 ``cache_max_bytes`` 초과
- 또는 파일이 ``cache_max_age_hours`` 보다 오래됨

오래된 항목부터 삭제 (LRU 기반: mtime).
"""

from __future__ import annotations

import time
from pathlib import Path

from src.config import settings
from src.utils.logger import logger


def cleanup_cache_dir(directory: Path) -> None:
    if not directory.exists():
        return
    now = time.time()
    age_limit = settings.cache_max_age_hours * 3600
    files = []
    for path in directory.rglob("*"):
        if path.is_file():
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))

    # 1단계: 오래된 파일 삭제
    for mtime, _size, path in list(files):
        if now - mtime > age_limit:
            try:
                path.unlink(missing_ok=True)
                logger.debug(f"캐시 만료 삭제: {path.name}")
            except Exception as exc:
                logger.warning(f"캐시 삭제 실패: {path}: {exc}")
            files = [(m, s, p) for m, s, p in files if p != path]

    # 2단계: 용량 초과 시 오래된 순으로 삭제
    files.sort(key=lambda x: x[0])
    total = sum(s for _m, s, _p in files)
    while total > settings.cache_max_bytes and files:
        _m, size, path = files.pop(0)
        try:
            path.unlink(missing_ok=True)
            total -= size
            logger.debug(f"캐시 LRU 삭제: {path.name}")
        except Exception as exc:
            logger.warning(f"캐시 삭제 실패: {path}: {exc}")


def cleanup_all_caches() -> None:
    cleanup_cache_dir(settings.image_cache_dir)
    cleanup_cache_dir(settings.audio_cache_dir)
