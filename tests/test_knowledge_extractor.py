"""Tests for the AI Business Knowledge extractor (pipeline/knowledge_extractor.py).

Offline — no real Anthropic API. Uses fake/mock clients. No pytest.
    ./.venv/bin/python tests/test_knowledge_extractor.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk      # noqa: E402
import source_package as sp          # noqa: E402
import knowledge_extractor as ke     # noqa: E402


# --- helpers ----------------------------------------------------------------

def _pkg(sources):
    """sources: list of (filename, content, category) -> SourcePackage."""
    files = [(fn, content.encode("utf-8"), cat) for fn, content, cat in sources]
    return sp.build_package_from_files(files)


def _prop(**kw):
    p = {"category": "product", "attribute": "name", "value": "X", "confidence": 0.9,
         "status": "proposed", "source_filename": "deck.txt", "source_category": "pitch_deck",
         "source_section_reference": "", "evidence_excerpt": "", "temporal_context": "current",
         "notes": ""}
    p.update(kw)
    return p


class FakeClient:
    """Returns canned proposals (or raises) per batch; captures calls and usage."""
    model = "fake"

    def __init__(self, responder, usage=None):
        self.responder = responder
        self.calls = []
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5,
                               "cache_write_tokens": 0, "cache_read_tokens": 0}

    def complete(self, system, user, *, structured=False):
        self.calls.append(user)
        text = self.responder(user, len(self.calls))     # may raise
        return text, dict(self.usage)


def _fixed(proposals):
    return FakeClient(lambda user, n: json.dumps(proposals))


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeUsage:
    def __init__(self, i=0, o=0, w=0, r=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_creation_input_tokens = w
        self.cache_read_input_tokens = r


class FakeMsg:
    def __init__(self, text, usage):
        self.content = [FakeBlock(text)]
        self.usage = usage


class FakeDoc:
    def __init__(self, filename, text):
        self.filename = filename
        self.source_id = "src-000"
        self.source_category = "other"
        self.extraction_status = "success"
        self.extracted_text = text


# --- proposal validation / confirmation -------------------------------------

def test_direct_fact_becomes_confirmed():
    pkg = _pkg([("deck.txt", "We sell an embedded analytics SDK to platform teams", "pitch_deck")])
    client = _fixed([_prop(category="product", value="embedded analytics SDK",
                           evidence_excerpt="embedded analytics SDK")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.is_mock is False
    assert res.proposals_confirmed == 1, res.to_dict()
    items = res.business_knowledge.get_items(category="product")
    assert any(it.status == bk.CONFIRMED for it in items)


def test_interpretation_stays_proposed():
    pkg = _pkg([("deck.txt", "We sell to VP Engineering leaders", "pitch_deck")])
    client = _fixed([_prop(category="buyer", attribute="role", value="VP Engineering",
                           evidence_excerpt="VP Engineering")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 0
    assert res.proposals_left_proposed == 1
    assert res.business_knowledge.get_items(category="buyer")[0].status == bk.PROPOSED


def test_missing_source_stays_proposed_with_warning():
    pkg = _pkg([("deck.txt", "We sell an SDK", "pitch_deck")])
    client = _fixed([_prop(source_filename="nope.txt", value="SDK", evidence_excerpt="SDK")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 0 and res.proposals_left_proposed == 1
    assert any("attribution" in w.lower() for w in res.warnings)


def test_missing_excerpt_stays_proposed_with_warning():
    pkg = _pkg([("deck.txt", "We sell an SDK", "pitch_deck")])
    client = _fixed([_prop(value="SDK", evidence_excerpt="not present here")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 0 and res.proposals_left_proposed == 1
    assert any("attribution" in w.lower() for w in res.warnings)


def test_invalid_category_rejected():
    pkg = _pkg([("deck.txt", "text", "pitch_deck")])
    client = _fixed([_prop(category="banana")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_rejected == 1 and res.proposals_accepted == 0


def test_malformed_confidence_rejected():
    pkg = _pkg([("deck.txt", "text", "pitch_deck")])
    client = _fixed([_prop(confidence="high")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_rejected == 1


def test_empty_value_non_unknown_rejected():
    pkg = _pkg([("deck.txt", "text", "pitch_deck")])
    client = _fixed([_prop(value="", status="proposed")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_rejected == 1


def test_unknown_information_preserved():
    pkg = _pkg([("deck.txt", "text", "pitch_deck")])
    client = _fixed([_prop(category="geography", attribute="region", value="",
                           status="unknown", evidence_excerpt="")])
    res = ke.extract_business_knowledge(pkg, client=client)
    unknown = res.business_knowledge.get_items(status=bk.UNKNOWN)
    assert any(it.category == "geography" for it in unknown)


def test_chain_of_thought_rejected():
    pkg = _pkg([("deck.txt", "text", "pitch_deck")])
    client = _fixed([_prop(notes="Step 1: I think the buyer is...")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_rejected == 1


# --- safety rules -----------------------------------------------------------

def test_hard_exclusion_mention_not_confirmed():
    pkg = _pkg([("deck.txt", "We often work with banks and agencies", "pitch_deck")])
    client = _fixed([_prop(category="hard_exclusion_candidate", attribute="rule",
                           value="banks", evidence_excerpt="banks")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 0
    it = res.business_knowledge.get_items(category="hard_exclusion_candidate")[0]
    assert it.status == bk.PROPOSED


def test_explicit_exclusion_stays_proposed_candidate():
    pkg = _pkg([("icp.txt", "We reject staffing agencies entirely", "existing_icp")])
    client = _fixed([_prop(category="hard_exclusion_candidate", attribute="rule",
                           value="staffing agencies", source_filename="icp.txt",
                           source_category="existing_icp", evidence_excerpt="staffing agencies")])
    res = ke.extract_business_knowledge(pkg, client=client)
    items = res.business_knowledge.get_items(category="hard_exclusion_candidate")
    assert items and items[0].status == bk.PROPOSED


def test_historical_customer_not_current_target():
    pkg = _pkg([("deck.txt", "A former customer was OldCo", "pitch_deck")])
    client = _fixed([_prop(category="customer", attribute="best_customer", value="OldCo",
                           temporal_context="former_customer", evidence_excerpt="OldCo")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 0            # historical -> not confirmed as current
    assert res.proposals_left_proposed == 1


# --- merge / conflict -------------------------------------------------------

def test_duplicate_facts_merge_source_references():
    pkg = _pkg([("deck.txt", "Alpha product. Also alpha product here.", "pitch_deck")])
    client = _fixed([
        _prop(category="product", attribute="name", value="alpha", evidence_excerpt="Alpha product"),
        _prop(category="product", attribute="name", value="alpha",
              evidence_excerpt="alpha product here"),
    ])
    res = ke.extract_business_knowledge(pkg, client=client)
    items = res.business_knowledge.get_items(category="product")
    assert len(items) == 1
    assert len(items[0].source_references) == 2


def test_conflicting_facts_create_conflict_record():
    # company_size is single-valued: two different values are a genuine contradiction, not a list.
    pkg = _pkg([("deck.txt", "Target 50-500 employees, though one slide says 1000-5000", "pitch_deck")])
    client = _fixed([
        _prop(category="company_size", attribute="preference", value="50-500",
              evidence_excerpt="50-500"),
        _prop(category="company_size", attribute="preference", value="1000-5000",
              evidence_excerpt="1000-5000"),
    ])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.conflicts_created >= 1
    assert res.business_knowledge.conflicts


def test_user_confirmed_value_not_overwritten():
    knowledge = bk.BusinessKnowledge()
    a = knowledge.add_item("company_size", "preference", "50-500")   # single-valued -> conflicts
    knowledge.confirm_item(a.knowledge_id)
    res = ke.KnowledgeExtractionResult()
    src_index = {"x.txt": FakeDoc("x.txt", "we prefer 1000-5000 employees")}
    p = _prop(category="company_size", attribute="preference", value="1000-5000",
              source_filename="x.txt", evidence_excerpt="1000-5000")
    ke._ingest_proposal(p, knowledge, src_index, res)
    assert a.user_confirmed and a.status == bk.CONFIRMED     # not overwritten
    assert knowledge.conflicts                               # contrary evidence recorded


def test_rejected_item_stays_rejected():
    knowledge = bk.BusinessKnowledge()
    it = knowledge.add_item("product", "name", "OldTool")
    knowledge.reject_item(it.knowledge_id)
    res = ke.KnowledgeExtractionResult()
    res._unknown = 0
    src_index = {"x.txt": FakeDoc("x.txt", "OldTool is our product")}
    p = _prop(category="product", attribute="name", value="OldTool",
              source_filename="x.txt", evidence_excerpt="OldTool")
    ke._ingest_proposal(p, knowledge, src_index, res)
    assert it.status == bk.REJECTED
    assert len(knowledge.get_items(category="product")) == 2   # rejected + new active


# --- batching / retry / parsing ---------------------------------------------

def test_deterministic_batching():
    pkg = _pkg([("a.txt", "a" * 100, "other"), ("b.txt", "b" * 100, "other"),
                ("c.txt", "c" * 100, "other")])
    b1 = ke._build_batches(pkg, char_budget=250)
    b2 = ke._build_batches(pkg, char_budget=250)
    names = [[d.filename for d in batch] for batch in b1]
    assert names == [["a.txt", "b.txt"], ["c.txt"]]            # grouped, never split, ordered
    assert names == [[d.filename for d in batch] for batch in b2]


def test_failed_batch_retries_only_and_success_not_retried():
    pkg = _pkg([("good.txt", "g" * 100, "other"), ("bad.txt", "b" * 100, "other")])

    def responder(user, n):
        if "bad.txt" in user:
            raise RuntimeError("boom")
        return json.dumps([_prop(source_filename="good.txt", value="g", evidence_excerpt="g")])

    client = FakeClient(responder)
    res = ke.extract_business_knowledge(pkg, client=client, char_budget=120)
    assert res.successful_batches == 1 and res.failed_batches == 1
    assert res.retries == ke.MAX_RETRIES                       # only the failed batch retried
    assert sum(1 for u in client.calls if "good.txt" in u) == 1   # success never retried
    assert sum(1 for u in client.calls if "bad.txt" in u) == ke.MAX_RETRIES + 1


def test_parser_failure_isolated_to_one_batch():
    pkg = _pkg([("good.txt", "g" * 100, "other"), ("bad.txt", "b" * 100, "other")])

    def responder(user, n):
        if "bad.txt" in user:
            return "this is not json at all"
        return json.dumps([_prop(source_filename="good.txt", value="g", evidence_excerpt="g")])

    client = FakeClient(responder)
    res = ke.extract_business_knowledge(pkg, client=client, char_budget=120)
    assert res.parser_failures >= 1
    assert res.successful_batches == 1 and res.failed_batches == 1


def test_tolerant_json_extraction():
    assert ke.parse_proposals('[{"category":"product"}]')[0]["category"] == "product"
    assert ke.parse_proposals('```json\n[{"a":1}]\n```')[0]["a"] == 1
    assert ke.parse_proposals('{"proposals":[{"b":2}]}')[0]["b"] == 2
    assert ke.parse_proposals('text before [{"c":3}] text after')[0]["c"] == 3
    try:
        ke.parse_proposals("no json here")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- client behaviors (structured / caching / tokens) -----------------------

def test_structured_output_compatibility_path():
    seen = []

    def create(**kw):
        seen.append("output_config" in kw)
        return FakeMsg('{"proposals":[{"category":"product","attribute":"name","value":"X"}]}',
                       FakeUsage(i=5, o=2))

    client = ke.KnowledgeExtractionClient(_create=create)
    text, usage = client.complete(ke.build_system_blocks("sys"), "user", structured=True)
    assert seen == [True]                                      # structured request sent
    assert ke.parse_proposals(text)[0]["category"] == "product"


def test_structured_output_fallback_to_plain():
    calls = []

    def create(**kw):
        calls.append("output_config" in kw)
        if "output_config" in kw:
            raise RuntimeError("output_config not supported")
        return FakeMsg('[{"category":"product","attribute":"name","value":"X"}]', FakeUsage())

    client = ke.KnowledgeExtractionClient(_create=create)
    text, _ = client.complete(ke.build_system_blocks("sys"), "user", structured=True)
    assert calls == [True, False]                              # structured then plain
    assert ke.parse_proposals(text)[0]["value"] == "X"


def test_prompt_caching_path_captures_cache_write():
    captured = {}

    def create(**kw):
        captured["system"] = kw["system"]
        return FakeMsg("[]", FakeUsage(i=100, o=10, w=250, r=0))

    client = ke.KnowledgeExtractionClient(_create=create)
    _, usage = client.complete(ke.build_system_blocks("stable prompt"), "docs")
    assert captured["system"][0].get("cache_control") == {"type": "ephemeral"}
    assert usage["cache_write_tokens"] == 250


def test_caching_unavailable_fallback():
    calls = []

    def create(**kw):
        calls.append(kw["system"])
        if any("cache_control" in b for b in kw["system"]):
            raise RuntimeError("prompt caching not supported: cache error")
        return FakeMsg("[]", FakeUsage(i=50))

    client = ke.KnowledgeExtractionClient(_create=create)
    text, usage = client.complete(ke.build_system_blocks("stable"), "docs")
    assert len(calls) == 2                                     # retried without cache_control
    assert not any("cache_control" in b for b in calls[1])
    assert usage["input_tokens"] == 50 and text == "[]"


def test_token_metrics_captured():
    pkg = _pkg([("deck.txt", "We sell an SDK", "pitch_deck")])
    client = _fixed([_prop(value="SDK", evidence_excerpt="SDK")])
    client.usage = {"input_tokens": 12, "output_tokens": 7,
                    "cache_read_tokens": 3, "cache_write_tokens": 9}
    res = ke.extract_business_knowledge(pkg, client=client)
    assert (res.input_tokens, res.output_tokens, res.cache_read_tokens, res.cache_write_tokens) \
        == (12, 7, 3, 9)
    assert res.model_calls == 1


# --- mock / entry points / gap report ---------------------------------------

def test_mock_mode_clearly_marked():
    pkg = _pkg([("deck.txt", "We build analytics tools", "pitch_deck")])
    res = ke.extract_business_knowledge(pkg, client=ke.MockKnowledgeClient())
    assert res.is_mock is True and res.model == "mock"
    assert res.proposals_received > 0
    assert res.gap_report is not None


def _run_extract(entry):
    pkg = _pkg([("deck.txt", "We sell an embedded analytics SDK", "pitch_deck")])
    client = _fixed([_prop(category="product", value="embedded analytics SDK",
                           evidence_excerpt="embedded analytics SDK")])
    return ke.extract_business_knowledge(pkg, client=client, entry_point=entry)


def test_both_entry_points_behave_identically():
    a = _run_extract("generate_new")
    b = _run_extract("standardize_existing")
    assert a.business_knowledge.summary()["fields"] == b.business_knowledge.summary()["fields"]
    assert a.proposals_confirmed == b.proposals_confirmed
    assert a.gap_report.completeness_score == b.gap_report.completeness_score


def test_updated_gap_report_returned():
    pkg = _pkg([("deck.txt", "We sell an embedded analytics SDK", "pitch_deck")])
    client = _fixed([_prop(category="product", value="embedded analytics SDK",
                           evidence_excerpt="embedded analytics SDK")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert isinstance(res.gap_report, __import__("knowledge_gaps").KnowledgeGapReport)
    assert 0 <= res.gap_report.completeness_score <= 100
    assert res.gap_report.is_ready_for_icp_generation in (True, False)


# --- 4.1D.2 hardening regressions -------------------------------------------

def test_oversized_source_is_split():
    pkg = _pkg([("big.txt", "z" * 45000, "other")])
    batches = ke._build_batches(pkg, char_budget=18000)
    assert len(batches) == 3                                    # 18k + 18k + 9k, never one giant unit
    for b in batches:
        assert sum(len(u.extracted_text) for u in b) <= 18000
        assert all(u.filename == "big.txt" for u in b)         # filename preserved for verification


def test_multi_value_attribute_no_conflict():
    pkg = _pkg([("deck.txt", "Buyers: CEO, CTO, Founder", "pitch_deck")])
    client = _fixed([
        _prop(category="buyer", attribute="role", value="CEO", evidence_excerpt="CEO"),
        _prop(category="buyer", attribute="role", value="CTO", evidence_excerpt="CTO"),
        _prop(category="buyer", attribute="role", value="Founder", evidence_excerpt="Founder"),
    ])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.conflicts_created == 0                          # a list, not a conflict
    items = res.business_knowledge.get_items(category="buyer")
    assert len(items) == 1
    assert set(items[0].value.split("; ")) == {"CEO", "CTO", "Founder"}


def test_literal_excerpt_confirms_with_dash_and_whitespace_variant():
    # source uses an en-dash; the model quotes with a hyphen and extra spaces — still literal.
    pkg = _pkg([("deck.txt", "Price band: $10K – $25K, finalised at audit.", "service_catalogue")])
    client = _fixed([_prop(category="service", attribute="pricing", value="$10K-$25K",
                           source_filename="deck.txt", source_category="service_catalogue",
                           evidence_excerpt="$10K - $25K")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 1                        # recall up, precision intact


def test_paraphrased_evidence_not_confirmed():
    pkg = _pkg([("deck.txt", "We provide embedded analytics dashboards", "pitch_deck")])
    client = _fixed([_prop(category="product", attribute="name", value="analytics SDK",
                           evidence_excerpt="an embedded analytics software development kit")])
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.proposals_confirmed == 0 and res.proposals_left_proposed == 1


def test_no_batch_loss_on_truncated_output():
    pkg = _pkg([("deck.txt", "We sell an SDK product to teams", "pitch_deck")])
    good = json.dumps(_prop(category="product", attribute="name", value="SDK product",
                            evidence_excerpt="SDK product"))
    truncated = "[" + good + ',{"category":"service","attribute":"na'   # cut mid-second object
    client = FakeClient(lambda user, n: truncated)
    res = ke.extract_business_knowledge(pkg, client=client)
    assert res.failed_batches == 0 and res.successful_batches == 1
    assert res.proposals_received == 1                         # salvaged the one complete object


def test_parser_salvage_from_truncated_array():
    text = '[{"category":"a","attribute":"x","value":"1"},{"category":"b","attr'
    got = ke.parse_proposals(text)
    assert len(got) == 1 and got[0]["category"] == "a"


def test_salvage_ignores_non_proposal_json():
    try:
        ke.parse_proposals('prefix {"foo": 1} suffix')       # object without a category
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_max_proposals_per_batch_capped():
    pkg = _pkg([("deck.txt", "content", "pitch_deck")])
    many = [_prop(category="other", attribute=f"a{i}", value=str(i), evidence_excerpt="")
            for i in range(ke.MAX_PROPOSALS_PER_BATCH + 10)]
    res = ke.extract_business_knowledge(pkg, client=_fixed(many))
    assert res.proposals_received == ke.MAX_PROPOSALS_PER_BATCH
    assert any("capped" in w.lower() for w in res.warnings)


def test_prompt_retains_safety_rules():
    txt = ke.load_system_prompt().lower()
    for phrase in ("missing information stays unknown", "never infer", "hard-exclusion",
                   "never return `confirmed`", "verbatim"):
        assert phrase in txt, f"prompt missing safety phrase: {phrase}"


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
