"""Tests for the Knowledge Interview Engine (pipeline/knowledge_interview.py).

Proves the Sprint 5.3 guarantees: the plan is deterministic and project-scoped, answers write only
into the selected project's ICP Knowledge, Company Knowledge is read but never mutated by an answer,
temporal safety holds, completion is derived from the recomputed gap report, and draft regeneration
versions correctly. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_knowledge_interview.py
"""
import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import generated_icp as gi              # noqa: E402
import icp_project as ip                # noqa: E402
import icp_draft_generator as dg        # noqa: E402
import knowledge_interview as ki        # noqa: E402


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "d", "value_proposition": "v", "business_model": "m"},
            "dimensions": [
                {"name": "Segment fit", "purpose": "p", "weight": 50, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Buyer persona", "purpose": "p", "weight": 50, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": ["x"], "acceptable": [], "non_ideal": ["y"]},
            "generation_notes": "note",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


class BadWordingClient:
    """Returns garbage: Python must fall back to the deterministic wording."""
    model = "bad"

    def complete(self, system, user, *, structured=False):
        return "not json at all", {}


def _company():
    c = bk.BusinessKnowledge()
    c.add_item("company", "name", "Acme Software", status=bk.CONFIRMED, evidence_excerpt="Acme")
    c.add_item("service", "name", "Custom software delivery", status=bk.CONFIRMED,
               evidence_excerpt="Custom software delivery")
    return c


def _portfolio():
    port = ip.ICPPortfolio(company=_company())
    fintech = port.create_project("FinTech", hypothesis="Sell to FinTech scale-ups")
    healthcare = port.create_project("Healthcare", hypothesis="Sell to hospital networks")
    return port, fintech, healthcare


def _iv(port, project, **kw):
    iv = ki.KnowledgeInterview.for_project(port.company, project, **kw)
    iv.start()
    return iv


def _q(iv, gap_field):
    return next(q for q in iv.session.questions if q.gap_field == gap_field)


def _make_project_conflict(project, category="company_size", attribute="preference"):
    """Two differing, un-confirmed values on one core single-value attribute -> open conflict."""
    project.project_knowledge.add_item(category, attribute, "50-200", status=bk.CONFIRMED)
    project.project_knowledge.add_item(category, attribute, "500-1000", status=bk.CONFIRMED)
    return project.project_knowledge.conflicts[0]


def _answer_blocking(iv):
    """Answer every blocking question so the session can reach completion."""
    plan = (("target_company", "FinTech", bk.TEMPORAL_CURRENT),
            ("buyer_roles", "CTO; VP Engineering", bk.TEMPORAL_CURRENT),
            ("qualification_dimensions", "embedded finance", bk.TEMPORAL_CURRENT),
            ("target_vs_exclusion", "Reject staffing agencies", None))
    for gap_field, value, temporal in plan:
        pending = [q for q in iv.session.questions
                   if q.gap_field == gap_field and q.status == ki.PENDING]
        if not pending:
            continue
        kw = {"temporal_context": temporal} if temporal else {}
        res = iv.submit_answer(pending[0].question_id, value, **kw)
        assert res.ok, res.error


# 1. Plan is generated only from gaps/conflicts for the selected project.
def test_plan_comes_only_from_gaps_and_conflicts():
    port, fintech, _ = _portfolio()
    _make_project_conflict(fintech)
    iv = _iv(port, fintech)
    report = iv.gap_report()
    gap_fields = {g.field for g in report.blocking_gaps + report.important_gaps}
    conflict_ids = {c.conflict_id for c in fintech.project_knowledge.conflicts}
    project_item_ids = {i.knowledge_id for i in fintech.project_knowledge.knowledge_items}
    assert iv.session.questions
    for q in iv.session.questions:
        assert q.project_id == fintech.project_id
        if q.source_type == ki.SRC_CONFLICT:
            assert q.related_conflict_id in conflict_ids
        elif q.source_type == ki.SRC_TEMPORAL:
            assert q.related_item_ids[0] in project_item_ids
        else:
            assert q.gap_field in gap_fields


# 2. FinTech questions never include Healthcare project gaps.
def test_fintech_questions_exclude_healthcare_gaps():
    port, fintech, healthcare = _portfolio()
    heal_conflict = _make_project_conflict(healthcare)
    heal_ids = {i.knowledge_id for i in healthcare.project_knowledge.knowledge_items}
    iv = _iv(port, fintech)
    for q in iv.session.questions:
        assert q.related_conflict_id != heal_conflict.conflict_id
        assert not (set(q.related_item_ids) & heal_ids)
    assert not [q for q in iv.session.questions if q.source_type == ki.SRC_CONFLICT]
    # ...while the Healthcare interview does raise its own conflict question.
    iv_h = _iv(port, healthcare)
    assert [q for q in iv_h.session.questions if q.source_type == ki.SRC_CONFLICT]


# 3. Company Knowledge is visible when planning but is not mutated by normal answers.
def test_company_knowledge_visible_but_not_mutated():
    port, fintech, _ = _portfolio()
    before = port.company.to_json()
    iv = _iv(port, fintech)
    # Company facts already satisfy these blocking gaps, so they are never asked -> company is read.
    asked = {q.gap_field for q in iv.session.questions}
    assert "company_context" not in asked and "product_or_service" not in asked
    res = iv.submit_answer(_q(iv, "target_company").question_id, "FinTech",
                           temporal_context=bk.TEMPORAL_CURRENT)
    assert res.ok
    assert port.company.to_json() == before          # a normal answer never touches Company Knowledge


# 4. Answers are written to the selected ICP Knowledge with the required provenance.
def test_answer_written_to_project_with_user_provenance():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO",
                           temporal_context=bk.TEMPORAL_CURRENT, note="from the call")
    assert res.ok and res.written_item_ids
    item = fintech.project_knowledge._get(res.written_item_ids[0])
    assert item.origin == bk.ORIGIN_USER
    assert item.status == bk.CONFIRMED
    assert item.user_confirmed is True
    assert item.confidence == 1.0
    assert item.category == "buyer" and item.value == "CTO"
    # the answer links back to the question it came from
    assert any(res.question_id in n for n in item.notes)


# 5. A user answer in Project A never appears in Project B.
def test_answer_never_leaks_to_another_project():
    port, fintech, healthcare = _portfolio()
    iv = _iv(port, fintech)
    iv.submit_answer(_q(iv, "target_company").question_id, "FinTech",
                     temporal_context=bk.TEMPORAL_CURRENT)
    heal_composed = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert "FinTech" not in heal_composed.field("industries")
    assert "FinTech" not in [i.value for i in healthcare.project_knowledge.knowledge_items]
    assert "FinTech" not in [i.value for i in port.company.knowledge_items]


# 6. Blocking gaps are asked before important gaps.
def test_blocking_asked_before_important():
    port, fintech, _ = _portfolio()
    _make_project_conflict(fintech)
    iv = _iv(port, fintech)
    priorities = [q.priority for q in iv.session.questions]
    assert priorities == sorted(priorities)              # deterministic ask-order
    kinds = [q.source_type for q in iv.session.questions]
    assert kinds.index(ki.SRC_CONFLICT) < kinds.index(ki.SRC_BLOCKING) < kinds.index(ki.SRC_IMPORTANT)
    # the first question offered is the highest-priority one
    assert iv.current_question().source_type == ki.SRC_CONFLICT


# 7. Optional gaps are not asked by default.
def test_optional_gaps_not_asked_by_default():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    assert not [q for q in iv.session.questions if q.source_type == ki.SRC_OPTIONAL]
    assert iv.gap_report().optional_gaps                 # they exist, they are just not asked
    # ...only when explicitly requested.
    iv_opt = _iv(port, fintech, include_optional=True)
    assert [q for q in iv_opt.session.questions if q.source_type == ki.SRC_OPTIONAL]


# 8. Conflict questions reuse conflict resolution and retain contrary evidence.
def test_conflict_answer_retains_contrary_evidence():
    port, fintech, _ = _portfolio()
    conflict = _make_project_conflict(fintech)
    iv = _iv(port, fintech)
    q = next(q for q in iv.session.questions if q.source_type == ki.SRC_CONFLICT)
    assert sorted(q.suggested_options) == ["50-200", "500-1000"]
    res = iv.submit_answer(q.question_id, "50-200")
    assert res.ok and res.resolved_conflict_id == conflict.conflict_id
    rec = fintech.project_knowledge.conflicts[0]
    assert rec.status == bk.CONFLICT_USER
    preferred = fintech.project_knowledge._get(rec.preferred_item_id)
    assert preferred.value == "50-200" and preferred.user_confirmed
    # the losing value is still on record as evidence — nothing is deleted
    values = [i.value for i in fintech.project_knowledge.get_items(category="company_size")]
    assert "500-1000" in values and "50-200" in values
    assert len(rec.item_ids) == 2


# 9. Skip leaves the gap unresolved.
def test_skip_leaves_gap_unresolved():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    q = _q(iv, "target_company")
    before = len(fintech.project_knowledge.knowledge_items)
    iv.skip(q.question_id, note="need to check with sales")
    assert iv.session.get(q.question_id).status == ki.SKIPPED
    assert len(fintech.project_knowledge.knowledge_items) == before      # nothing invented
    assert any(g.field == "target_company" for g in iv.gap_report().blocking_gaps)
    assert iv.session.skipped_count == 1


# 10. Not applicable prevents repeat questioning without inventing a business fact.
def test_not_applicable_stops_repeat_without_inventing_fact():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    q = _q(iv, "best_customer_examples")
    before = len(fintech.project_knowledge.knowledge_items)
    iv.not_applicable(q.question_id, note="no customers in this segment yet")
    assert iv.session.get(q.question_id).status == ki.Q_NOT_APPLICABLE
    assert len(fintech.project_knowledge.knowledge_items) == before      # no knowledge written
    assert q.question_id in fintech.not_applicable
    # not asked again, even in a brand-new session
    iv2 = _iv(port, fintech)
    assert iv2.session.get(q.question_id).status == ki.Q_NOT_APPLICABLE
    assert q.question_id not in [p.question_id for p in iv2.session.pending()]
    # ...and the gap itself is NOT silently closed
    assert any(g.field == "best_customer_examples" for g in iv2.gap_report().important_gaps)


# 11. Unknown temporal context stays unknown.
def test_unknown_temporal_stays_unknown():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "target_company").question_id, "FinTech",
                           temporal_context=bk.TEMPORAL_UNKNOWN)
    assert res.ok
    item = fintech.project_knowledge.get_items(category="industry")[0]
    assert item.temporal_context == bk.TEMPORAL_UNKNOWN
    # an unknown temporal context becomes a clarification question rather than a silent "current"
    assert [q for q in iv.session.questions if q.source_type == ki.SRC_TEMPORAL
            and item.knowledge_id in q.related_item_ids]


# 12. Historical answers are not promoted to current targets.
def test_historical_answer_is_not_a_current_target():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    iv.submit_answer(_q(iv, "target_company").question_id, "Healthcare",
                     temporal_context=bk.TEMPORAL_HISTORICAL)
    item = fintech.project_knowledge.get_items(category="industry")[0]
    assert item.temporal_context == bk.TEMPORAL_HISTORICAL
    included, _, historical = dg._targets(iv.composed(), "industry")
    assert "Healthcare" in historical and "Healthcare" not in included


# 13/14. Promotion is explicit-only; a normal answer never auto-promotes.
def test_promotion_is_explicit_and_never_automatic():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO",
                           temporal_context=bk.TEMPORAL_CURRENT)
    # 14: submitting an answer never promotes anything
    assert "CTO" not in [i.value for i in port.company.knowledge_items]
    # 13: only the explicit human action promotes, and it copies by default
    iv.promote_answer(res.question_id)
    assert "CTO" in [i.value for i in port.company.get_items(category="buyer")]
    assert "CTO" in [i.value for i in fintech.project_knowledge.get_items(category="buyer")]
    promoted = port.company.get_items(category="buyer")[0]
    assert promoted.origin == bk.ORIGIN_USER


def test_promotion_can_move_instead_of_copy():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO",
                           temporal_context=bk.TEMPORAL_CURRENT)
    iv.promote_answer(res.question_id, move=True)
    assert "CTO" in [i.value for i in port.company.get_items(category="buyer")]
    assert [i for i in fintech.project_knowledge.get_items(category="buyer") if i.is_active] == []


# 15. Session cannot complete while blocking gaps remain.
def test_session_cannot_complete_with_blocking_gaps():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    assert iv.session.status == ki.IN_PROGRESS
    # click through every question without answering: still not complete
    for q in list(iv.session.pending()):
        iv.skip(q.question_id)
    assert iv.session.current_gap_summary["blocking_gaps"] > 0
    assert iv.session.status != ki.COMPLETED
    assert iv.session.status == ki.INCOMPLETE


# 16. Session completes when blocking gaps and core conflicts hit zero, important gaps may remain.
def test_session_completes_with_important_gaps_remaining():
    port, fintech, _ = _portfolio()
    _make_project_conflict(fintech)
    iv = _iv(port, fintech)
    conflict_q = next(q for q in iv.session.questions if q.source_type == ki.SRC_CONFLICT)
    iv.submit_answer(conflict_q.question_id, "50-200")
    _answer_blocking(iv)
    assert iv.session.current_gap_summary["blocking_gaps"] == 0
    assert iv.session.current_gap_summary["core_open_conflicts"] == 0
    assert iv.session.current_gap_summary["important_gaps"] > 0      # important gaps still open
    assert iv.session.status == ki.COMPLETED
    assert iv.session.completed_at


# 17. The gap report is recomputed after every accepted answer.
def test_gap_report_recomputed_after_every_answer():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    initial = dict(iv.session.initial_gap_summary)
    res1 = iv.submit_answer(_q(iv, "target_company").question_id, "FinTech",
                            temporal_context=bk.TEMPORAL_CURRENT)
    assert res1.gap_summary["blocking_gaps"] < initial["blocking_gaps"]
    res2 = iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO",
                            temporal_context=bk.TEMPORAL_CURRENT)
    assert res2.gap_summary["blocking_gaps"] < res1.gap_summary["blocking_gaps"]
    # the session summary always matches a freshly computed report
    fresh = iv.gap_report()
    assert iv.session.current_gap_summary["blocking_gaps"] == len(fresh.blocking_gaps)
    assert iv.session.current_gap_summary["completeness"] == fresh.completeness_score
    assert iv.session.initial_gap_summary == initial                 # initial snapshot is preserved


# 18/19/20/21. Draft regeneration.
def test_generate_updated_draft_versions_honestly():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    _answer_blocking(iv)
    res1 = iv.generate_updated_draft(client=FakeDraftClient())
    assert len(fintech.draft_versions) == 1                          # 18: new version appended
    first_snapshot = json.dumps(fintech.draft_versions[0].to_dict())

    iv.submit_answer(_q(iv, "target_geographies").question_id, "US; DACH",
                     temporal_context=bk.TEMPORAL_CURRENT)
    res2 = iv.generate_updated_draft(client=FakeDraftClient())
    assert len(fintech.draft_versions) == 2                          # 18
    assert json.dumps(fintech.draft_versions[0].to_dict()) == first_snapshot   # 19: unchanged
    assert fintech.draft_versions[0] is not fintech.draft_versions[1]

    for res in (res1, res2):
        assert res.generated_icp.metadata.status == gi.STATUS_DRAFT  # 20: never approved
        val = res.validation_result                                  # 21: IQS returned honestly
        assert isinstance(val.is_valid, bool)
        assert isinstance(val.completeness_score, int)
        assert val.is_valid == (not val.blocking_errors)
    # the newest draft reflects the answers given during the interview
    assert "DACH" in fintech.draft_versions[1].to_json()


# 22. Mock/offline wording behavior is deterministic.
def test_wording_client_is_deterministic_and_offline_safe():
    port, fintech, _ = _portfolio()
    deterministic = [(q.question_id, q.question_text) for q in _iv(port, fintech).session.questions]
    for _ in range(2):
        iv = _iv(port, fintech, wording_client=ki.MockInterviewWordingClient())
        assert [(q.question_id, q.question_text) for q in iv.session.questions] == deterministic
    # a broken/garbage client silently falls back to the deterministic wording
    iv_bad = _iv(port, fintech, wording_client=BadWordingClient())
    assert [(q.question_id, q.question_text) for q in iv_bad.session.questions] == deterministic
    # a model may only reword — it can never add, drop or re-rank questions
    class RewordClient:
        model = "reword"

        def complete(self, system, user, *, structured=False):
            data = json.loads(user)
            return json.dumps({"questions": [
                {"question_id": q["question_id"], "question_text": "Reworded: " + q["question_text"]}
                for q in data["questions"]] + [{"question_id": "made-up", "question_text": "invented"}]
            }), {}
    iv_rw = _iv(port, fintech, wording_client=RewordClient())
    assert [q.question_id for q in iv_rw.session.questions] == [qid for qid, _ in deterministic]
    assert all(q.question_text.startswith("Reworded: ") for q in iv_rw.session.questions)
    # offline default: no API key -> deterministic mock, never a live client
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        client, is_live = ki.get_wording_client()
        assert isinstance(client, ki.MockInterviewWordingClient) and is_live is False
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_starting_criteria_and_answer_validation():
    port, fintech, _ = _portfolio()
    iv = ki.KnowledgeInterview.for_project(port.company, fintech)
    assert iv.should_start()                                     # gaps exist
    iv.start()
    # an answer must be valid; Python is the authority
    q = _q(iv, "company_size_preferences")
    assert not iv.submit_answer(q.question_id, "loads of people").ok
    assert iv.submit_answer(q.question_id, "50-500").ok
    # a temporally-sensitive answer must classify itself explicitly
    tq = _q(iv, "target_geographies")
    bad = iv.submit_answer(tq.question_id, "US")
    assert not bad.ok and "temporal" in bad.error.lower()


# ---------------------------------------------------------------------------
# Sprint 5.3.1 — hardening
# ---------------------------------------------------------------------------

# 1. Not-applicable semantics: explicit allowlist, effective gap state, no dead end.

def test_allowed_not_applicable_closes_the_gap():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    q = _q(iv, "best_customer_examples")
    assert any(g.field == "best_customer_examples" for g in iv.effective_gap_report().important_gaps)
    res = iv.not_applicable(q.question_id, note="brand-new segment, no customers yet")
    assert res.ok
    # closed in the EFFECTIVE state the interview acts on...
    assert not any(g.field == "best_customer_examples"
                   for g in iv.effective_gap_report().important_gaps)
    # ...while the raw detector is untouched and still honest about the missing knowledge
    assert any(g.field == "best_customer_examples" for g in iv.gap_report().important_gaps)
    # ...and completeness is not inflated: N/A adds no knowledge
    assert iv.effective_gap_report().completeness_score == iv.gap_report().completeness_score
    assert iv.session.current_gap_summary["not_applicable_gaps"] == 1


def test_disallowed_not_applicable_is_rejected():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    for gap_field in ("target_company", "buyer_roles"):
        q = _q(iv, gap_field)
        res = iv.not_applicable(q.question_id, note="does not apply")
        assert not res.ok and res.error
        assert iv.session.get(q.question_id).status == ki.PENDING     # still asked
        assert q.question_id not in fintech.not_applicable            # nothing recorded
        assert any(g.field == gap_field for g in iv.effective_gap_report().blocking_gaps)
    # a conflict can never be "not applicable" — one of the recorded values must be chosen
    _make_project_conflict(fintech)
    iv2 = _iv(port, fintech)
    cq = next(q for q in iv2.session.questions if q.source_type == ki.SRC_CONFLICT)
    assert not iv2.not_applicable(cq.question_id).ok


def test_not_applicable_creates_no_knowledge_item():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    before = [i.knowledge_id for i in fintech.project_knowledge.knowledge_items]
    iv.not_applicable(_q(iv, "target_geographies").question_id, note="we sell worldwide")
    assert [i.knowledge_id for i in fintech.project_knowledge.knowledge_items] == before
    assert not fintech.project_knowledge.field("target_geographies")
    # the record is an audited marker, not a business fact
    rec = list(fintech.not_applicable.values())[0]
    assert rec["gap_field"] == "target_geographies" and rec["note"] == "we sell worldwide"
    assert rec["recorded_at"] and rec["question_text"]


def test_not_applicable_does_not_leak_to_another_project():
    port, fintech, healthcare = _portfolio()
    iv = _iv(port, fintech)
    iv.not_applicable(_q(iv, "best_customer_examples").question_id, note="none yet")
    assert healthcare.not_applicable == {}
    iv_h = _iv(port, healthcare)
    # Healthcare is still asked the question FinTech marked not applicable
    assert _q(iv_h, "best_customer_examples").status == ki.PENDING
    assert any(g.field == "best_customer_examples"
               for g in iv_h.effective_gap_report().important_gaps)


def test_session_completes_when_remaining_blocking_gap_is_legitimately_na():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    for gap_field, value in (("target_company", "FinTech"), ("buyer_roles", "CTO")):
        iv.submit_answer(_q(iv, gap_field).question_id, value, temporal_context=bk.TEMPORAL_CURRENT)
    # the one blocking gap left is hard exclusions, which may legitimately be "none declared"
    remaining = [g.field for g in iv.effective_gap_report().blocking_gaps]
    assert remaining == ["target_vs_exclusion"]
    assert iv.session.status != ki.COMPLETED
    res = iv.not_applicable(_q(iv, "target_vs_exclusion").question_id,
                            note="we declare no hard exclusions")
    assert res.ok
    assert iv.session.current_gap_summary["blocking_gaps"] == 0
    assert iv.session.status == ki.COMPLETED          # no dead end


def test_legitimate_na_question_is_never_re_asked():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    q = _q(iv, "best_customer_examples")
    iv.not_applicable(q.question_id, note="none yet")
    assert q.question_id not in [p.question_id for p in iv.session.pending()]
    iv.refresh()
    assert q.question_id not in [p.question_id for p in iv.session.pending()]
    # ...and the decision survives a brand-new session, still visible for audit
    iv2 = _iv(port, fintech)
    assert iv2.session.get(q.question_id).status == ki.Q_NOT_APPLICABLE
    assert q.question_id not in [p.question_id for p in iv2.session.pending()]


# 2. Multi-value write-back: one KnowledgeItem per value.

def test_multi_value_answer_writes_one_item_per_value():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "buyer_roles").question_id,
                           "CTO; VP Engineering; Head of Platform",
                           temporal_context=bk.TEMPORAL_CURRENT)
    assert res.ok and len(res.written_item_ids) == 3
    buyers = fintech.project_knowledge.get_items(category="buyer")
    assert sorted(i.value for i in buyers) == ["CTO", "Head of Platform", "VP Engineering"]
    assert all(i.value.count(";") == 0 for i in buyers)          # no joined blob
    for i in buyers:                                             # same provenance on each item
        assert i.origin == bk.ORIGIN_USER and i.status == bk.CONFIRMED
        assert i.user_confirmed and i.confidence == 1.0
        assert any(res.question_id in n for n in i.notes)
    assert not fintech.project_knowledge.conflicts               # a list is not a contradiction


def test_repeated_multi_value_submission_does_not_duplicate_or_conflict():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO; VP Engineering",
                     temporal_context=bk.TEMPORAL_CURRENT)
    # answer the same question again (it re-opens only if the gap re-opens; force a second write)
    q2 = ki.InterviewQuestion(question_id="x", project_id=fintech.project_id,
                              source_type=ki.SRC_BLOCKING, category="buyer", attribute="role",
                              question_text="again", gap_field="buyer_roles",
                              temporal_required=True)
    iv._write_fact(q2, ["CTO", "cto", "Head of Platform"], bk.TEMPORAL_CURRENT, "")
    values = sorted(i.value for i in fintech.project_knowledge.get_items(category="buyer")
                    if i.is_active)
    assert values == ["CTO", "Head of Platform", "VP Engineering"]   # deduped by normalized value
    assert not fintech.project_knowledge.conflicts                    # never a self-conflict


def test_each_multi_value_item_is_independently_curatable():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO; VP Engineering",
                           temporal_context=bk.TEMPORAL_CURRENT)
    cto_id, vp_id = res.written_item_ids
    fintech.project_knowledge.reject_item(vp_id, note="not a buyer after all")
    fintech.project_knowledge.edit_item(cto_id, value="Chief Technology Officer")
    active = [i.value for i in fintech.project_knowledge.get_items(category="buyer") if i.is_active]
    assert active == ["Chief Technology Officer"]                # one value edited, the other gone
    assert "VP Engineering" in [i.value for i in fintech.project_knowledge.knowledge_items]  # audit


def test_promoting_one_value_does_not_promote_the_others():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    res = iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO; VP Engineering",
                           temporal_context=bk.TEMPORAL_CURRENT)
    cto_id = next(k for k in res.written_item_ids
                  if fintech.project_knowledge._get(k).value == "CTO")
    iv.promote_answer(res.question_id, knowledge_id=cto_id)
    company_buyers = [i.value for i in port.company.get_items(category="buyer")]
    assert company_buyers == ["CTO"]                              # only the selected value
    assert "VP Engineering" not in company_buyers
    # both remain in the project (copy, not move)
    assert sorted(i.value for i in fintech.project_knowledge.get_items(category="buyer")) == \
        ["CTO", "VP Engineering"]


def test_composed_knowledge_unions_multi_values_correctly():
    port, fintech, healthcare = _portfolio()
    iv = _iv(port, fintech)
    iv.submit_answer(_q(iv, "buyer_roles").question_id, "CTO; VP Engineering",
                     temporal_context=bk.TEMPORAL_CURRENT)
    # the company knows CTO company-wide; the project also named CTO plus its own VP Engineering
    port.company.add_item("buyer", "role", "CTO", status=bk.CONFIRMED, evidence_excerpt="CTO")
    fin = iv.composed()
    assert sorted(fin.buyer_roles) == ["CTO", "VP Engineering"]   # union, CTO de-duplicated once
    assert fin.buyer_roles.count("CTO") == 1
    heal = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert heal.buyer_roles == ["CTO"]                            # company value only; no leak


# 3. Qualification-dimensions boundary: owned by Strategy Review, never asked here.

def test_interview_never_asks_for_scoring_configuration():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech, include_optional=True)
    for q in iv.session.questions:
        assert q.gap_field != "qualification_dimensions"
        blob = f"{q.question_text} {q.help_text}".lower()
        for term in ("weight", "threshold", "priority band", "scoring strategy", "score band"):
            assert term not in blob, f"{q.question_id} asks about {term}"
    assert "qualification_dimensions" not in ki._GAP_SPECS
    assert "qualification_dimensions" in ki.STRATEGY_GAP_FIELDS


def test_target_and_buyer_answers_do_not_satisfy_scoring_configuration():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    before = iv.strategy_requirements()
    assert len(before) == 1 and before[0].status == "unresolved"
    for gap_field, value in (("target_company", "FinTech"), ("buyer_roles", "CTO")):
        iv.submit_answer(_q(iv, gap_field).question_id, value, temporal_context=bk.TEMPORAL_CURRENT)
    # the raw detector auto-closes its qualification_dimensions gap once targets exist...
    assert not any(g.field == "qualification_dimensions" for g in iv.gap_report().blocking_gaps)
    # ...but the strategy requirement is NOT satisfied by knowledge answers.
    after = iv.strategy_requirements()
    assert len(after) == 1 and after[0].status == "unresolved"
    assert after[0].gap_field == "qualification_dimensions"
    assert iv.session.current_gap_summary["strategy_requirements"] == 1


def test_interview_completes_without_strategy_configuration():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    _answer_blocking(iv)
    assert iv.session.status == ki.COMPLETED
    assert iv.strategy_requirements()[0].status == "unresolved"   # still handed on, unresolved
    # qualification_dimensions never appears in what the interview owns, at any point
    assert not any(g.field == "qualification_dimensions"
                   for g in iv.effective_gap_report().blocking_gaps)


def test_strategy_requirement_is_exposed_for_sprint_5_4():
    port, fintech, _ = _portfolio()
    iv = _iv(port, fintech)
    req = iv.strategy_requirements()[0]
    assert req.owner == "strategy_review"
    assert req.project_id == fintech.project_id
    assert req.requirement_id.endswith(":strategy:qualification_dimensions")
    assert req.title and req.reason
    assert isinstance(req.to_dict(), dict)


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
