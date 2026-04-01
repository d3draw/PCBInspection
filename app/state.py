"""Session state management for the inspection UI."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FEEDBACK_DIR = Path("data/feedback")


def save_feedback(
    board_id: str,
    component_id: str,
    original_severity: str,
    corrected_severity: str,
    comment: str = "",
    image_path: str = "",
) -> None:
    """Save operator feedback for a single component judgment."""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "board_id": board_id,
        "component_id": component_id,
        "original": original_severity,
        "corrected": corrected_severity,
        "comment": comment,
        "image_path": image_path,
    }

    # Append to JSONL file
    feedback_file = FEEDBACK_DIR / f"{board_id}.jsonl"
    with feedback_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("Feedback saved: %s/%s: %s -> %s", board_id, component_id, original_severity, corrected_severity)


def load_feedback(board_id: str) -> list[dict]:
    """Load all feedback entries for a board."""
    feedback_file = FEEDBACK_DIR / f"{board_id}.jsonl"
    if not feedback_file.exists():
        return []

    entries = []
    with feedback_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def get_feedback_stats() -> dict[str, Any]:
    """Get aggregate feedback statistics."""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    false_reject = 0  # was NG, corrected to OK
    escape = 0  # was OK, corrected to NG

    for f in FEEDBACK_DIR.glob("*.jsonl"):
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                total += 1
                if entry["original"] == "ng" and entry["corrected"] == "ok":
                    false_reject += 1
                elif entry["original"] == "ok" and entry["corrected"] == "ng":
                    escape += 1

    return {
        "total_feedback": total,
        "false_rejects": false_reject,
        "escapes": escape,
    }
