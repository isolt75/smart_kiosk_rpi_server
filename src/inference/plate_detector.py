"""번호판 영역 검출 (YOLOv8).

엣지 측에서는 **번호판 영역 검출(detection)** 까지만 수행한다. OCR(텍스트
추출)은 메인 서버 책임. 모델 파일이 없거나 ultralytics 미설치 환경에서는
자동으로 빈 결과를 반환하여 파이프라인이 멈추지 않는다 (FR-2 + 하이브리드
방침).

지원 모델:
- Ultralytics YOLOv8 ``.pt`` 또는 ``.onnx``
- 클래스명에 "plate"가 포함되거나 단일 클래스 모델이면 그 클래스를 사용
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from src.config import settings
from src.utils.logger import logger


@dataclass
class PlateDetection:
    bbox_xyxy: tuple[int, int, int, int]  # (x1, y1, x2, y2) pixel coords
    confidence: float
    class_name: str
    crop_jpeg: bytes  # crop된 번호판 이미지 JPEG


class _NullDetector:
    """모델 미존재/import 실패 시 폴백."""

    available = False

    def detect(self, _jpeg: bytes) -> list[PlateDetection]:
        return []

    def close(self) -> None: ...


class _YoloDetector:
    available = True

    def __init__(self, model_path: Path) -> None:
        from ultralytics import YOLO  # type: ignore

        self._model = YOLO(str(model_path))
        names = getattr(self._model, "names", {}) or {}
        if isinstance(names, dict):
            self._class_names = names
        else:  # list 형태
            self._class_names = {i: n for i, n in enumerate(names)}
        self._target_indices = self._resolve_target_classes()
        self._lock = threading.Lock()
        logger.info(
            f"YOLO 번호판 검출기 로드: {model_path.name} "
            f"(classes={self._class_names}, target={self._target_indices})"
        )

    def _resolve_target_classes(self) -> Optional[list[int]]:
        """클래스명에 'plate'/'lp'/'license'를 포함하는 인덱스만 사용.

        단일 클래스 모델이거나 일치가 없으면 None(전체 클래스 허용).
        """
        if len(self._class_names) <= 1:
            return None
        keywords = ("plate", "lp", "license", "번호")
        matched = [
            idx
            for idx, name in self._class_names.items()
            if any(kw in str(name).lower() for kw in keywords)
        ]
        return matched or None

    def detect(self, jpeg_bytes: bytes) -> list[PlateDetection]:
        with self._lock:
            img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
            results = self._model.predict(
                img,
                imgsz=settings.plate_img_size,
                conf=settings.plate_conf_threshold,
                iou=settings.plate_iou_threshold,
                max_det=settings.plate_max_detections,
                classes=self._target_indices,
                verbose=False,
            )

        out: list[PlateDetection] = []
        if not results:
            return out
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return out

        boxes = r.boxes.xyxy.cpu().tolist()
        confs = r.boxes.conf.cpu().tolist()
        clses = r.boxes.cls.cpu().tolist()
        for (x1, y1, x2, y2), conf, cls_idx in zip(boxes, confs, clses):
            x1i, y1i, x2i, y2i = (
                max(0, int(x1)),
                max(0, int(y1)),
                min(img.width, int(x2)),
                min(img.height, int(y2)),
            )
            if x2i <= x1i or y2i <= y1i:
                continue
            crop = img.crop((x1i, y1i, x2i, y2i))
            buf = io.BytesIO()
            crop.save(buf, format="JPEG", quality=settings.plate_crop_jpeg_quality)
            out.append(
                PlateDetection(
                    bbox_xyxy=(x1i, y1i, x2i, y2i),
                    confidence=float(conf),
                    class_name=str(self._class_names.get(int(cls_idx), "plate")),
                    crop_jpeg=buf.getvalue(),
                )
            )
        return out

    def close(self) -> None:
        try:
            del self._model
        except Exception:
            pass


def make_plate_detector():
    path = Path(settings.plate_model_path) if settings.plate_model_path else None
    if not path or not path.exists():
        logger.info(
            f"번호판 모델 경로 미존재({path}) — 검출 비활성화, 원본 이미지만 전송"
        )
        return _NullDetector()
    try:
        return _YoloDetector(path)
    except Exception as exc:
        logger.error(f"YOLO 로드 실패 — null 검출기 사용: {exc}")
        return _NullDetector()


__all__ = ["PlateDetection", "make_plate_detector"]
