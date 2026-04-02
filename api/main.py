"""FastAPI backend for PCB Inspection System.

Provides REST API for:
- Inspection execution (image upload → result)
- Feedback collection (operator corrections)
- Health monitoring and statistics
- Model management

Usage:
    uvicorn api.main:app --reload --port 8000

Or:
    python -m api.main
"""

from __future__ import annotations

import base64
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.schemas import (
    BulkFeedbackRequest,
    BulkFeedbackResponse,
    DetectionResult,
    HealthResponse,
    InspectResponse,
    StatsResponse,
)
from pcb_inspection.common.types import Severity
from pcb_inspection.data.feedback_store import FeedbackItem, FeedbackStore
from pcb_inspection.notify import AlertThresholds, check_and_notify

logger = logging.getLogger(__name__)

# ── Global State ──
feedback_store = FeedbackStore()
_inspection_log: list[dict] = []  # In-memory log (replace with DB later)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("PCB Inspection API starting...")
    yield
    logger.info("PCB Inspection API shutting down.")


app = FastAPI(
    title="PCB Inspection API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════
# Inspection Endpoints
# ═══════════════════════════════════════════

@app.post("/inspect/image", response_model=InspectResponse)
async def inspect_image(file: UploadFile = File(...)):
    """Run full inspection on an uploaded PCB image."""
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if image is None:
        raise HTTPException(400, "Invalid image file")

    board_id = f"board_{uuid.uuid4().hex[:8]}"

    # Run anomaly inspection
    result = _run_inspection(image, board_id)

    # Save raw
    feedback_store.save_raw(image, board_id, result)

    # Log
    _inspection_log.append(result)

    # Check health
    stats = _compute_stats()
    check_and_notify("api", stats)

    return InspectResponse(
        board_id=result["board_id"],
        overall=result["overall"],
        component_count=result["component_count"],
        ng_count=result["ng_count"],
        alignment_quality=result.get("alignment_quality", 1.0),
        timestamp=result["timestamp"],
    )


@app.get("/inspect/{board_id}/details", response_model=list[DetectionResult])
async def get_inspection_details(board_id: str):
    """Get detailed inspection results for a board."""
    for log in reversed(_inspection_log):
        if log.get("board_id") == board_id:
            return [
                DetectionResult(**r) for r in log.get("results", [])
            ]
    raise HTTPException(404, f"Board {board_id} not found")


# ═══════════════════════════════════════════
# Feedback Endpoints (PODO pattern)
# ═══════════════════════════════════════════

@app.post("/feedback/bulk", response_model=BulkFeedbackResponse)
async def submit_bulk_feedback(req: BulkFeedbackRequest):
    """Submit bulk feedback for a component (PODO-style relabeling)."""
    feedbacks = [
        FeedbackItem(
            feedback_type=fb.feedback_type,
            correct_label=fb.correct_label,
            comment=fb.comment,
            target_bbox=fb.target_bbox,
        )
        for fb in req.feedbacks
    ]

    # Find original detections from log
    original_detections = []
    for log in reversed(_inspection_log):
        if log.get("board_id") == req.board_id:
            for r in log.get("results", []):
                if r.get("component_id") == req.component_id:
                    original_detections = r.get("detections", [])
                    break
            break

    result = feedback_store.save_feedback(
        board_id=req.board_id,
        component_id=req.component_id,
        feedbacks=feedbacks,
        original_detections=original_detections,
    )

    return BulkFeedbackResponse(**result)


@app.post("/feedback/quick")
async def submit_quick_feedback(board_id: str, component_id: str, is_ok: bool):
    """1-click OK/NG feedback (simple mode)."""
    fb_type = "false_positive" if is_ok else "false_negative"
    feedbacks = [FeedbackItem(feedback_type=fb_type)]

    result = feedback_store.save_feedback(
        board_id=board_id,
        component_id=component_id,
        feedbacks=feedbacks,
    )
    return {"status": "saved", **result}


# ═══════════════════════════════════════════
# Health & Stats Endpoints
# ═══════════════════════════════════════════

@app.get("/health", response_model=HealthResponse)
async def get_health():
    """Get system health status with alerts."""
    stats = _compute_stats()
    health = check_and_notify("api", stats)
    return HealthResponse(**health)


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get inspection and feedback statistics."""
    stats = _compute_stats()
    fb_stats = feedback_store.get_stats()
    return StatsResponse(
        total_inspections=stats.get("total_inspections", 0),
        total_ng=stats.get("total_ng", 0),
        defect_rate=stats.get("defect_rate", 0),
        false_rejects=fb_stats.get("false_rejects", 0),
        escapes=fb_stats.get("escapes", 0),
        refined_images=fb_stats.get("refined_images", 0),
        retrain_ready=fb_stats.get("retrain_ready", False),
    )


@app.get("/stats/feedback")
async def get_feedback_stats():
    """Get detailed feedback statistics for MLOps."""
    return feedback_store.get_stats()


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════

def _run_inspection(image: np.ndarray, board_id: str) -> dict:
    """Run anomaly inspection on image."""
    # Try loading cached inspector
    inspector = _get_anomaly_inspector()

    timestamp = datetime.now(timezone.utc).isoformat()
    results = []

    if inspector and inspector.is_loaded:
        result = inspector.inspect(image, None, {
            "component_id": "full_image",
            "anomaly_threshold": 0.52,
            "warning_threshold": 0.4,
        })
        results.append({
            "component_id": "full_image",
            "inspection_type": "anomaly",
            "severity": result.severity.value,
            "score": result.score,
            "detail": result.detail,
        })

    ng_count = sum(1 for r in results if r["severity"] == "ng")
    overall = "ng" if ng_count > 0 else "ok"

    return {
        "board_id": board_id,
        "overall": overall,
        "component_count": 1,
        "ng_count": ng_count,
        "alignment_quality": 1.0,
        "timestamp": timestamp,
        "results": results,
    }


_cached_inspector = None


def _get_anomaly_inspector():
    """Get cached anomaly inspector."""
    global _cached_inspector
    if _cached_inspector is not None:
        return _cached_inspector

    ckpt = Path("data/models/transistor/patchcore/Patchcore/MVTecAD/transistor/v0/weights/lightning/model.ckpt")
    if not ckpt.exists():
        return None

    from pcb_inspection.inspection.anomaly import AnomalyInspector
    _cached_inspector = AnomalyInspector()
    _cached_inspector.load(str(ckpt), image_size=(256, 256))
    return _cached_inspector


def _compute_stats() -> dict:
    """Compute current inspection statistics."""
    total = len(_inspection_log)
    ng = sum(1 for log in _inspection_log if log.get("overall") == "ng")
    defect_rate = (ng / total * 100) if total > 0 else 0

    fb_stats = feedback_store.get_stats()
    false_reject_rate = (fb_stats["false_rejects"] / total * 100) if total > 0 else 0

    return {
        "total_inspections": total,
        "total_ng": ng,
        "defect_rate": defect_rate,
        "false_reject_rate": false_reject_rate,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
