"""Guards the link between docs/INSPECTION_CRITERIA.md and src/.../criteria.py.

If these tests fail, either the document was edited without updating the
module (or vice versa), or the per-class borderline directory was deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pcb_inspection import criteria
from pcb_inspection.criteria import (
    ALL_KEYS,
    BORDERLINE_KEY,
    BORDERLINE_ROOT,
    CRITERIA_APPROVED,
    CRITERIA_DOC_VERSION,
    LABELS,
    LABEL_SET,
    OK_KEY,
    SPEC_BY_LABEL,
    TIER_BY_LABEL,
    Tier,
    borderline_dir,
    is_defect,
    labels_by_tier,
    require_approved,
    validate_label,
)

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "INSPECTION_CRITERIA.md"


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


class TestTaxonomyMatchesDoc:
    """Every label key declared in the module must appear in §2 of the doc."""

    def test_doc_exists(self) -> None:
        assert DOC_PATH.exists(), f"{DOC_PATH} missing"

    def test_every_label_appears_in_doc_table(self) -> None:
        text = _doc_text()
        for key in LABELS:
            # Doc embeds keys in markdown code spans: `missing`, `cold_solder`, ...
            assert f"`{key}`" in text, f"Label {key!r} not found in §2 table"

    def test_reserved_keys_in_doc(self) -> None:
        text = _doc_text()
        assert f"`{OK_KEY}`" in text
        assert f"`{BORDERLINE_KEY}`" in text

    def test_doc_version_marker_matches_module(self) -> None:
        text = _doc_text()
        # Doc frontmatter row: "| 버전 | 0.1 (초안 / draft) |"
        m = re.search(r"\|\s*버전\s*\|\s*([0-9]+\.[0-9]+)", text)
        assert m, "Could not find version row in doc frontmatter"
        assert m.group(1) == CRITERIA_DOC_VERSION, (
            f"Doc version {m.group(1)} != module CRITERIA_DOC_VERSION {CRITERIA_DOC_VERSION}. "
            "Sync them when bumping."
        )


class TestLabelLookup:
    def test_labels_unique(self) -> None:
        assert len(LABELS) == len(set(LABELS))

    def test_reserved_disjoint_from_defects(self) -> None:
        assert OK_KEY not in LABEL_SET
        assert BORDERLINE_KEY not in LABEL_SET
        assert {OK_KEY, BORDERLINE_KEY} <= ALL_KEYS

    def test_spec_lookup(self) -> None:
        assert set(SPEC_BY_LABEL) == LABEL_SET
        for key, spec in SPEC_BY_LABEL.items():
            assert spec.key == key
            assert spec.tier is TIER_BY_LABEL[key]

    def test_is_defect(self) -> None:
        assert is_defect("missing")
        assert not is_defect(OK_KEY)
        assert not is_defect(BORDERLINE_KEY)
        assert not is_defect("not_a_real_key")

    def test_validate_label_passes_known(self) -> None:
        for key in ALL_KEYS:
            assert validate_label(key) == key

    def test_validate_label_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown label key"):
            validate_label("totally_made_up")


class TestTiers:
    def test_every_label_has_tier(self) -> None:
        assert set(TIER_BY_LABEL) == LABEL_SET

    def test_p0_set_matches_doc_intent(self) -> None:
        # §5: P0 = polarity, bridge, conductive foreign material.
        # foreign_material/solder_ball escalation handled by judgment layer (P1 here).
        assert set(labels_by_tier(Tier.P0)) == {"polarity", "bridge"}

    def test_no_tier_is_empty_except_p3(self) -> None:
        # P3 is allowed to be empty until a stat-only category is added.
        for tier in (Tier.P0, Tier.P1, Tier.P2):
            assert labels_by_tier(tier), f"Tier {tier} unexpectedly empty"


class TestBorderlineLayout:
    def test_root_exists(self) -> None:
        assert BORDERLINE_ROOT.exists(), (
            f"{BORDERLINE_ROOT} missing — see INSPECTION_CRITERIA.md §4.1"
        )

    def test_each_label_has_accept_and_reject(self) -> None:
        for key in LABELS:
            for decision in ("accept", "reject"):
                p = borderline_dir(key, decision)
                assert p.is_dir(), f"missing {p}"

    def test_borderline_dir_rejects_bad_decision(self) -> None:
        with pytest.raises(ValueError, match="decision must"):
            borderline_dir("missing", "maybe")

    def test_borderline_dir_rejects_unknown_label(self) -> None:
        with pytest.raises(ValueError, match="Unknown label key"):
            borderline_dir("not_a_label", "accept")


class TestApprovalGate:
    def test_default_is_draft(self) -> None:
        assert CRITERIA_APPROVED is False
        assert CRITERIA_DOC_VERSION == "0.1"

    def test_require_approved_blocks_while_draft(self) -> None:
        with pytest.raises(RuntimeError, match="draft"):
            require_approved()

    def test_require_approved_passes_when_flag_set(self, monkeypatch) -> None:
        monkeypatch.setattr(criteria, "CRITERIA_APPROVED", True)
        require_approved()  # should not raise
