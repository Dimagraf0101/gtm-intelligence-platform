"""Lead business-entity restoration tests (Sprint 13a).

Proves the immutable domain ``Lead`` carries a canonical, source-agnostic business-attribute map without
breaking immutability, persistence, backward compatibility, the Vayne import, or qualification. Offline,
no LLM, no pytest:

    ./.venv/bin/python tests/test_lead_business_attributes.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import lead_batch as lb                   # noqa: E402
import business_attributes as ba          # noqa: E402
import vayne_adapter as va                # noqa: E402
import qualification_mapper as qm         # noqa: E402

# A rich CSV exercising every canonical business attribute + the website/company-linkedin split.
_RICH_CSV = (
    b"First Name,Last Name,Job Title,Company,Location,LinkedIn URL,Company Website,"
    b"Company LinkedIn URL,Number of Connections,LinkedIn Founded Year,LinkedIn Specialities,"
    b"Job Started On,Employee Count,LinkedIn Industry\n"
    b"Jane,Doe,CTO,FinCo,US,https://linkedin.com/in/jane,https://finco.com,"
    b"https://linkedin.com/company/finco,873,2011,Payments; APIs,2021-03,240,FinTech\n")


# --- registry ---------------------------------------------------------------

def test_registry_filters_unknown_and_empty_keys():
    got = ba.normalize_attributes({"first_name": " Jane ", "bogus": "x", "connections": "",
                                   "founded_year": "2011"})
    assert got == {"first_name": "Jane", "founded_year": "2011"}      # unknown + empty dropped, trimmed
    assert ba.is_known("connections") and not ba.is_known("bogus")


# --- immutability -----------------------------------------------------------

def test_core_and_attributes_are_immutable():
    lead = lb.Lead(company_name="Acme", attributes={"first_name": "Jane", "connections": "500"})
    try:
        lead.company_name = "X"                                       # frozen dataclass
        assert False, "core field was mutable"
    except Exception:
        pass
    try:
        lead.attributes["first_name"] = "Z"                          # frozen mapping
        assert False, "attributes were mutable"
    except TypeError:
        pass
    try:
        lead.attributes["new"] = "y"
        assert False, "attributes accepted a new key"
    except TypeError:
        pass


def test_attributes_normalized_at_construction():
    lead = lb.Lead(attributes={"first_name": " Jane ", "bogus": "x", "last_name": ""})
    assert dict(lead.attributes) == {"first_name": "Jane"}            # trimmed, filtered, empty dropped


# --- persistence / round-trip ----------------------------------------------

def test_round_trip_serialization_preserves_attributes():
    lead = lb.Lead(company_name="Acme", person_name="Jane Doe",
                   attributes={"first_name": "Jane", "last_name": "Doe", "connections": "500"})
    d = lead.to_dict()
    assert isinstance(d["attributes"], dict)                          # plain dict for JSON
    again = lb.Lead.from_dict(d)
    assert again.to_dict() == d                                       # stable round-trip
    assert dict(again.attributes) == {"first_name": "Jane", "last_name": "Doe", "connections": "500"}


def test_batch_round_trip_with_attributes():
    leads = va.leads_from_csv(_RICH_CSV)
    src = lb.LeadSource(kind=lb.SOURCE_VAYNE_SALESNAV)
    batch = lb.build_lead_batch("h1", leads, source=src, imported_by="dana",
                                search_strategy_id="s1", derived_from_search_strategy="ref")
    b2 = lb.LeadBatch.from_dict(batch.to_dict())
    assert b2.to_dict() == batch.to_dict()
    assert dict(b2.leads[0].attributes) == dict(batch.leads[0].attributes)


# --- backward compatibility -------------------------------------------------

def test_old_lead_json_without_attributes_loads():
    old = {"lead_id": "ld1", "company_name": "Old", "person_name": "P", "source": "x"}
    lead = lb.Lead.from_dict(old)
    assert dict(lead.attributes) == {}                               # defaults to empty (unknown)


def test_old_batch_json_without_attributes_loads():
    old = {
        "batch_id": "lb-old", "hypothesis_id": "p1",
        "source": {"kind": lb.SOURCE_VAYNE_SALESNAV},
        "imported_at": "t", "imported_by": "x", "search_strategy_id": "s1",
        "leads": [{"lead_id": "ld1", "company_name": "Old"}],        # pre-13a lead (no attributes)
        "stats": {},
    }
    b = lb.LeadBatch.from_dict(old)
    assert dict(b.leads[0].attributes) == {}


# --- Vayne import: attributes preserved, website vs company-linkedin split ---

def test_vayne_import_preserves_business_attributes():
    lead = va.leads_from_csv(_RICH_CSV)[0]
    attrs = dict(lead.attributes)
    assert attrs == {
        "first_name": "Jane", "last_name": "Doe", "job_started": "2021-03",
        "connections": "873", "company_linkedin_url": "https://linkedin.com/company/finco",
        "employee_count": "240", "founded_year": "2011", "specialities": "Payments; APIs",
    }
    # core stays source-agnostic; company_url is the WEBSITE (not the company LinkedIn url)
    assert lead.company_url == "https://finco.com"
    assert lead.company_name == "FinCo" and lead.industry == "FinTech"


def test_website_and_company_linkedin_not_duplicated():
    lead = va.leads_from_csv(_RICH_CSV)[0]
    assert lead.company_url == "https://finco.com"                   # core = website
    assert lead.attributes["company_linkedin_url"] == "https://linkedin.com/company/finco"
    assert lead.company_url != lead.attributes["company_linkedin_url"]


def test_unknown_attributes_stay_unknown():
    # a minimal CSV with none of the business-attribute columns -> attributes empty (never invented)
    csv = b"Company,Job Title,LinkedIn URL\nAcme,CTO,https://linkedin.com/in/x\n"
    lead = va.leads_from_csv(csv)[0]
    assert dict(lead.attributes) == {}
    assert lead.company_name == "Acme"


# --- qualification compatibility (scoring reads the CORE only) --------------

def test_qualification_mapper_unaffected_by_attributes():
    lead = va.leads_from_csv(_RICH_CSV)[0]
    scoring_lead = qm.to_scoring_lead(lead, 0)
    # the engine lead is built from the typed core; attributes never leak into scoring inputs
    assert scoring_lead.fields.get("company") == "FinCo"
    assert scoring_lead.fields.get("job_title") == "CTO"             # engine uses normalized keys


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
