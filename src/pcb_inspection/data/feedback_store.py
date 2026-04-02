"""Feedback storage and retraining data pipeline.

Adopted from PODO: structured data flow (raw → refined → needs_labeling).
Uses local filesystem instead of S3 (can swap to S3 later).

Directory structure:
    data/
    ├── raw/              # Original inspection images + results
    │   └── {YYYYMMDD}/
    ├── refined/          # Human-verified labels (ready for retraining)
    │   ├── images/
    │   └── labels/       # YOLO format .txt
    ├── needs_labeling/   # False negatives requiring manual labeling
    │   ├── images/
    │   └── metadata/
    └── feedback/         # Feedback logs (JSONL)
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DATA_ROOT = Path("data")


class FeedbackStore:
    """Manages inspection feedback and retraining data pipeline."""

    def __init__(self, data_root: Path | str = DATA_ROOT) -> None:
        self.root = Path(data_root)
        self.raw_dir = self.root / "raw"
        self.refined_dir = self.root / "refined"
        self.needs_labeling_dir = self.root / "needs_labeling"
        self.feedback_dir = self.root / "feedback"

        # Ensure directories exist
        for d in [
            self.raw_dir,
            self.refined_dir / "images",
            self.refined_dir / "labels",
            self.needs_labeling_dir / "images",
            self.needs_labeling_dir / "metadata",
            self.feedback_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Raw Image Storage ──

    def save_raw(
        self,
        image: np.ndarray,
        board_id: str,
        result: dict[str, Any],
    ) -> Path:
        """Save raw inspection image and result."""
        date_dir = self.raw_dir / datetime.now().strftime("%Y%m%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%H%M%S_%f")
        img_path = date_dir / f"{board_id}_{timestamp}.png"
        cv2.imwrite(str(img_path), image)

        # Save result metadata
        meta_path = img_path.with_suffix(".json")
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        return img_path

    # ── Feedback ──

    def save_feedback(
        self,
        board_id: str,
        component_id: str,
        feedbacks: list[FeedbackItem],
        image: np.ndarray | None = None,
        original_detections: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Process bulk feedback for a component (PODO pattern).

        Args:
            board_id: Board identifier.
            component_id: Component identifier.
            feedbacks: List of feedback items.
            image: Component ROI image (for refined dataset).
            original_detections: Original model detections.

        Returns:
            Summary dict with counts.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        original_detections = original_detections or []

        # Build final labels from feedback
        final_labels = []
        fn_count = 0

        for det in original_detections:
            fb = _find_feedback_for_detection(det, feedbacks)
            if fb is None:
                # No feedback → implicit true positive, keep
                final_labels.append(det)
            elif fb.feedback_type == "false_positive":
                # Remove this detection
                continue
            elif fb.feedback_type == "tp_wrong_class":
                # Correct the class
                corrected = dict(det)
                corrected["defect_type"] = fb.correct_label
                final_labels.append(corrected)

        # Add false negatives
        for fb in feedbacks:
            if fb.feedback_type == "false_negative":
                fn_count += 1
                final_labels.append({
                    "defect_type": fb.correct_label,
                    "bbox": fb.target_bbox,
                    "confidence": 1.0,
                })

        # Save to refined/ if image provided
        refined_path = None
        if image is not None and final_labels:
            refined_path = self._save_refined(
                board_id, component_id, image, final_labels
            )

        # Save false negatives to needs_labeling/
        if image is not None and fn_count > 0:
            self._save_needs_labeling(
                board_id, component_id, image, feedbacks, original_detections
            )

        # Log feedback
        log_entry = {
            "timestamp": timestamp,
            "board_id": board_id,
            "component_id": component_id,
            "feedbacks": [fb.to_dict() for fb in feedbacks],
            "final_label_count": len(final_labels),
            "fn_count": fn_count,
            "refined_path": str(refined_path) if refined_path else None,
        }

        log_file = self.feedback_dir / f"{board_id}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return {
            "final_labels": len(final_labels),
            "false_negatives": fn_count,
            "refined_path": str(refined_path) if refined_path else None,
        }

    def _save_refined(
        self,
        board_id: str,
        component_id: str,
        image: np.ndarray,
        labels: list[dict],
    ) -> Path:
        """Save verified image + YOLO format labels to refined/."""
        filename = f"{board_id}_{component_id}"

        img_path = self.refined_dir / "images" / f"{filename}.png"
        cv2.imwrite(str(img_path), image)

        # Convert to YOLO format
        h, w = image.shape[:2]
        label_path = self.refined_dir / "labels" / f"{filename}.txt"
        with label_path.open("w") as f:
            for label in labels:
                bbox = label.get("bbox", [0, 0, w, h])
                class_id = label.get("class_id", 0)
                # Normalize to YOLO format [x_center, y_center, w, h] (0-1)
                x_center = (bbox[0] + bbox[2]) / 2 / w
                y_center = (bbox[1] + bbox[3]) / 2 / h
                bw = (bbox[2] - bbox[0]) / w
                bh = (bbox[3] - bbox[1]) / h
                f.write(f"{class_id} {x_center:.6f} {y_center:.6f} {bw:.6f} {bh:.6f}\n")

        return img_path

    def _save_needs_labeling(
        self,
        board_id: str,
        component_id: str,
        image: np.ndarray,
        feedbacks: list[FeedbackItem],
        original_detections: list[dict],
    ) -> None:
        """Save false negatives for manual labeling."""
        filename = f"{board_id}_{component_id}"

        img_path = self.needs_labeling_dir / "images" / f"{filename}.png"
        cv2.imwrite(str(img_path), image)

        meta = {
            "board_id": board_id,
            "component_id": component_id,
            "original_detections": original_detections,
            "false_negative_comments": [
                fb.comment for fb in feedbacks
                if fb.feedback_type == "false_negative" and fb.comment
            ],
        }
        meta_path = self.needs_labeling_dir / "metadata" / f"{filename}.json"
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # ── Stats ──

    def get_stats(self) -> dict[str, Any]:
        """Get feedback and data pipeline statistics."""
        refined_images = len(list((self.refined_dir / "images").glob("*.png")))
        refined_labels = len(list((self.refined_dir / "labels").glob("*.txt")))
        needs_labeling = len(list((self.needs_labeling_dir / "images").glob("*.png")))

        total_feedback = 0
        false_rejects = 0
        escapes = 0

        for f in self.feedback_dir.glob("*.jsonl"):
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    fbs = entry.get("feedbacks", [])
                    for fb in fbs:
                        total_feedback += 1
                        if fb.get("feedback_type") == "false_positive":
                            false_rejects += 1
                        elif fb.get("feedback_type") == "false_negative":
                            escapes += 1

        return {
            "refined_images": refined_images,
            "refined_labels": refined_labels,
            "needs_labeling": needs_labeling,
            "total_feedback": total_feedback,
            "false_rejects": false_rejects,
            "escapes": escapes,
            "retrain_ready": refined_images >= 10,
        }

    def get_refined_dataset_path(self) -> Path:
        """Get path to refined dataset for retraining."""
        return self.refined_dir


class FeedbackItem:
    """Single feedback entry for a detection."""

    def __init__(
        self,
        feedback_type: str,  # "false_positive" | "tp_wrong_class" | "false_negative"
        correct_label: str | None = None,
        comment: str | None = None,
        target_bbox: list[int] | None = None,
    ):
        self.feedback_type = feedback_type
        self.correct_label = correct_label
        self.comment = comment
        self.target_bbox = target_bbox

    def to_dict(self) -> dict:
        return {
            "feedback_type": self.feedback_type,
            "correct_label": self.correct_label,
            "comment": self.comment,
            "target_bbox": self.target_bbox,
        }


def _find_feedback_for_detection(
    detection: dict, feedbacks: list[FeedbackItem], tolerance: int = 2
) -> FeedbackItem | None:
    """Match feedback to detection by bbox coordinates (PODO: 2px tolerance)."""
    det_bbox = detection.get("bbox")
    if det_bbox is None:
        return None

    for fb in feedbacks:
        if fb.target_bbox is None:
            continue
        if fb.feedback_type == "false_negative":
            continue  # FNs are new detections, not corrections

        if _bbox_equals(det_bbox, fb.target_bbox, tolerance):
            return fb

    return None


def _bbox_equals(bbox1: list[int], bbox2: list[int], tolerance: int = 2) -> bool:
    """Compare two bboxes with pixel tolerance (PODO pattern)."""
    if len(bbox1) != len(bbox2):
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(bbox1, bbox2))
