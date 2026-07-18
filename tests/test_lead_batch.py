"""Domain + adapter unit tests for lead acquisition (Sprint 10, updated Sprint 10.1).

Covers the pure ACL (`vayne_adapter.leads_from_csv`) and domain (`lead_batch`) behavior: CSV mapping,
unknowns, de-duplication (malformed URLs never drive dedup), statistics, LeadBatch round-trip,
LeadSource validation, and the domain/adapter boundary. Lineage / approval-gated persistence lives in
`tests/test_lead_import.py`. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_lead_batch.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import lead_batch as lb                 # noqa: E402
import vayne_adapter as va              # noqa: E402

_CSV = (
    b"Full Name,Job Title,Company,Location,LinkedIn URL,LinkedIn Industry,LinkedIn Employees\n"
    b"Jane Doe,CTO,FinCo,New York US,https://linkedin.com/in/jane,FinTech,50-200\n"
    b"John Roe,VP Eng,FinCo,US,https://linkedin.com/in/jane,FinTech,50-200\n"      # dup LinkedIn URL
    b"Amy Lee,,HealthCo,London UK,notaurl,Healthcare,\n"                            # missing title + bad url
    b",Ghost,,,,,\n"                                                                # missing company -> skipped
    b"Bob Fox,Head of Data,LogiCo,Berlin DE,https://linkedin.com/in/bob,Logistics,201-500\n"
)


def _batch(csv=_CSV):
    return lb.build_lead_batch("hyp1", va.leads_from_csv(csv), source=lb.LeadSource(),
                               imported_by="dana", search_strategy_id="s1",
                               derived_from_search_strategy="search_strategy:hyp1:s1:1")


# 1. Adapter maps CSV to source-agnostic domain leads.
def test_adapter_maps_domain_leads():
    leads = va.leads_from_csv(_CSV)
    jane = next(l for l in leads if l.person_name == "Jane Doe")
    assert jane.company_name == "FinCo" and jane.current_title == "CTO"
    assert jane.geography == "New York US" and jane.industry == "FinTech"
    assert jane.linkedin_url == "https://linkedin.com/in/jane"
    assert jane.source == lb.SOURCE_VAYNE_SALESNAV and jane.lead_id


# 2. Adapter handles first/last name aliases and keeps unknowns unknown.
def test_adapter_aliases_and_unknowns():
    leads = va.leads_from_csv(b"First Name,Last Name,Title,Company Name,Country\n"
                              b"Sam,Patel,Head of Platform,Acme,Canada\n")
    lead = leads[0]
    assert lead.person_name == "Sam Patel" and lead.current_title == "Head of Platform"
    assert lead.company_name == "Acme" and lead.geography == "Canada"
    assert lead.linkedin_url == "" and lead.industry == ""       # unknown stays unknown


# 3. Malformed / empty / unrecognized CSV raises (adapter validates structure only).
def test_adapter_structure_validation():
    for bad in (b"", b"foo,bar\n1,2\n"):
        raised = False
        try:
            va.leads_from_csv(bad)
        except va.VayneImportError:
            raised = True
        assert raised, bad


# 4. De-dup + skip missing company + statistics are deterministic.
def test_dedup_skip_and_statistics():
    b = _batch()
    s = b.stats
    assert s["input_rows"] == 5 and s["imported"] == 3
    assert s["duplicates_removed"] == 1 and s["skipped_missing_company"] == 1
    assert s["malformed_linkedin_url"] == 1 and s["missing_title"] == 1
    assert s["unique_companies"] == 3 and s["with_linkedin_url"] == 2   # only VALID urls counted
    assert set(s["industries"]) == {"FinTech", "Healthcare", "Logistics"}
    assert any("duplicate" in w for w in lb.batch_warnings(b))


# 5. Unknown values remain unknown (never invented).
def test_unknown_values_remain_unknown():
    b = _batch()
    amy = next(l for l in b.leads if l.person_name == "Amy Lee")
    assert amy.current_title == "" and amy.company_size == ""


# 6. Malformed LinkedIn URL is not a valid URL, not counted, and never drives de-dup (Sprint 10.1).
def test_malformed_url_not_valid_and_not_dedup_key():
    assert lb.looks_like_url("notaurl") is False
    assert lb.looks_like_url("https://linkedin.com/in/x") is True
    # two rows: same person+company, both with DIFFERENT malformed URLs -> must de-dup to one
    csv = (b"Full Name,Job Title,Company,LinkedIn URL\n"
           b"Bob,VP,FinCo,notaurl1\n"
           b"Bob,VP,FinCo,notaurl2\n")
    b = lb.build_lead_batch("h", va.leads_from_csv(csv), source=lb.LeadSource())
    assert b.stats["imported"] == 1 and b.stats["duplicates_removed"] == 1
    assert b.stats["with_linkedin_url"] == 0 and b.stats["malformed_linkedin_url"] == 1
    assert b.leads[0].linkedin_url == "notaurl1"                 # raw value preserved as evidence


# 7. LeadBatch JSON round-trip is lossless (incl. provenance).
def test_lead_batch_roundtrip():
    b = _batch()
    b2 = lb.LeadBatch.from_dict(b.to_dict())
    assert b2.to_dict() == b.to_dict()
    assert b2.derived_from_search_strategy == "search_strategy:hyp1:s1:1"
    assert b2.source.kind == lb.SOURCE_VAYNE_SALESNAV


# 8. LeadSource validates its kind (extensible; unknown rejected).
def test_lead_source_kinds():
    assert lb.LeadSource(kind=lb.SOURCE_MANUAL_CSV).kind == lb.SOURCE_MANUAL_CSV
    raised = False
    try:
        lb.LeadSource(kind="not_a_source")
    except lb.LeadDomainError:
        raised = True
    assert raised


# 9. Boundary: domain has no CSV/scoring/adapter; adapter never resolves ownership/approval.
def test_boundary_and_no_cycles():
    domain = (ROOT / "pipeline" / "lead_batch.py").read_text(encoding="utf-8")
    for forbidden in ("import scoring", "import vayne_adapter", "import icp_project", "import csv",
                      "score_leads(", "detect_gaps("):
        assert forbidden not in domain, f"domain leaks: {forbidden}"
    adapter = (ROOT / "pipeline" / "vayne_adapter.py").read_text(encoding="utf-8")
    # the adapter must not depend on MarketHypothesis / strategy approval / scoring
    for forbidden in ("import icp_project", "import search_strategy", "import scoring",
                      "STRATEGY_APPROVED", "lead_batches", "score_leads("):
        assert forbidden not in adapter, f"adapter leaks: {forbidden}"
    import importlib
    for m in ("lead_batch", "vayne_adapter", "lead_import", "icp_project"):
        importlib.import_module(m)


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
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
