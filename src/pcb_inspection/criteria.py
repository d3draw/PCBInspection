"""Single source of truth for the inspection criteria document.

Mirrors docs/INSPECTION_CRITERIA.md §2 (taxonomy) and §5 (priority tiers).
The document is human-authoritative; this module is the machine-readable face
so anomaly dataset prep, the labeling tool config, recipe YAMLs and the
operator UI all agree on the same label keys and priorities.

Anything that touches "what counts as a defect" should import from here, not
re-spell the strings.

Update flow when the document changes:
    1. Edit docs/INSPECTION_CRITERIA.md, bump its version, log §7.2.
    2. Update CRITERIA_DOC_VERSION below to match.
    3. If §2 (taxonomy) changed, update LABELS / TIER_BY_LABEL accordingly.
    4. tests/test_criteria.py guards the doc/code link.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Bump in lockstep with docs/INSPECTION_CRITERIA.md frontmatter "버전".
CRITERIA_DOC_VERSION = "0.1"

# Until the document reaches 1.0 (QA + 라인 운영 + 엔지니어링 3인 서명, §8),
# downstream code MUST refuse to start labeling. Set to True only when
# CRITERIA_DOC_VERSION advances to "1.0" or above.
CRITERIA_APPROVED = False


class Tier(str, Enum):
    """Detection-priority tier (§5). String values so they serialize naturally."""

    P0 = "P0"  # 미검출 = 라인 정지
    P1 = "P1"  # 미검출 = 출하 불가
    P2 = "P2"  # 미검출 = 보수 가능
    P3 = "P3"  # 통계 관리 항목


@dataclass(frozen=True)
class LabelSpec:
    """One row of the taxonomy (§2)."""

    key: str
    category_id: str       # C1..C6, "OK", "BL"
    category_name: str     # human-readable Korean (matches doc)
    subtype: str           # subtype within the category
    tier: Tier


# --- Taxonomy (mirrors INSPECTION_CRITERIA.md §2) ---------------------------
# Order intentionally matches the table in the document for diff-readability.

LABEL_SPECS: tuple[LabelSpec, ...] = (
    LabelSpec("missing",             "C1", "부품 유무·오삽", "Missing",             Tier.P1),
    LabelSpec("wrong_component",     "C1", "부품 유무·오삽", "Wrong component",     Tier.P1),
    LabelSpec("polarity",            "C2", "부품 극성",      "Polarity error",      Tier.P0),
    LabelSpec("offset",              "C3", "위치·회전",      "Offset",              Tier.P2),
    LabelSpec("rotation",            "C3", "위치·회전",      "Rotation",            Tier.P2),
    LabelSpec("cold_solder",         "C4", "납땜 품질",      "Cold solder",         Tier.P1),
    LabelSpec("insufficient_solder", "C4", "납땜 품질",      "Insufficient solder", Tier.P1),
    LabelSpec("excess_solder",       "C4", "납땜 품질",      "Excess solder",       Tier.P2),
    LabelSpec("bridge",              "C4", "납땜 품질",      "Bridge",              Tier.P0),
    # solder_ball / foreign_material: P0 only when 도전성 (conductive); the model
    # cannot tell. Tag at P1 here and let the judgment layer escalate by rule.
    LabelSpec("solder_ball",         "C5", "납볼·이물",      "Solder ball",         Tier.P1),
    LabelSpec("foreign_material",    "C5", "납볼·이물",      "Foreign material",    Tier.P1),
    LabelSpec("tombstone",           "C6", "부품 들림",      "Tombstone",           Tier.P1),
)

# Reserved keys that are NOT defects but appear in labeling pipelines.
OK_KEY = "ok"
BORDERLINE_KEY = "borderline"
RESERVED_KEYS: frozenset[str] = frozenset({OK_KEY, BORDERLINE_KEY})

LABELS: tuple[str, ...] = tuple(s.key for s in LABEL_SPECS)
LABEL_SET: frozenset[str] = frozenset(LABELS)
ALL_KEYS: frozenset[str] = LABEL_SET | RESERVED_KEYS

SPEC_BY_LABEL: dict[str, LabelSpec] = {s.key: s for s in LABEL_SPECS}
TIER_BY_LABEL: dict[str, Tier] = {s.key: s.tier for s in LABEL_SPECS}


def labels_by_tier(tier: Tier) -> tuple[str, ...]:
    """All label keys at the given priority tier."""
    return tuple(s.key for s in LABEL_SPECS if s.tier is tier)


def is_defect(key: str) -> bool:
    """True if the key is a defect class (excludes 'ok' and 'borderline')."""
    return key in LABEL_SET


def validate_label(key: str) -> str:
    """Return key if known, else raise. Use at all input boundaries."""
    if key not in ALL_KEYS:
        raise ValueError(
            f"Unknown label key {key!r}. "
            f"Allowed: {sorted(ALL_KEYS)}. "
            f"See docs/INSPECTION_CRITERIA.md §2."
        )
    return key


# --- Borderline asset layout (mirrors INSPECTION_CRITERIA.md §4.1) -----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CRITERIA_DATA_ROOT = PROJECT_ROOT / "data" / "criteria"
BORDERLINE_ROOT = CRITERIA_DATA_ROOT / "borderline"


def borderline_dir(label: str, decision: str) -> Path:
    """Resolve data/criteria/borderline/<label>/<accept|reject>/."""
    if decision not in ("accept", "reject"):
        raise ValueError(f"decision must be 'accept' or 'reject', got {decision!r}")
    validate_label(label)
    return BORDERLINE_ROOT / label / decision


def require_approved() -> None:
    """Gate function: call before starting labeling pipelines.

    Raises RuntimeError until docs/INSPECTION_CRITERIA.md reaches 1.0 with QA
    sign-off (§8) and CRITERIA_APPROVED is set True.
    """
    if not CRITERIA_APPROVED:
        raise RuntimeError(
            f"Inspection criteria still at draft v{CRITERIA_DOC_VERSION}. "
            "Labeling/training cannot start until docs/INSPECTION_CRITERIA.md "
            "is signed off (§8) and CRITERIA_APPROVED is set True."
        )
