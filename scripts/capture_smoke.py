"""Smoke capture sweep: walk exposure (and optional pixel format) on the real
camera, save each frame, and report per-frame stats so optical setup problems
(saturation, underexposure, vignetting, focus) surface in one pass.

Not a labeled-dataset producer — that comes after lighting/optics is locked
and inspection taxonomy is approved.

Usage:
    python scripts/capture_smoke.py
    python scripts/capture_smoke.py --exposures 500,1000,2000,5000,10000
    python scripts/capture_smoke.py --formats Mono8,Mono10 --width 2048 --height 2048
    python scripts/capture_smoke.py --out data/captures/smoke_2026-04-30
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pcb_inspection.camera import CameraConfig, create_camera

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "captures"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Optical smoke capture sweep")
    p.add_argument(
        "--exposures",
        default="500,1000,2000,5000,10000,20000",
        help="Comma-separated exposure values in microseconds.",
    )
    p.add_argument(
        "--formats",
        default="Mono8",
        help="Comma-separated pixel formats (e.g. Mono8,Mono10,Mono12).",
    )
    p.add_argument("--width", type=int, default=None, help="ROI width (default: sensor max)")
    p.add_argument("--height", type=int, default=None, help="ROI height (default: sensor max)")
    p.add_argument("--gain", type=float, default=0.0, help="Gain in dB (if supported).")
    p.add_argument("--device", type=int, default=0, help="Camera device index.")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output directory (default: {DEFAULT_OUT}/smoke_<timestamp>).",
    )
    return p.parse_args()


def stats(img: np.ndarray) -> dict[str, float]:
    """Per-frame quality indicators for the optical sweep."""
    flat = img.reshape(-1)
    max_val = float(np.iinfo(img.dtype).max)
    sat_high = float(np.mean(flat >= max_val * 0.99) * 100.0)
    sat_low = float(np.mean(flat <= max_val * 0.01) * 100.0)

    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if gray.dtype != np.uint8:
        gray8 = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    else:
        gray8 = gray
    laplacian_var = float(cv2.Laplacian(gray8, cv2.CV_64F).var())

    return {
        "shape": list(img.shape),
        "dtype": str(img.dtype),
        "min": int(flat.min()),
        "max": int(flat.max()),
        "mean": float(flat.mean()),
        "std": float(flat.std()),
        "sat_high_pct": round(sat_high, 3),
        "sat_low_pct": round(sat_low, 3),
        "focus_laplacian_var": round(laplacian_var, 1),
    }


def diagnose(s: dict[str, float]) -> str:
    """Map stats to one-shot text hints for a fast read."""
    notes: list[str] = []
    if s["sat_high_pct"] > 5.0:
        notes.append(f"BRIGHT: {s['sat_high_pct']:.1f}% pixels at sensor ceiling")
    if s["sat_low_pct"] > 30.0:
        notes.append(f"DARK: {s['sat_low_pct']:.1f}% pixels at floor")
    if s["std"] < 10.0:
        notes.append("LOW-CONTRAST: std<10 (uniform scene or wrong exposure)")
    if s["focus_laplacian_var"] < 50.0:
        notes.append(f"DEFOCUS-LIKE: laplacian-var={s['focus_laplacian_var']:.0f}")
    return "; ".join(notes) if notes else "ok"


def main() -> None:
    args = parse_args()

    exposures = [float(v.strip()) for v in args.exposures.split(",") if v.strip()]
    formats = [v.strip() for v in args.formats.split(",") if v.strip()]
    if not exposures or not formats:
        raise SystemExit("--exposures and --formats must each have at least one value.")

    out_dir = args.out or DEFAULT_OUT / f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    report: list[dict[str, object]] = []

    for fmt in formats:
        cfg = CameraConfig(
            exposure_us=exposures[0],
            gain=args.gain,
            pixel_format=fmt,
            width=args.width,
            height=args.height,
        )
        cam = create_camera(backend="crevis", config=cfg, device_index=args.device)
        cam.open()
        try:
            for exp in exposures:
                cam.set_feature("ExposureTime", float(exp), "float")
                img = cam.grab()
                s = stats(img)
                tag = f"{fmt}_exp{int(exp):07d}us"
                path = out_dir / f"{tag}.png"
                cv2.imwrite(str(path), img)
                row = {"file": path.name, "exposure_us": exp, "format": fmt, **s,
                       "diagnose": diagnose(s)}
                report.append(row)
                logger.info(
                    "%s  mean=%.0f std=%.0f sat_hi=%.2f%%  focus=%.0f  -> %s",
                    tag, s["mean"], s["std"], s["sat_high_pct"],
                    s["focus_laplacian_var"], row["diagnose"],
                )
        finally:
            cam.close()

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(report, indent=2))
    logger.info("Saved %d frames + %s", len(report), summary_path)

    print()
    print("=== Optical sweep summary ===")
    print(f"{'file':40s} {'mean':>6s} {'std':>6s} {'sat%':>6s} {'focus':>8s}  notes")
    for r in report:
        print(
            f"{r['file']:40s} {r['mean']:>6.0f} {r['std']:>6.0f} "
            f"{r['sat_high_pct']:>6.2f} {r['focus_laplacian_var']:>8.0f}  {r['diagnose']}"
        )


if __name__ == "__main__":
    main()
