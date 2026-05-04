"""DOE lighting capture: tag a sweep of frames with the active light source so a
later compare step can rank Dome / Low-angle Ring / Coaxial (and combinations)
on the same board under the same exposure.

Operator workflow (manual light switching, no controller yet):

    # 1) Mount board OK01, switch on Dome only:
    python scripts/capture_doe.py --light dome --board OK01

    # 2) Switch lights to Low-angle Ring only (board untouched):
    python scripts/capture_doe.py --light low_ring --board OK01

    # 3) Coaxial:
    python scripts/capture_doe.py --light coaxial --board OK01

    # 4) Combinations (free-form labels, e.g. dome+coax):
    python scripts/capture_doe.py --light dome+coax --board OK01

Each run captures N frames at one or more exposures, writes per-frame PNGs
plus a manifest.json under data/captures/doe_<board>/<light>/, and appends a
top-level index.json so compare_lighting.py can find every light variant for
the board.

Captures only — judgement of "best light" is the compare step's job.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
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

# Whitelist of canonical single-source labels matches HARDWARE_SPEC §6 + PLAN §5.1.
# Combinations are written as plus-joined tokens (e.g. "dome+coax").
KNOWN_LIGHTS = {"dome", "low_ring", "coaxial", "coax"}
LIGHT_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tagged DOE capture for lighting evaluation")
    p.add_argument(
        "--light",
        required=True,
        help="Active light label. Single (dome|low_ring|coaxial) or combo joined by '+', "
        "e.g. dome+coax. Free-form combos allowed; warns if no known token present.",
    )
    p.add_argument(
        "--board",
        required=True,
        help="Board ID — typically OK## for known-good or NG##_<defect> for defect samples.",
    )
    p.add_argument(
        "--exposures",
        default="1000",
        help="Comma-separated exposure(s) in microseconds. One run captures the cross "
        "product (lights × exposures). Default 1ms (smoke-test sweet spot).",
    )
    p.add_argument(
        "--frames",
        type=int,
        default=5,
        help="Frames per (light, exposure). >1 lets the compare step quantify temporal "
        "stability and per-pixel noise. Default 5.",
    )
    p.add_argument("--gain", type=float, default=0.0, help="Gain in dB.")
    p.add_argument("--pixel-format", default="Mono8", help="Pixel format (Mono8|Mono10|Mono12).")
    p.add_argument("--width", type=int, default=None, help="ROI width (default sensor max).")
    p.add_argument("--height", type=int, default=None, help="ROI height (default sensor max).")
    p.add_argument("--device", type=int, default=0, help="Camera device index.")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Override output root (default: {DEFAULT_OUT}/doe_<board>/).",
    )
    p.add_argument(
        "--note",
        default="",
        help="Free-text note saved into the manifest (e.g. 'low_ring 45deg, intensity 60%%').",
    )
    return p.parse_args()


def validate_light(label: str) -> str:
    tokens = [t.strip() for t in label.split("+") if t.strip()]
    if not tokens:
        raise SystemExit("--light must contain at least one token")
    for t in tokens:
        if not LIGHT_TOKEN_RE.match(t):
            raise SystemExit(f"--light token {t!r} must be lowercase [a-z0-9_]")
    if not any(t in KNOWN_LIGHTS for t in tokens):
        logger.warning(
            "--light=%r contains no known token (%s); allowed but check spelling.",
            label, sorted(KNOWN_LIGHTS),
        )
    return "+".join(tokens)


def capture_one(cam, exposure_us: float, frames: int) -> list[np.ndarray]:
    cam.set_feature("ExposureTime", float(exposure_us), "float")
    # Drop one frame after exposure change so the next grab reflects new setting.
    cam.grab()
    return [cam.grab() for _ in range(frames)]


def update_index(board_dir: Path, light: str, run_dir: Path) -> None:
    """Append-only board-level index so compare_lighting.py can discover runs."""
    idx_path = board_dir / "index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text())
    else:
        idx = {"board": board_dir.name.removeprefix("doe_"), "runs": []}
    idx["runs"].append({
        "light": light,
        "run_dir": run_dir.name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })
    idx_path.write_text(json.dumps(idx, indent=2))


def main() -> None:
    args = parse_args()
    light = validate_light(args.light)
    exposures = [float(v.strip()) for v in args.exposures.split(",") if v.strip()]
    if not exposures:
        raise SystemExit("--exposures must have at least one value")

    board_dir = (args.out or DEFAULT_OUT) / f"doe_{args.board}"
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = board_dir / f"{light}__{run_stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = CameraConfig(
        exposure_us=exposures[0],
        gain=args.gain,
        pixel_format=args.pixel_format,
        width=args.width,
        height=args.height,
    )
    cam = create_camera(backend="crevis", config=cfg, device_index=args.device)
    cam.open()

    manifest: dict[str, object] = {
        "board": args.board,
        "light": light,
        "pixel_format": args.pixel_format,
        "gain_db": args.gain,
        "roi": {"width": args.width, "height": args.height},
        "note": args.note,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "frames": [],
    }

    try:
        for exp in exposures:
            imgs = capture_one(cam, exp, args.frames)
            for i, img in enumerate(imgs):
                fname = f"exp{int(exp):07d}us_f{i:02d}.png"
                cv2.imwrite(str(run_dir / fname), img)
                manifest["frames"].append({
                    "file": fname,
                    "exposure_us": exp,
                    "frame_index": i,
                    "shape": list(img.shape),
                    "dtype": str(img.dtype),
                })
            logger.info("light=%s exp=%dus  saved %d frames", light, int(exp), len(imgs))
    finally:
        cam.close()

    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    update_index(board_dir, light, run_dir)
    logger.info("Done: %s (%d frames)", run_dir, len(manifest["frames"]))


if __name__ == "__main__":
    main()
