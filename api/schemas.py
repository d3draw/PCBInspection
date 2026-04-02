"""Pydantic models for API request/response (adopted from PODO)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Detection(BaseModel):
    defect_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[int]  # [x1, y1, x2, y2]
    class_id: int = 0


class InspectRequest(BaseModel):
    image_id: str
    image: str | None = None  # Base64 encoded
    session_id: str | None = None
    camera_id: str = "cam_1"


class InspectResponse(BaseModel):
    board_id: str
    overall: str  # ok, warning, ng
    component_count: int
    ng_count: int
    alignment_quality: float
    timestamp: str


class DetectionResult(BaseModel):
    component_id: str
    inspection_type: str
    severity: str
    score: float
    detail: str


class FeedbackItem(BaseModel):
    feedback_type: str  # false_positive, tp_wrong_class, false_negative
    correct_label: str | None = None
    comment: str | None = Field(None, max_length=500)
    target_bbox: list[int] | None = None


class BulkFeedbackRequest(BaseModel):
    board_id: str
    component_id: str
    feedbacks: list[FeedbackItem] = Field(min_length=1, max_length=100)
    created_by: str | None = None


class BulkFeedbackResponse(BaseModel):
    final_labels: int
    false_negatives: int
    refined_path: str | None


class HealthResponse(BaseModel):
    status: str  # healthy, warning, critical
    alerts: list[dict]
    timestamp: str


class StatsResponse(BaseModel):
    total_inspections: int
    total_ng: int
    defect_rate: float
    false_rejects: int
    escapes: int
    refined_images: int
    retrain_ready: bool
