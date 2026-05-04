# Borderline asset layout

Authoritative spec lives in `docs/INSPECTION_CRITERIA.md` §4. This file is the
on-disk schema reference.

## Directory layout

```
data/criteria/borderline/
├── <label_key>/
│   ├── accept/        # OK 측 끝점 — 라벨러가 보고 "여기까진 통과" 기준
│   │   ├── 001.png
│   │   └── 001.json   # meta — same stem as the image
│   └── reject/        # NG 측 끝점 — "여기부터는 불합격" 기준
│       ├── 001.png
│       └── 001.json
```

`<label_key>` is one of the 12 defect keys defined in
`src/pcb_inspection/criteria.py` (`LABELS`). One folder per key already exists.

## Image file rules

- Format: PNG, lossless. 8-bit Mono or 16-bit Mono OK; record `dtype` in meta.
- Naming: zero-padded sequential per (label, decision), starting at `001`.
- Resolution: full ROI crop, not the whole board. Crop must include enough
  context for the call (typically 1.5× component bounding box).
- One `.png` ↔ one `.json` with the same stem. No orphans.

## meta.json schema (per image)

```json
{
  "schema_version": "1",
  "label": "cold_solder",
  "decision": "accept",
  "decided_by": "QA-홍길동",
  "decided_at": "2026-05-04",
  "reason": "광택 약하지만 fillet 정상 형성, IPC class 2 통과",
  "source": {
    "board": "OK01",
    "component_designator": "R12",
    "package": "0603",
    "lighting": "low_ring",
    "exposure_us": 1200,
    "capture_run": "data/captures/doe_OK01/low_ring__20260504_141023"
  },
  "image": {
    "file": "001.png",
    "dtype": "uint8",
    "shape": [256, 256]
  },
  "doc_version": "0.1"
}
```

### Required fields

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | string | This schema's version. Bump if the structure changes. Currently `"1"`. |
| `label` | string | Must match a key from `criteria.LABELS`. |
| `decision` | enum | `"accept"` or `"reject"` — must match the parent dir. |
| `decided_by` | string | QA approver name/ID. |
| `decided_at` | date (YYYY-MM-DD) | Approval date. |
| `reason` | string | One-line rationale; cited in label-training material. |
| `source.board` | string | Board ID used. |
| `source.lighting` | string | Light setup label (matches `capture_doe.py --light`). |
| `image.file` | string | Filename, must equal the `.png` next to this JSON. |
| `doc_version` | string | `INSPECTION_CRITERIA.md` version at time of decision. |

### Optional fields

| Field | When useful |
|-------|-------------|
| `source.component_designator` / `package` | Lets training pull only matching components when needed. |
| `source.exposure_us` | Reproducibility — recapture under same exposure. |
| `source.capture_run` | Trace back to the original DOE run directory. |

## Gate to start labeling for a class

`docs/INSPECTION_CRITERIA.md` §0 / §4.1: a class's labeling pipeline cannot
start until that class has **≥ 5 accept** AND **≥ 5 reject** images registered
here, each with a valid `meta.json`. Enforce via `tests/test_criteria.py` (or
a dedicated `borderline_complete()` check) once approval gate is opened.

## See also

- Example: `data/criteria/borderline/_example/` — concrete OK and NG meta files.
- Tests: `tests/test_criteria.py::TestBorderlineLayout`.
