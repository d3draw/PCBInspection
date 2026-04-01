"""PCB Inspection Operator UI — Streamlit Application.

Usage:
    streamlit run app/main.py

Features:
    - Board image display with NG overlay
    - ROI detail view with reference comparison
    - Anomaly heatmap visualization
    - 1-click feedback (OK/NG correction)
    - Inspection statistics dashboard
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project source to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import cv2
import numpy as np
import streamlit as st

from app.state import get_feedback_stats, load_feedback, save_feedback
from pcb_inspection.common.types import Severity

# ── Page Config ──
st.set_page_config(
    page_title="PCB Inspection",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

SEVERITY_COLORS = {
    "ok": "#22c55e",
    "warning": "#f59e0b",
    "ng": "#ef4444",
}


def main():
    st.title("PCB Inspection System")

    # Sidebar
    with st.sidebar:
        st.header("Navigation")
        page = st.radio("", ["Inspection", "Board View", "History", "Statistics"], label_visibility="collapsed")

    if page == "Inspection":
        inspection_page()
    elif page == "Board View":
        from app.board_view import board_inspection_page
        board_inspection_page()
    elif page == "History":
        history_page()
    elif page == "Statistics":
        statistics_page()


# ═══════════════════════════════════════════
# Inspection Page
# ═══════════════════════════════════════════
def inspection_page():
    # ── Image Source Selection ──
    with st.sidebar:
        st.header("Image Source")
        source = st.radio("Source", ["Sample (Transistor)", "Upload Image"], label_visibility="collapsed")

    if source == "Sample (Transistor)":
        _transistor_inspection()
    else:
        _upload_inspection()


def _transistor_inspection():
    """Run inspection on transistor sample dataset."""
    test_dir = Path("transistor/test")
    if not test_dir.exists():
        st.error("transistor/test 폴더가 없습니다.")
        return

    with st.sidebar:
        st.header("Sample Selection")
        categories = sorted([d.name for d in test_dir.iterdir() if d.is_dir()])
        category = st.selectbox("Category", categories)

        images = sorted((test_dir / category).glob("*.png"))
        image_names = [img.name for img in images]
        selected = st.selectbox("Image", image_names)
        image_path = test_dir / category / selected

    # Load image
    image = cv2.imread(str(image_path))
    if image is None:
        st.error(f"Failed to load: {image_path}")
        return

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # Run anomaly detection
    result = _run_anomaly_inference(image, image_path.stem)

    # ── Layout ──
    col_img, col_detail = st.columns([2, 1])

    with col_img:
        st.subheader("Inspection Image")

        # Draw result overlay
        overlay = _draw_result_overlay(image_rgb.copy(), result)
        st.image(overlay, use_container_width=True)

        # Verdict banner
        severity = result.get("severity", "ok")
        score = result.get("anomaly_score", 0)
        _verdict_banner(severity, score)

    with col_detail:
        st.subheader("Detail")

        # Reference comparison
        ref_path = Path("transistor/test/good/000.png")
        if ref_path.exists():
            ref_img = cv2.imread(str(ref_path))
            ref_rgb = cv2.cvtColor(ref_img, cv2.COLOR_BGR2RGB)

            tab_test, tab_ref, tab_diff = st.tabs(["Test", "Reference", "Difference"])
            with tab_test:
                st.image(image_rgb, caption=f"{category}/{selected}", use_container_width=True)
            with tab_ref:
                st.image(ref_rgb, caption="Reference (good/000)", use_container_width=True)
            with tab_diff:
                diff = _compute_diff_image(image_rgb, ref_rgb)
                st.image(diff, caption="Difference Map", use_container_width=True)

        # Score details
        st.markdown("---")
        st.metric("Anomaly Score", f"{score:.3f}")
        st.metric("Category", category)
        st.metric("Predicted", "ANOMALY" if severity != "ok" else "NORMAL")

        # ── Feedback ──
        st.markdown("---")
        st.subheader("Operator Feedback")

        col_ok, col_ng = st.columns(2)
        board_id = f"transistor_{image_path.stem}"

        with col_ok:
            if st.button("✅ OK (정상)", key="fb_ok", use_container_width=True):
                save_feedback(board_id, "transistor", severity, "ok")
                st.success("Saved: OK")

        with col_ng:
            if st.button("❌ NG (불량)", key="fb_ng", use_container_width=True, type="primary"):
                save_feedback(board_id, "transistor", severity, "ng")
                st.error("Saved: NG")

        comment = st.text_input("Comment (optional)", key="fb_comment")
        if comment and st.button("Save Comment", key="fb_save"):
            save_feedback(board_id, "transistor", severity, severity, comment=comment)
            st.info("Comment saved")


def _upload_inspection():
    """Run inspection on uploaded image."""
    uploaded = st.file_uploader("Upload PCB Image", type=["png", "jpg", "bmp"])
    if uploaded is None:
        st.info("이미지를 업로드하세요.")
        return

    file_bytes = np.frombuffer(uploaded.read(), dtype=np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    if image is None:
        st.error("Invalid image")
        return

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = _run_anomaly_inference(image, uploaded.name)

    col_img, col_detail = st.columns([2, 1])

    with col_img:
        st.subheader("Uploaded Image")
        overlay = _draw_result_overlay(image_rgb.copy(), result)
        st.image(overlay, use_container_width=True)

        severity = result.get("severity", "ok")
        score = result.get("anomaly_score", 0)
        _verdict_banner(severity, score)

    with col_detail:
        st.subheader("Detail")
        st.image(image_rgb, caption=uploaded.name, use_container_width=True)
        st.metric("Anomaly Score", f"{score:.3f}")
        st.metric("Predicted", "ANOMALY" if severity != "ok" else "NORMAL")


# ═══════════════════════════════════════════
# History Page
# ═══════════════════════════════════════════
def history_page():
    st.header("Feedback History")

    feedback_dir = Path("data/feedback")
    if not feedback_dir.exists() or not list(feedback_dir.glob("*.jsonl")):
        st.info("No feedback data yet. Run inspections and provide feedback first.")
        return

    boards = sorted([f.stem for f in feedback_dir.glob("*.jsonl")])
    selected_board = st.selectbox("Board", boards)

    entries = load_feedback(selected_board)
    if not entries:
        st.info("No feedback entries for this board.")
        return

    for i, entry in enumerate(entries):
        with st.container():
            cols = st.columns([2, 1, 1, 1, 2])
            cols[0].write(entry.get("timestamp", "")[:19])
            cols[1].write(f"Original: **{entry.get('original', '')}**")

            corrected = entry.get("corrected", "")
            color = SEVERITY_COLORS.get(corrected, "#666")
            cols[2].markdown(f"Corrected: <span style='color:{color};font-weight:bold'>{corrected.upper()}</span>", unsafe_allow_html=True)

            comment = entry.get("comment", "")
            if comment:
                cols[3].write(f"💬 {comment}")
            st.divider()


# ═══════════════════════════════════════════
# Statistics Page
# ═══════════════════════════════════════════
def statistics_page():
    st.header("Inspection Statistics")

    stats = get_feedback_stats()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Feedback", stats["total_feedback"])
    col2.metric("False Rejects (과검출)", stats["false_rejects"])
    col3.metric("Escapes (미검출)", stats["escapes"])

    if stats["total_feedback"] > 0:
        st.markdown("---")
        false_reject_rate = stats["false_rejects"] / stats["total_feedback"] * 100
        escape_rate = stats["escapes"] / stats["total_feedback"] * 100

        col_a, col_b = st.columns(2)
        col_a.metric("False Reject Rate", f"{false_reject_rate:.1f}%")
        col_b.metric("Escape Rate", f"{escape_rate:.1f}%")

        st.markdown("---")
        st.subheader("Feedback Distribution")
        import pandas as pd

        feedback_dir = Path("data/feedback")
        all_entries = []
        for f in feedback_dir.glob("*.jsonl"):
            entries = load_feedback(f.stem)
            all_entries.extend(entries)

        if all_entries:
            df = pd.DataFrame(all_entries)
            if "corrected" in df.columns:
                counts = df["corrected"].value_counts()
                st.bar_chart(counts)

            if "timestamp" in df.columns:
                df["date"] = pd.to_datetime(df["timestamp"]).dt.date
                daily = df.groupby("date").size()
                st.subheader("Daily Feedback Count")
                st.line_chart(daily)


# ═══════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════
@st.cache_resource
def _get_inspector():
    """Load anomaly inspector (cached across reruns)."""
    ckpt_path = Path("data/models/transistor/patchcore/Patchcore/MVTecAD/transistor/v0/weights/lightning/model.ckpt")
    if not ckpt_path.exists():
        return None

    from pcb_inspection.inspection.anomaly import AnomalyInspector
    inspector = AnomalyInspector()
    inspector.load(str(ckpt_path), image_size=(256, 256))
    return inspector


def _run_anomaly_inference(image: np.ndarray, component_id: str) -> dict:
    """Run anomaly inference and return results dict."""
    inspector = _get_inspector()
    if inspector is None or not inspector.is_loaded:
        return {"severity": "warning", "anomaly_score": 0.0, "detail": "Model not loaded"}

    result = inspector.inspect(image, None, {
        "component_id": component_id,
        "anomaly_threshold": 0.52,
        "warning_threshold": 0.4,
    })

    return {
        "severity": result.severity.value,
        "anomaly_score": result.metadata.get("anomaly_score", 0),
        "detail": result.detail,
        "pred_label": result.metadata.get("pred_label", False),
    }


def _draw_result_overlay(image_rgb: np.ndarray, result: dict) -> np.ndarray:
    """Draw inspection result overlay on image."""
    severity = result.get("severity", "ok")
    score = result.get("anomaly_score", 0)

    h, w = image_rgb.shape[:2]

    # Border color based on severity
    color_map = {"ok": (34, 197, 94), "warning": (245, 158, 11), "ng": (239, 68, 68)}
    color = color_map.get(severity, (128, 128, 128))
    border = 6

    # Draw border
    cv2.rectangle(image_rgb, (0, 0), (w - 1, h - 1), color, border)

    # Label
    label = f"{severity.upper()} ({score:.2f})"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = min(w, h) / 400
    thickness = max(1, int(font_scale * 2))

    (tw, th), _ = cv2.getTextSize(label, font, font_scale, thickness)
    cv2.rectangle(image_rgb, (border, border), (border + tw + 10, border + th + 14), color, -1)
    cv2.putText(image_rgb, label, (border + 5, border + th + 7), font, font_scale, (255, 255, 255), thickness)

    return image_rgb


def _compute_diff_image(img1: np.ndarray, img2: np.ndarray) -> np.ndarray:
    """Compute absolute difference and apply colormap."""
    # Ensure same size
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)

    diff = cv2.absdiff(gray1, gray2)
    # Enhance contrast
    diff = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX)
    # Apply heatmap
    heatmap = cv2.applyColorMap(diff, cv2.COLORMAP_JET)
    heatmap_rgb = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)

    return heatmap_rgb


def _verdict_banner(severity: str, score: float):
    """Display a colored verdict banner."""
    color = SEVERITY_COLORS.get(severity, "#666")
    icon = {"ok": "✅", "warning": "⚠️", "ng": "❌"}.get(severity, "❓")
    st.markdown(
        f"""
        <div style="
            background-color: {color};
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            text-align: center;
            font-size: 1.4em;
            font-weight: bold;
            margin: 8px 0;
        ">
            {icon} {severity.upper()} — Anomaly Score: {score:.3f}
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
