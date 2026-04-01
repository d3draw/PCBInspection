"""Board-level inspection view — shows full PCB with ROI overlays.

Runs alignment + ROI + inspection pipeline on synthetic PCB data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import cv2
import numpy as np
import streamlit as st

from pcb_inspection.common.image_utils import crop_roi
from pcb_inspection.common.types import ComponentROI, InspectionResult, InspectionType, Severity


SEVERITY_COLORS_BGR = {
    Severity.OK: (0, 200, 0),
    Severity.WARNING: (0, 200, 255),
    Severity.NG: (0, 0, 255),
}


def board_inspection_page():
    """Board-level inspection UI using synthetic PCB."""
    st.header("Board Inspection (Synthetic PCB)")

    # Generate or load synthetic data
    data = _get_synthetic_data()
    if data is None:
        st.error("Failed to generate synthetic PCB data")
        return

    ref_img = data["ref_img"]
    test_img = data["test_img"]
    rois = data["rois"]
    results = data["results"]
    aligned_img = data["aligned_img"]

    # ── Board Overview ──
    col_board, col_detail = st.columns([3, 2])

    with col_board:
        st.subheader("Board Overview")

        # Draw all ROIs with severity colors
        overlay = _draw_board_overlay(aligned_img, rois, results)
        overlay_rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
        st.image(overlay_rgb, use_container_width=True)

        # Summary stats
        ng_count = sum(1 for r in results if r.severity == Severity.NG)
        warn_count = sum(1 for r in results if r.severity == Severity.WARNING)
        ok_count = sum(1 for r in results if r.severity == Severity.OK)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total ROIs", len(rois))
        c2.metric("OK", ok_count)
        c3.metric("WARNING", warn_count)
        c4.metric("NG", ng_count)

    with col_detail:
        st.subheader("Component Detail")

        # Component selector
        component_ids = [roi.component_id for roi in rois]
        selected_id = st.selectbox("Select Component", component_ids)

        if selected_id:
            _show_component_detail(selected_id, rois, results, aligned_img, ref_img)


def _show_component_detail(
    component_id: str,
    rois: list[ComponentROI],
    results: list[InspectionResult],
    aligned_img: np.ndarray,
    ref_img: np.ndarray,
):
    """Show detailed view for a single component."""
    roi = next((r for r in rois if r.component_id == component_id), None)
    if roi is None:
        return

    comp_results = [r for r in results if r.component_id == component_id]

    # Crop images
    test_crop = crop_roi(aligned_img, roi.bbox, padding=10)
    ref_crop = crop_roi(ref_img, roi.bbox, padding=10)

    if test_crop.size == 0:
        st.warning("ROI outside image bounds")
        return

    # Show crops side by side
    tab_test, tab_ref, tab_diff = st.tabs(["Test", "Reference", "Diff"])

    test_rgb = cv2.cvtColor(test_crop, cv2.COLOR_BGR2RGB)
    ref_rgb = cv2.cvtColor(ref_crop, cv2.COLOR_BGR2RGB) if ref_crop.size > 0 else None

    with tab_test:
        st.image(test_rgb, caption=f"{component_id} (Test)", use_container_width=True)
    with tab_ref:
        if ref_rgb is not None:
            st.image(ref_rgb, caption=f"{component_id} (Reference)", use_container_width=True)
    with tab_diff:
        if ref_rgb is not None and test_rgb.shape == ref_rgb.shape:
            diff = cv2.absdiff(test_rgb, ref_rgb)
            diff_enhanced = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX)
            st.image(diff_enhanced, caption="Difference", use_container_width=True)

    # Inspection results table
    st.markdown("**Inspection Results:**")
    for r in comp_results:
        color = {"ok": "🟢", "warning": "🟡", "ng": "🔴"}.get(r.severity.value, "⚪")
        st.markdown(f"{color} **{r.inspection_type.value}**: {r.severity.value.upper()} (score={r.score:.3f}) — {r.detail}")

    # Component info
    st.markdown("---")
    st.markdown(f"**Type:** {roi.component_type}")
    st.markdown(f"**Position:** ({roi.bbox[0]}, {roi.bbox[1]})")
    st.markdown(f"**Size:** {roi.bbox[2]}x{roi.bbox[3]}px")
    st.markdown(f"**Inspections:** {', '.join(t.value for t in roi.inspection_types)}")


def _draw_board_overlay(
    image: np.ndarray,
    rois: list[ComponentROI],
    results: list[InspectionResult],
) -> np.ndarray:
    """Draw inspection results overlaid on board image."""
    vis = image.copy()

    # Group results by component
    comp_results: dict[str, list[InspectionResult]] = {}
    for r in results:
        comp_results.setdefault(r.component_id, []).append(r)

    for roi in rois:
        x, y, w, h = roi.bbox
        cr = comp_results.get(roi.component_id, [])

        # Worst severity
        worst = Severity.OK
        for r in cr:
            if r.severity == Severity.NG:
                worst = Severity.NG
                break
            elif r.severity == Severity.WARNING:
                worst = Severity.WARNING

        color = SEVERITY_COLORS_BGR[worst]
        thickness = 3 if worst == Severity.NG else 2
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, thickness)

        # Label
        label = f"{roi.component_id}"
        cv2.putText(vis, label, (x, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

    return vis


@st.cache_data
def _get_synthetic_data():
    """Generate and cache synthetic PCB inspection data."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from generate_synthetic_pcb import generate_pcb_image, generate_test_image, save_cpl

        ref_img, components, fiducials = generate_pcb_image(seed=42)
        test_img = generate_test_image(ref_img, offset_x=5, offset_y=-3, rotation_deg=0.3, noise_std=2.0)

        # Alignment
        from pcb_inspection.alignment.fiducial import FiducialConfig
        from pcb_inspection.alignment.registration import align_board

        fid_x, fid_y = int(fiducials[0][0]), int(fiducials[0][1])
        fid_template = ref_img[fid_y - 30:fid_y + 30, fid_x - 30:fid_x + 30].copy()

        alignment = align_board(
            test_img, fiducials,
            FiducialConfig(method="template", match_threshold=0.6, expected_count=2, pixels_per_mm=50.0),
            fid_template,
            output_size=(ref_img.shape[1], ref_img.shape[0]),
        )

        # ROI generation
        import tempfile, csv
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Designator", "Package", "X(mm)", "Y(mm)", "Rotation", "Layer", "Value"])
            for c in components:
                writer.writerow([c["designator"], c["package"], c["x_mm"], c["y_mm"], c["rotation"], c["layer"], c["value"]])
            cpl_path = f.name

        from pcb_inspection.roi.cad_parser import parse_cpl
        from pcb_inspection.roi.roi_generator import generate_rois

        cad_comps = parse_cpl(cpl_path)
        rois = generate_rois(cad_comps, 50.0, (alignment.aligned_image.shape[1], alignment.aligned_image.shape[0]))

        # Run inspections
        from pcb_inspection.inspection.reference import ReferenceInspector
        from pcb_inspection.inspection.rule_based import RuleBasedInspector
        from pcb_inspection.inspection.blob import BlobInspector

        ref_insp = ReferenceInspector()
        rule_insp = RuleBasedInspector()
        blob_insp = BlobInspector()

        all_results = []
        for roi in rois:
            roi_image = crop_roi(alignment.aligned_image, roi.bbox, padding=5)
            ref_roi = crop_roi(ref_img, roi.bbox, padding=5)
            if roi_image.size == 0:
                continue

            for itype in roi.inspection_types:
                config = {"component_id": roi.component_id, "similarity_threshold": 0.6, "max_offset_px": 15.0}
                if itype == InspectionType.REFERENCE:
                    all_results.append(ref_insp.inspect(roi_image, ref_roi, config))
                elif itype == InspectionType.RULE_BASED:
                    all_results.append(rule_insp.inspect(roi_image, ref_roi, config))
                elif itype == InspectionType.BLOB:
                    all_results.append(blob_insp.inspect(roi_image, ref_roi, config))

        return {
            "ref_img": ref_img,
            "test_img": test_img,
            "aligned_img": alignment.aligned_image,
            "rois": rois,
            "results": all_results,
        }

    except Exception as e:
        st.error(f"Error: {e}")
        return None
