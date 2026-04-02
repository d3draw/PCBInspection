"""YOLO-based defect detection inspector.

Wraps Ultralytics YOLO for supervised defect detection.
Used when labeled defect data is available (Phase 2+).

Supports: .pt, .onnx, .engine (TensorRT)
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pcb_inspection.common.types import InspectionResult, InspectionType, Severity

logger = logging.getLogger(__name__)


class YOLOInspector:
    """YOLO-based defect detection with model hot-swap support.

    Pattern adopted from PODO: thread-safe model reload via lock.
    """

    def __init__(self) -> None:
        self._model = None
        self._model_lock = threading.Lock()
        self._model_path: str | None = None
        self._class_names: dict[int, str] = {}
        self._image_size: int = 640

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self, model_path: str | None = None, image_size: int = 640) -> None:
        """Load a YOLO model (.pt, .onnx, or .engine).

        Args:
            model_path: Path to model file.
            image_size: Inference image size.
        """
        if model_path is None:
            logger.warning("No model path provided for YOLOInspector")
            return

        path = Path(model_path)
        if not path.exists():
            logger.error("Model path does not exist: %s", model_path)
            return

        self._image_size = image_size

        try:
            from ultralytics import YOLO

            # TensorRT engine auto-detection (PODO pattern)
            engine_path = path.with_suffix(".engine")
            if path.suffix == ".pt" and engine_path.exists():
                logger.info("Found TensorRT engine, using: %s", engine_path)
                self._model = YOLO(str(engine_path))
            else:
                self._model = YOLO(str(path))

            self._model_path = str(path)

            # Extract class names
            if hasattr(self._model, "names"):
                self._class_names = dict(self._model.names)

            logger.info("Loaded YOLO model from %s (%d classes)", model_path, len(self._class_names))

        except ImportError:
            logger.error("ultralytics not installed. Run: pip install ultralytics")
        except Exception:
            logger.exception("Failed to load YOLO model from %s", model_path)

    def reload(self, model_path: str) -> None:
        """Hot-swap model with thread safety (PODO pattern)."""
        try:
            from ultralytics import YOLO

            new_model = YOLO(model_path)

            with self._model_lock:
                old_model = self._model
                self._model = new_model
                self._model_path = model_path
                if hasattr(new_model, "names"):
                    self._class_names = dict(new_model.names)
                del old_model

            logger.info("Model hot-swapped to %s", model_path)
        except Exception:
            logger.exception("Model reload failed: %s", model_path)

    def inspect(
        self,
        roi_image: np.ndarray,
        reference: np.ndarray | None,
        config: dict[str, Any],
    ) -> InspectionResult:
        """Run YOLO detection on a ROI image.

        Config keys:
            component_id: str
            confidence_threshold: float (default 0.5)
            max_defects: int — more than this = NG (default 0)
        """
        component_id = config.get("component_id", "unknown")
        conf_threshold = config.get("confidence_threshold", 0.5)
        max_defects = config.get("max_defects", 0)

        if not self.is_loaded:
            return InspectionResult(
                inspection_type=InspectionType.REFERENCE,
                component_id=component_id,
                severity=Severity.WARNING,
                score=0.5,
                detail="YOLO model not loaded",
            )

        try:
            with self._model_lock:
                results = self._model.predict(
                    roi_image,
                    imgsz=self._image_size,
                    conf=conf_threshold,
                    verbose=False,
                )

            detections = self._parse_results(results)
            defect_count = len(detections)

            if defect_count > max_defects:
                severity = Severity.NG
            elif defect_count > 0:
                severity = Severity.WARNING
            else:
                severity = Severity.OK

            score = max(0.0, 1.0 - defect_count / max(1, max_defects + 3))
            max_conf = max((d["confidence"] for d in detections), default=0.0)

            return InspectionResult(
                inspection_type=InspectionType.REFERENCE,
                component_id=component_id,
                severity=severity,
                score=score,
                detail=f"{defect_count} defect(s) detected" + (
                    f", top: {detections[0]['defect_type']} ({detections[0]['confidence']:.2f})"
                    if detections else ""
                ),
                metadata={
                    "defect_count": defect_count,
                    "detections": detections,
                    "max_confidence": max_conf,
                },
            )

        except Exception:
            logger.exception("YOLO inference failed for %s", component_id)
            return InspectionResult(
                inspection_type=InspectionType.REFERENCE,
                component_id=component_id,
                severity=Severity.NG,
                score=0.0,
                detail="YOLO inference error — fail-safe NG",
            )

    def detect_full_image(
        self,
        image: np.ndarray,
        conf_threshold: float = 0.5,
    ) -> list[dict]:
        """Run detection on a full board image (not ROI-based).

        Returns list of detections with bbox, class, confidence.
        """
        if not self.is_loaded:
            return []

        with self._model_lock:
            results = self._model.predict(
                image,
                imgsz=self._image_size,
                conf=conf_threshold,
                verbose=False,
            )

        return self._parse_results(results)

    def _parse_results(self, results) -> list[dict]:
        """Parse YOLO results into standardized detection list."""
        detections = []

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                detections.append({
                    "defect_type": self._class_names.get(cls_id, f"class_{cls_id}"),
                    "confidence": round(float(box.conf[0]), 4),
                    "bbox": [int(float(x)) for x in box.xyxy[0].tolist()],
                    "class_id": cls_id,
                })

        return sorted(detections, key=lambda d: d["confidence"], reverse=True)


def export_tensorrt(
    model_path: str,
    half: bool = True,
    dynamic: bool = True,
    workspace_mb: int = 1024,
) -> str | None:
    """Export YOLO model to TensorRT engine (PODO pattern).

    Args:
        model_path: Path to .pt model.
        half: Use FP16.
        dynamic: Dynamic input shapes.
        workspace_mb: TensorRT workspace limit (Jetson: 1024).

    Returns:
        Path to exported .engine file, or None on failure.
    """
    try:
        from ultralytics import YOLO

        model = YOLO(model_path)
        engine_path = model.export(
            format="engine",
            dynamic=dynamic,
            half=half,
            device=0,
            workspace=workspace_mb / 1024,  # GB
        )
        logger.info("Exported TensorRT engine: %s", engine_path)
        return str(engine_path)
    except Exception:
        logger.exception("TensorRT export failed for %s", model_path)
        return None
