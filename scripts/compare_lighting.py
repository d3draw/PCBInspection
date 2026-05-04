"""Compare lighting variants captured by capture_doe.py for one board.

Reads data/captures/doe_<board>/index.json, walks every run directory, and for
each (light, exposure) prints quantitative metrics that map to the PLAN §5.2
DOE protocol:

  * mean / std / saturation%  — exposure sanity (same as smoke step)
  * SNR (dB)                  — temporal noise across the N frames at a pixel
  * uniformity                — std/mean of a low-pass image (vignetting proxy)
  * focus (laplacian var)     — sharpness, lets us spot light scatter blur
  * contrast_p99_p1           — 99th-1st percentile spread (defect contrast proxy)

Then emits a per-light verdict line — strengths/weaknesses derived from the
metrics — and writes a CSV + JSON report next to index.json. The verdict is
heuristic, intended to focus the operator's eye, not to auto-select a light.

Usage:
    python scripts/compare_lighting.py data/captures/doe_OK01/
    python scripts/compare_lighting.py data/captures/doe_OK01/ --csv-only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare DOE lighting captures for one board")
    p.add_argument("board_dir", type=Path, help="Path to data/captures/doe_<board>/")
    p.add_argument("--csv-only", action="store_true", help="Skip stdout table, write files only")
    return p.parse_args()


def to_float(img: np.ndarray) -> np.ndarray:
    """Normalize to [0, 1] float32 regardless of source bit depth."""
    max_val = float(np.iinfo(img.dtype).max)
    return img.astype(np.float32) / max_val


def metrics(frames: list[np.ndarray]) -> dict[str, float]:
    """Derive DOE comparison metrics from a stack of N frames at the same setting.

    Temporal SNR requires N>=2; for N=1 we report nan and let the caller note it.
    """
    stack = np.stack([to_float(f) for f in frames], axis=0)  # (N, H, W)
    mean_img = stack.mean(axis=0)
    flat = mean_img.reshape(-1)

    # Exposure sanity
    sat_high = float(np.mean(flat >= 0.99) * 100.0)
    sat_low = float(np.mean(flat <= 0.01) * 100.0)

    # Temporal SNR: signal = per-pixel mean, noise = per-pixel temporal std.
    if stack.shape[0] >= 2:
        temporal_std = stack.std(axis=0).mean()
        # Avoid log of zero on perfectly flat fields.
        snr_db = float(20.0 * np.log10(max(mean_img.mean(), 1e-6) / max(temporal_std, 1e-6)))
    else:
        snr_db = float("nan")

    # Uniformity proxy: large-kernel blur, then std/mean. Small => uniform.
    h, w = mean_img.shape[-2:]
    k = max(31, (min(h, w) // 32) | 1)  # odd kernel ~3% of frame
    blurred = cv2.GaussianBlur((mean_img * 255).astype(np.uint8), (k, k), 0).astype(np.float32) / 255.0
    uniformity = float(blurred.std() / max(blurred.mean(), 1e-6))

    # Focus (sharpness) on the average frame, uint8 for stable Laplacian scale.
    gray8 = (mean_img * 255).astype(np.uint8)
    focus = float(cv2.Laplacian(gray8, cv2.CV_64F).var())

    # Contrast proxy: spread between dim and bright tail.
    p1, p99 = np.percentile(flat, [1.0, 99.0])
    contrast = float(p99 - p1)

    return {
        "mean": float(flat.mean()),
        "std": float(flat.std()),
        "sat_high_pct": round(sat_high, 3),
        "sat_low_pct": round(sat_low, 3),
        "snr_db": round(snr_db, 2),
        "uniformity": round(uniformity, 4),
        "focus": round(focus, 1),
        "contrast_p99_p1": round(contrast, 4),
        "frames": int(stack.shape[0]),
    }


def verdict(m: dict[str, float]) -> str:
    """Heuristic one-line read of the metrics. Operator confirms by eye."""
    notes: list[str] = []
    if m["sat_high_pct"] > 5.0:
        notes.append(f"clipped({m['sat_high_pct']:.1f}%)")
    if m["sat_low_pct"] > 30.0:
        notes.append(f"crushed({m['sat_low_pct']:.1f}%)")
    if not (m["sat_high_pct"] > 5.0 or m["sat_low_pct"] > 30.0):
        notes.append("exposure-ok")
    if m["frames"] >= 2:
        if m["snr_db"] >= 35:
            notes.append(f"low-noise({m['snr_db']}dB)")
        elif m["snr_db"] < 25:
            notes.append(f"noisy({m['snr_db']}dB)")
    if m["uniformity"] > 0.15:
        notes.append(f"vignetted({m['uniformity']:.2f})")
    elif m["uniformity"] < 0.05:
        notes.append("uniform")
    if m["contrast_p99_p1"] > 0.5:
        notes.append(f"high-contrast({m['contrast_p99_p1']:.2f})")
    elif m["contrast_p99_p1"] < 0.15:
        notes.append(f"flat({m['contrast_p99_p1']:.2f})")
    return ", ".join(notes)


def load_run(run_dir: Path) -> tuple[dict, dict[float, list[np.ndarray]]]:
    """Group frames in a run by exposure."""
    manifest = json.loads((run_dir / "manifest.json").read_text())
    by_exp: dict[float, list[np.ndarray]] = {}
    for entry in manifest["frames"]:
        img = cv2.imread(str(run_dir / entry["file"]), cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.warning("skip unreadable: %s", entry["file"])
            continue
        by_exp.setdefault(float(entry["exposure_us"]), []).append(img)
    return manifest, by_exp


def main() -> None:
    args = parse_args()
    board_dir = args.board_dir
    idx_path = board_dir / "index.json"
    if not idx_path.exists():
        raise SystemExit(f"No index.json under {board_dir} — run capture_doe.py first.")

    idx = json.loads(idx_path.read_text())
    rows: list[dict[str, object]] = []
    for run in idx["runs"]:
        run_dir = board_dir / run["run_dir"]
        if not run_dir.exists():
            logger.warning("missing run dir: %s", run_dir)
            continue
        manifest, by_exp = load_run(run_dir)
        for exp, frames in sorted(by_exp.items()):
            m = metrics(frames)
            rows.append({
                "light": manifest["light"],
                "exposure_us": int(exp),
                "run": run["run_dir"],
                **m,
                "verdict": verdict(m),
            })

    if not rows:
        raise SystemExit("No frames found across runs.")

    csv_path = board_dir / "compare_report.csv"
    json_path = board_dir / "compare_report.json"
    with csv_path.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    json_path.write_text(json.dumps(rows, indent=2))
    logger.info("Wrote %s and %s (%d rows)", csv_path.name, json_path.name, len(rows))

    if args.csv_only:
        return

    print()
    print(f"=== DOE lighting comparison: {idx.get('board','?')} ===")
    cols = ("light", "exp_us", "mean", "std", "sat%", "snr_dB", "unif", "focus", "ctr", "verdict")
    print(f"{cols[0]:>14s} {cols[1]:>7s} {cols[2]:>5s} {cols[3]:>5s} "
          f"{cols[4]:>5s} {cols[5]:>6s} {cols[6]:>5s} {cols[7]:>6s} {cols[8]:>5s}  {cols[9]}")
    for r in rows:
        print(
            f"{str(r['light']):>14s} {r['exposure_us']:>7d} "
            f"{r['mean']*255:>5.0f} {r['std']*255:>5.0f} "
            f"{r['sat_high_pct']:>5.1f} {r['snr_db']:>6.1f} "
            f"{r['uniformity']:>5.2f} {r['focus']:>6.0f} "
            f"{r['contrast_p99_p1']:>5.2f}  {r['verdict']}"
        )


if __name__ == "__main__":
    main()
