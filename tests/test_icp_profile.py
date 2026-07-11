"""Unit tests for the Knowledge Layer (pipeline/icp_profile.py).

Offline, no LLM, no network, no pytest dependency:
    ./.venv/bin/python tests/test_icp_profile.py

Real-ICP tests read local PDFs via pipeline/icp_pdf.py (local file I/O only — no network).
Validation-edge tests use synthetic definition dicts, so they need no PDFs at all.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import icp_profile as ip  # noqa: E402

_CLEAN_NAME = __import__("re").compile(r"[A-Za-z][A-Za-z &/\-]{0,44}$")


def _profile_from_pdf(rel_pdf: str, name: str):
    from icp_pdf import extract_icp
    return ip.build_profile_from_text(name, extract_icp(str(ROOT / rel_pdf)).text)


# --- real ICP PDFs ----------------------------------------------------------

def test_fintech_icp_extracts_rubric():
    p = _profile_from_pdf("icp/Innotechfy_Fintech_Playbook.pdf", "FinTech")
    names = [d.name for d in p.scoring_dimensions]
    assert len(p.scoring_dimensions) == 7, names
    assert sum(d.weight for d in p.scoring_dimensions) == 100
    labels = {t.label for t in p.category_thresholds}
    assert {"A+ / Hot", "A / High", "B / Normal", "C / Low"} <= labels
    assert any(r.raw.replace("–", "-") == "50-500" for r in p.employee_ranges)
    # enrichment-required dimensions detected by name (funding/hiring/reachability)
    assert set(p.enrichment_required_fields) == {"Stage & funding fit", "Eng hiring signal", "Reachability"}
    assert not [w for w in p.warnings if "weight" in w or "overlap" in w]


def test_ai_icp_runs_and_records_unknowns_honestly():
    p = _profile_from_pdf("icp/AI.pdf", "AI")
    assert p.name == "AI"
    assert list(p.universal_exclusions) == list(ip.UNIVERSAL_VALIDITY_EXCLUSIONS)
    # nothing invented: any missing mandatory section is recorded, not fabricated
    if not p.scoring_dimensions:
        assert "scoring_dimensions" in p.unknown_fields
        assert any("no scoring dimensions" in w for w in p.warnings)


def test_wordpress_icp_no_garbage_dimensions():
    p = _profile_from_pdf("icp/Innotechfy_Headless_WordPress_Playbook.pdf", "WordPress")
    # every extracted dimension name must be clean (no prose fragments / punctuation)
    for d in p.scoring_dimensions:
        assert _CLEAN_NAME.fullmatch(d.name), f"garbage dimension name: {d.name!r}"
        assert d.weight is None or 0 <= d.weight <= 100


def test_xamarin_icp_thresholds_clean_or_unknown():
    p = _profile_from_pdf("icp/Innotechfy_Xamarin_Playbook.pdf", "Xamarin")
    assert p.name == "Xamarin"
    # threshold labels are category tokens, never prose sentences
    for t in p.category_thresholds:
        assert len(t.label) <= 20, f"prose threshold label: {t.label!r}"


# --- validation edge cases (synthetic definitions; no PDF) -------------------

def test_missing_sections_are_warned_and_marked_unknown():
    p = ip.build_profile_from_definition("Empty", {})
    for section in ("scoring_dimensions", "category_thresholds", "target_buyer_personas", "hard_exclusions"):
        assert section in p.unknown_fields
    assert any("no scoring dimensions" in w for w in p.warnings)
    assert any("no category thresholds" in w for w in p.warnings)


def test_duplicated_dimensions_detected():
    p = ip.build_profile_from_definition("Dup", {"dimensions": [
        {"name": "Title", "weight": 50}, {"name": "title", "weight": 50}]})
    assert any("duplicated scoring dimension" in w for w in p.warnings)


def test_malformed_weights_detected():
    p = ip.build_profile_from_definition("W", {"dimensions": [
        {"name": "X", "weight": 150}, {"name": "Y", "weight": 30}]})
    assert any("invalid weight 150" in w for w in p.warnings)
    assert any("weights sum to 180" in w for w in p.warnings)


def test_overlapping_thresholds_detected():
    p = ip.build_profile_from_definition("T", {"category_thresholds": [
        {"label": "A", "min": 70, "max": 84}, {"label": "B", "min": 80, "max": 90}]})
    assert any("overlapping thresholds" in w for w in p.warnings)


def test_malformed_threshold_min_gt_max_detected():
    p = ip.build_profile_from_definition("T2", {"category_thresholds": [
        {"label": "A", "min": 90, "max": 40}]})
    assert any("min 90 > max 40" in w for w in p.warnings)


def test_contradictory_exclusions_detected():
    p = ip.build_profile_from_definition("C", {
        "target_company_types": ["SaaS"], "excluded_company_types": ["saas"]})
    assert any("contradictory exclusion" in w for w in p.warnings)


def test_malformed_employee_ranges_detected():
    p = ip.build_profile_from_definition("E", {"employee_ranges": ["abc", "500-50"]})
    assert any("malformed employee range: 'abc'" in w for w in p.warnings)
    assert any("low>high: '500-50'" in w for w in p.warnings)


def test_ambiguous_definitions_preserved_not_invented():
    ambiguous = ["embedded finance vs core payment rails"]
    p = ip.build_profile_from_definition("Amb", {"ambiguous_definitions": ambiguous})
    assert p.ambiguous_definitions == ambiguous          # preserved exactly


def test_unknown_enrichment_requirements_flagged_by_name():
    p = ip.build_profile_from_definition("Enr", {"dimensions": [
        {"name": "Stage & funding fit", "weight": 20},
        {"name": "Eng hiring signal", "weight": 20},
        {"name": "Buyer persona", "weight": 60}]})
    assert set(p.enrichment_required_fields) == {"Stage & funding fit", "Eng hiring signal"}


def test_universal_exclusions_are_validity_only_and_constant():
    p = ip.build_profile_from_definition("U", {"excluded_company_types": ["crypto company", "bank"]})
    # commercial exclusions are ICP-specific...
    assert "crypto company" in p.excluded_company_types
    # ...and never leak into the universal (validity-only) set
    assert list(p.universal_exclusions) == list(ip.UNIVERSAL_VALIDITY_EXCLUSIONS)
    for u in p.universal_exclusions:
        assert u in ("duplicate record", "invalid or missing profile identifier",
                     "no identifiable current employment", "unusable or corrupt source data")


# --- runner -----------------------------------------------------------------

def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
