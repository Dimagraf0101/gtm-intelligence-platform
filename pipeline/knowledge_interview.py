"""Knowledge Interview Engine (Sprint 5.3).

Headless business logic for the targeted knowledge-gap interview on a single ICP Project. It answers
exactly one question: *"What knowledge is missing, conflicting, uncertain, or incomplete for this ICP
Project?"* — never scoring strategy, weights, thresholds, exclusion activation, approval, or outreach.

Flow (all deterministic Python; the LLM is optional and may only reword):

    selected ICP Project -> composed knowledge (Company + ICP) -> detect_gaps() -> interview plan
    -> ask one question -> validate -> write into ICP Knowledge -> recompute gaps -> repeat
    -> "Generate Updated Draft ICP" (a brand-new draft version)

Invariants:

* **Answers only ever write into the selected project's ICP Knowledge.** Company Knowledge is read
  (through the composed view) when planning questions, but is *never* mutated by an answer. The only
  path that touches Company Knowledge is the explicit, human-invoked ``promote_answer``.
* Composed knowledge is a read-only view (Sprint 5.2.1) and is never mutated.
* Conflict questions are raised only for conflicts owned by **this project's** ICP Knowledge, and are
  resolved with the existing ``resolve_conflict`` (contrary evidence is always retained). Core
  conflicts owned by Company Knowledge are *reported* (``company_core_conflicts``) so the user can
  resolve them in Knowledge Review — the interview never silently edits them.
* Completion is computed by Python from the **recomputed** gap report and conflict state, never from
  "all questions were clicked through".
* This module never edits a GeneratedICP; drafts are produced by the existing generator.

Reuses BusinessKnowledge / KnowledgeItem / ConflictRecord / detect_gaps / ICPProject /
ComposedProjectKnowledge / KnowledgeReviewWorkspace / generate_draft_icp. It duplicates none of them.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import business_knowledge as bk
import knowledge_gaps as kg
import icp_project as ip
import knowledge_review as kr

MODEL = "claude-haiku-4-5-20251001"

# --- vocabularies ------------------------------------------------------------

# where a question came from
SRC_CONFLICT = "conflict"
SRC_BLOCKING = "blocking_gap"
SRC_IMPORTANT = "important_gap"
SRC_UNKNOWN = "unknown"
SRC_TEMPORAL = "temporal_clarification"
SRC_OPTIONAL = "optional_gap"

# Deterministic ask-order. 1 = first. Optional questions are only planned when explicitly requested.
_PRIORITY = {SRC_CONFLICT: 1, SRC_BLOCKING: 2, SRC_IMPORTANT: 3, SRC_UNKNOWN: 3,
             SRC_TEMPORAL: 4, SRC_OPTIONAL: 5}

# answer types (a minimal useful set — not a generic form engine)
SHORT_TEXT = "short_text"
LONG_TEXT = "long_text"
SINGLE_SELECT = "single_select"
MULTI_SELECT = "multi_select"
YES_NO = "yes_no"
NUMERIC_RANGE = "numeric_range"
NOT_APPLICABLE = "not_applicable"

# question statuses
PENDING = "pending"
ANSWERED = "answered"
SKIPPED = "skipped"
Q_NOT_APPLICABLE = "not_applicable"

# session statuses
NOT_STARTED = "not_started"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
INCOMPLETE = "incomplete"

# Facts in these categories can refer to experience/markets over time, so an answer must classify its
# temporal context explicitly. Historical experience must never become a current ICP target.
TEMPORAL_SENSITIVE_CATEGORIES = ("customer", "industry", "subsegment", "service", "product",
                                 "capability", "technology", "buyer", "geography")

# Gaps owned by Strategy Review (Sprint 5.4), NOT by the Knowledge Interview (Sprint 5.3.1 fix).
# "qualification_dimensions" is a *scoring configuration* requirement — dimensions, weights and
# thresholds are a strategy decision, not knowledge. The interview never asks for it, it never gates
# interview completion, and it is never silently satisfied by target/buyer answers: it is handed to
# Strategy Review explicitly via ``strategy_requirements()``.
STRATEGY_GAP_FIELDS = frozenset({"qualification_dimensions"})

# Explicit allowlist of gap types that may legitimately be marked "not applicable" (Sprint 5.3.1
# fix). These are gaps where "there is deliberately none" is a valid, honest answer — e.g. the IQS
# lets Hard Exclusions be an explicit "none declared", and a brand-new segment genuinely has no
# customers yet. Everything else (company identity, what is sold, who is targeted, who buys) cannot
# "not apply": marking those N/A would be inventing a fact by omission, so it is rejected.
# A tiny, explicit table — deliberately not a rule engine.
NOT_APPLICABLE_ALLOWED = frozenset({
    "target_vs_exclusion",          # blocking: "no hard exclusions declared" is a deliberate answer
    "target_geographies",
    "company_size_preferences",
    "best_customer_examples",
    "excluded_buyer_roles",
    "evidence_rules",
    "unknown_fields",
    "lost_customer_examples",
    "competitors",
    "technologies",
    "subsegments",
    "commercial_constraints",
})

_COT_MARKERS = ("chain of thought", "chain-of-thought", "let's think", "let me think",
                "reasoning:", "step 1:", "my reasoning")


# --- deterministic gap -> question mapping -----------------------------------

@dataclass(frozen=True)
class _GapSpec:
    """Where a gap's answer is stored and how it is asked. Explicit table, no inference."""
    category: str
    attribute: str
    answer_type: str
    multi: bool = False               # answer is a list -> ONE KnowledgeItem per value
    attribute_from_answer: bool = False   # the answer names the attribute (declared unknown fields)
    help_text: str = ""


# Keyed by KnowledgeGapReport GapItem.field. A gap field with no entry produces NO question — the
# interview never invents a gap category or a place to store an answer.
_GAP_SPECS = {
    # blocking
    "product_or_service": _GapSpec(
        "service", "name", SHORT_TEXT, multi=True,
        help_text="What this ICP sells. List one or more services."),
    "company_context": _GapSpec(
        "company", "overview", LONG_TEXT,
        help_text="A short description of the company and what it offers."),
    "target_company": _GapSpec(
        "industry", "target", SHORT_TEXT, multi=True,
        help_text="The industries this ICP targets. Mark historical experience as historical."),
    "buyer_roles": _GapSpec(
        "buyer", "role", SHORT_TEXT, multi=True,
        help_text="Title families rather than exhaustive titles, e.g. 'CTO; VP Engineering'."),
    # NOTE: "qualification_dimensions" deliberately has NO spec — see STRATEGY_GAP_FIELDS. The
    # Knowledge Interview never asks about dimensions, weights, thresholds or priority bands.
    "target_vs_exclusion": _GapSpec(
        "hard_exclusion_candidate", "rule", SHORT_TEXT, multi=True,
        help_text="Candidate rejection rules. Recorded as candidates for later review — "
                  "answering here does not activate a hard exclusion."),
    # important
    "target_geographies": _GapSpec(
        "geography", "region", SHORT_TEXT, multi=True,
        help_text="Preferred regions. A preference, not a rejection."),
    "company_size_preferences": _GapSpec(
        "company_size", "preference", NUMERIC_RANGE,
        help_text="An employee range such as 50-500. A preference, not a hard exclusion."),
    "best_customer_examples": _GapSpec(
        "customer", "best_customer", SHORT_TEXT, multi=True,
        help_text="Name current best-fit customers. Past customers must be marked historical/former."),
    "excluded_buyer_roles": _GapSpec(
        "excluded_buyer", "role", SHORT_TEXT, multi=True,
        help_text="Buyer roles you never want to target."),
    "evidence_rules": _GapSpec(
        "evidence_rule", "rule", SHORT_TEXT, multi=True,
        help_text="What evidence must be present before a company counts as a match."),
    "unknown_fields": _GapSpec(
        "unknown", "", SHORT_TEXT, multi=True, attribute_from_answer=True,
        help_text="Fields that may stay unknown (or need enrichment) without penalizing a lead."),
    # optional (only planned when explicitly requested)
    "lost_customer_examples": _GapSpec(
        "customer", "lost_customer", SHORT_TEXT, multi=True,
        help_text="Customers lost or that turned out to be a poor fit."),
    "competitors": _GapSpec(
        "other", "competitor", SHORT_TEXT, multi=True, help_text="Main competitors."),
    "technologies": _GapSpec(
        "technology", "stack", SHORT_TEXT, multi=True,
        help_text="Technologies a target company should (or shouldn't) use."),
    "subsegments": _GapSpec(
        "subsegment", "focus", SHORT_TEXT, multi=True, help_text="Market subsegments you focus on."),
    "commercial_constraints": _GapSpec(
        "commercial_constraint", "rule", SHORT_TEXT, multi=True,
        help_text="Commercial constraints (budget, contract, region) to honor."),
}


def _temporal_required(category: str) -> bool:
    return category in TEMPORAL_SENSITIVE_CATEGORIES


# --- model -------------------------------------------------------------------

@dataclass
class InterviewQuestion:
    question_id: str = ""
    project_id: str = ""
    source_type: str = SRC_BLOCKING
    category: str = ""
    attribute: str = ""
    question_text: str = ""
    answer_type: str = SHORT_TEXT
    required: bool = False
    priority: int = 3
    related_item_ids: list = field(default_factory=list)
    related_conflict_id: Optional[str] = None
    suggested_options: list = field(default_factory=list)
    help_text: str = ""
    status: str = PENDING
    # operational (not part of the asked question): where the answer goes / what it produced
    gap_field: str = ""
    temporal_required: bool = False
    written_item_ids: list = field(default_factory=list)
    answer_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InterviewSession:
    session_id: str = ""
    project_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    status: str = NOT_STARTED
    questions: list = field(default_factory=list)
    current_question_id: Optional[str] = None
    answers_count: int = 0
    skipped_count: int = 0
    initial_gap_summary: dict = field(default_factory=dict)
    current_gap_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["questions"] = [q.to_dict() for q in self.questions]
        return d

    def pending(self) -> list:
        return [q for q in self.questions if q.status == PENDING]

    def get(self, question_id: str) -> InterviewQuestion:
        for q in self.questions:
            if q.question_id == question_id:
                return q
        raise KeyError(question_id)


@dataclass
class StrategyRequirement:
    """Something this ICP Project still needs that the Knowledge Interview deliberately does not own.

    Handed to Strategy Review (Sprint 5.4). It is *not* knowledge, so no answer, target or buyer can
    satisfy it, and it never gates Knowledge Interview completion.
    """
    requirement_id: str = ""
    project_id: str = ""
    gap_field: str = ""
    title: str = ""
    reason: str = ""
    owner: str = "strategy_review"
    status: str = "unresolved"          # nothing can resolve it until Strategy Review exists

    def to_dict(self) -> dict:
        return asdict(self)


def _strategy_review_complete(project: ip.ICPProject) -> bool:
    """True only when a complete Strategy Review exists for this project. Duck-typed and lazily
    imported so the Knowledge Interview keeps *not* owning strategy and there is no import cycle."""
    decisions = getattr(project, "strategy", None)
    if decisions is None:
        return False
    base = getattr(decisions, "based_on_draft", None)
    if base is None:
        return False
    import strategy_review as sr
    return sr.is_review_complete(decisions, base)


def strategy_requirements(project: ip.ICPProject) -> list:
    """The strategy-owned requirements for this project, exposed explicitly for Strategy Review.

    ``status`` is ``resolved`` only when a **complete** Strategy Review exists (Sprint 5.4);
    otherwise ``unresolved``. The Knowledge Interview never owns or satisfies this — no answer,
    target or buyer can resolve it."""
    status = "resolved" if _strategy_review_complete(project) else "unresolved"
    return [StrategyRequirement(
        requirement_id=f"{project.project_id}:strategy:qualification_dimensions",
        project_id=project.project_id,
        gap_field="qualification_dimensions",
        title="Qualification dimensions, weights and priority thresholds",
        reason="Scoring configuration is a strategy decision, not knowledge. The Knowledge Interview "
               "never asks for weights, thresholds, priority bands or scoring strategy; Strategy "
               "Review owns them.",
        status=status,
    )]


@dataclass
class AnswerResult:
    ok: bool = False
    error: str = ""
    question_id: str = ""
    written_item_ids: list = field(default_factory=list)
    resolved_conflict_id: Optional[str] = None
    gap_summary: dict = field(default_factory=dict)
    session_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- helpers -----------------------------------------------------------------

def _qid(project_id: str, source_type: str, key: str) -> str:
    """Stable question id: the same gap/conflict always maps to the same question across re-plans."""
    return f"{project_id}:{source_type}:{key}"


def _core_open_conflicts(knowledge: bk.BusinessKnowledge) -> list:
    return [c for c in knowledge.conflicts
            if c.status == bk.CONFLICT_UNRESOLVED and c.category in kg.CORE_CONFLICT_CATEGORIES]


def _first_active(store: bk.BusinessKnowledge, category: str, attribute: str):
    for it in store.knowledge_items:
        if it.is_active and it.category == category and it.attribute == attribute:
            return it
    return None


def _find_value(store: bk.BusinessKnowledge, category: str, attribute: str, value: str):
    """An active item in this scope with the same normalized value (de-duplication key)."""
    norm = bk._normalize_for(category, value)
    for it in store.knowledge_items:
        if it.is_active and it.category == category and it.attribute == attribute \
                and it.normalized_value == norm:
            return it
    return None


# --- not-applicable applicability policy (Sprint 5.3.1) ----------------------

def na_rejection_reason(question: "InterviewQuestion") -> str:
    """Why this question may NOT be marked not applicable — "" when it legitimately may be."""
    if question.source_type == SRC_CONFLICT:
        return ("A conflict cannot be 'not applicable' — two recorded values disagree, so one must "
                "be chosen. Every value stays on record either way.")
    if question.source_type == SRC_TEMPORAL:
        return ("Choose a temporal context instead — 'unknown' is a valid answer and keeps the fact "
                "out of current targets.")
    if question.gap_field not in NOT_APPLICABLE_ALLOWED:
        return (f"'{question.gap_field}' cannot be marked not applicable: it defines who you are, "
                "what you sell, or who you target, so leaving it out would invent a fact by "
                "omission. Answer it, or skip it to leave the gap open.")
    return ""


def _na_records(project: ip.ICPProject) -> list:
    return [r for r in project.not_applicable.values() if isinstance(r, dict)]


def _na_gap_fields(project: ip.ICPProject) -> set:
    """Gap fields this project has explicitly and *legitimately* marked not applicable."""
    return {r.get("gap_field") for r in _na_records(project)
            if r.get("gap_field") in NOT_APPLICABLE_ALLOWED}


def effective_gap_report(report, project: ip.ICPProject):
    """The gap report the Knowledge Interview acts on: ``detect_gaps()`` minus

    * gaps explicitly and legitimately marked **not applicable** for this project, and
    * gaps owned by **Strategy Review** (``STRATEGY_GAP_FIELDS``), which this interview never asks.

    Completeness is deliberately NOT raised — marking something not applicable adds no knowledge.
    The underlying ``detect_gaps()`` report is never modified; this is a derived view.
    """
    excluded = _na_gap_fields(project) | STRATEGY_GAP_FIELDS
    eff = kg.KnowledgeGapReport(
        blocking_gaps=[g for g in report.blocking_gaps if g.field not in excluded],
        important_gaps=[g for g in report.important_gaps if g.field not in excluded],
        optional_gaps=[g for g in report.optional_gaps if g.field not in excluded],
        unresolved_conflicts=list(report.unresolved_conflicts),
        completeness_score=report.completeness_score,
        section_completeness=dict(report.section_completeness),
        suggested_questions=list(report.suggested_questions),
        warnings=list(report.warnings))
    eff.is_ready_for_icp_generation = (len(eff.blocking_gaps) == 0)
    return eff


def _na_questions(project: ip.ICPProject) -> list:
    """Rebuild the not-applicable questions from the project's audit records, so a legitimately
    closed question stays visible (and is never re-asked) even in a brand-new session."""
    out = []
    for r in _na_records(project):
        out.append(InterviewQuestion(
            question_id=r.get("question_id", ""), project_id=project.project_id,
            source_type=r.get("source_type", SRC_IMPORTANT), category=r.get("category", ""),
            attribute=r.get("attribute", ""), question_text=r.get("question_text", ""),
            gap_field=r.get("gap_field", ""), priority=r.get("priority", 3),
            status=Q_NOT_APPLICABLE, answer_note=r.get("note", "")))
    return out


def _split_values(answer) -> list[str]:
    if isinstance(answer, (list, tuple)):
        raw = [str(a) for a in answer]
    else:
        raw = re.split(r"[;\n]|,(?![^()]*\))", str(answer))
    return [v.strip() for v in raw if str(v).strip()]


def _answer_notes(q: InterviewQuestion, note: str) -> list[str]:
    """Link the stored knowledge back to the question and any prior items it relates to."""
    notes = [f"Knowledge Interview answer [{q.question_id}]: {q.question_text}"]
    if q.related_item_ids:
        notes.append("Related knowledge items: " + ", ".join(q.related_item_ids))
    if note:
        notes.append(note)
    return notes


# --- deterministic plan ------------------------------------------------------

def build_plan(project: ip.ICPProject, gap_report, composed: bk.BusinessKnowledge, *,
               include_optional: bool = False, wording_client=None) -> list:
    """The deterministic interview plan: which questions exist, and in what order.

    Python alone decides existence, requiredness and order. Ask-order is:
    1) unresolved core conflicts, 2) blocking gaps, 3) important gaps (incl. declared unknowns),
    4) temporal clarification, 5) optional gaps (only when ``include_optional``).
    """
    questions: list[InterviewQuestion] = []

    # 1) core conflicts owned by THIS project's ICP Knowledge (company conflicts are reported, not
    #    asked — the interview never edits Company Knowledge).
    for c in _core_open_conflicts(project.project_knowledge):
        options = [str(v) for v in c.conflicting_values]
        questions.append(InterviewQuestion(
            question_id=_qid(project.project_id, SRC_CONFLICT, c.conflict_id),
            project_id=project.project_id, source_type=SRC_CONFLICT,
            category=c.category, attribute=c.attribute,
            question_text=f"Which value is correct for {c.attribute or c.category}?",
            answer_type=SINGLE_SELECT, required=True, priority=_PRIORITY[SRC_CONFLICT],
            related_item_ids=list(c.item_ids), related_conflict_id=c.conflict_id,
            suggested_options=options,
            help_text="Every value stays on record as evidence; your choice marks the preferred one."))

    # 2) blocking gaps
    for gap in gap_report.blocking_gaps:
        q = _gap_question(project, gap, SRC_BLOCKING)
        if q is not None:
            questions.append(q)

    # 3) important gaps (conflict-derived important gaps are already covered above)
    for gap in gap_report.important_gaps:
        if gap.current_status == "conflicting":
            continue
        source = SRC_UNKNOWN if gap.field == "unknown_fields" else SRC_IMPORTANT
        q = _gap_question(project, gap, source)
        if q is not None:
            questions.append(q)

    # 4) temporal clarification for this project's own facts of unknown temporal context
    for it in project.project_knowledge.knowledge_items:
        if not (it.is_active and it.has_value):
            continue
        if it.category not in TEMPORAL_SENSITIVE_CATEGORIES:
            continue
        if it.temporal_context != bk.TEMPORAL_UNKNOWN:
            continue
        questions.append(InterviewQuestion(
            question_id=_qid(project.project_id, SRC_TEMPORAL, it.knowledge_id),
            project_id=project.project_id, source_type=SRC_TEMPORAL,
            category=it.category, attribute=it.attribute,
            question_text=f"Is \"{it.value}\" ({it.category}) current for this ICP, or historical?",
            answer_type=SINGLE_SELECT, required=False, priority=_PRIORITY[SRC_TEMPORAL],
            related_item_ids=[it.knowledge_id], suggested_options=list(bk.TEMPORAL_CONTEXTS),
            help_text="Historical experience is never used as a current ICP target."))

    # 5) optional gaps — never asked by default
    if include_optional:
        for gap in gap_report.optional_gaps:
            q = _gap_question(project, gap, SRC_OPTIONAL)
            if q is not None:
                questions.append(q)

    questions.sort(key=lambda q: q.priority)      # stable: preserves the order built above
    return apply_wording(questions, wording_client)


def _gap_question(project: ip.ICPProject, gap, source_type: str) -> Optional[InterviewQuestion]:
    spec = _GAP_SPECS.get(gap.field)
    if spec is None:
        return None                              # unmapped gap: never invent a question
    return InterviewQuestion(
        question_id=_qid(project.project_id, source_type, gap.field),
        project_id=project.project_id, source_type=source_type,
        category=spec.category, attribute=spec.attribute,
        question_text=gap.suggested_question, answer_type=spec.answer_type,
        required=(source_type in (SRC_CONFLICT, SRC_BLOCKING)),
        priority=_PRIORITY[source_type], help_text=spec.help_text,
        gap_field=gap.field, temporal_required=_temporal_required(spec.category),
        related_item_ids=list(gap.related_knowledge_ids))


# --- answer validation -------------------------------------------------------

_RANGE_RE = re.compile(r"^\d+\s*(?:[-–]\s*\d+)?\+?$")


def _validate_answer(q: InterviewQuestion, answer) -> tuple:
    """(ok, error, values). Python is the authority on what a valid answer is."""
    if q.answer_type == YES_NO:
        if isinstance(answer, bool):
            return True, "", ["yes" if answer else "no"]
        s = str(answer).strip().lower()
        if s in ("yes", "true", "y"):
            return True, "", ["yes"]
        if s in ("no", "false", "n"):
            return True, "", ["no"]
        return False, "Answer must be yes or no.", []

    if q.answer_type == SINGLE_SELECT:
        s = str(answer).strip()
        if s not in q.suggested_options:
            return False, f"Answer must be one of: {', '.join(q.suggested_options)}.", []
        return True, "", [s]

    if q.answer_type == MULTI_SELECT:
        values = _split_values(answer)
        if not values:
            return False, "Select at least one option.", []
        bad = [v for v in values if v not in q.suggested_options]
        if bad:
            return False, f"Not a valid option: {', '.join(bad)}.", []
        return True, "", values

    if q.answer_type == NUMERIC_RANGE:
        s = str(answer).strip()
        if not _RANGE_RE.match(s):
            return False, "Enter a numeric range such as 50-500 (or a single number).", []
        return True, "", [s]

    # short_text / long_text
    spec = _GAP_SPECS.get(q.gap_field)
    if spec is not None and spec.multi:
        values = _split_values(answer)
    else:
        values = [str(answer).strip()] if str(answer).strip() else []
    if not values:
        return False, "An answer is required.", []
    return True, "", values


# --- optional LLM wording (Python validates everything) ----------------------

_WORDING_SYSTEM = (
    "You rephrase pre-written interview questions for a B2B ICP knowledge review so they read "
    "naturally. You may ONLY improve wording. You must not invent questions, change what is being "
    "asked, add new topics, or decide what is required. Return JSON: "
    '{"questions":[{"question_id":"...","question_text":"..."}]} and nothing else. '
    "Never include reasoning or explanations."
)


class MockInterviewWordingClient:
    """Deterministic offline wording client: echoes each question unchanged. Same input, same output,
    no network. Used whenever no API key is configured."""
    model = "mock-interview-wording"

    def complete(self, system, user, *, structured=False):
        data = json.loads(user)
        out = {"questions": [{"question_id": q["question_id"], "question_text": q["question_text"]}
                             for q in data.get("questions", [])]}
        return json.dumps(out), {"input_tokens": 0, "output_tokens": 0,
                                 "cache_read_tokens": 0, "cache_write_tokens": 0}


class InterviewWordingClient:
    """Real Anthropic client for wording only (optional). Same real/mock pattern as the other AI
    modules; the SDK is imported lazily so offline tests never need it."""

    def __init__(self, model: str = MODEL, api_key: Optional[str] = None):
        self.model = model
        self._api_key = api_key
        self._client = None

    def _ensure(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key \
                else anthropic.Anthropic()
        return self._client

    def complete(self, system, user, *, structured=False):
        client = self._ensure()
        msg = client.messages.create(
            model=self.model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        u = msg.usage
        return text, {"input_tokens": getattr(u, "input_tokens", 0),
                      "output_tokens": getattr(u, "output_tokens", 0),
                      "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0),
                      "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0)}


def get_wording_client():
    """(client, is_live). Offline-safe: falls back to the deterministic mock without an API key."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return MockInterviewWordingClient(), False
    return InterviewWordingClient(api_key=key), True


def apply_wording(questions: list, client) -> list:
    """Optionally let a model make the deterministic questions read more naturally.

    Python keeps full authority: only ``question_text`` can change, and only if the replacement is a
    sane short string. Anything else — a bad payload, an exception, an oversized or reasoning-like
    answer — silently keeps the deterministic wording. Never changes which questions exist, their
    category/attribute, requiredness, or order."""
    if client is None or not questions:
        return questions
    payload = {"questions": [{"question_id": q.question_id, "question_text": q.question_text,
                              "category": q.category, "attribute": q.attribute}
                             for q in questions]}
    try:
        raw, _usage = client.complete(_WORDING_SYSTEM, json.dumps(payload), structured=True)
        data = json.loads(raw)
        proposed = {d["question_id"]: d["question_text"] for d in data.get("questions", [])
                    if isinstance(d, dict) and "question_id" in d and "question_text" in d}
    except Exception:                                   # noqa: BLE001 - wording is best-effort only
        return questions
    by_id = {q.question_id: q for q in questions}
    for qid, text in proposed.items():
        q = by_id.get(qid)
        if q is None or not isinstance(text, str):
            continue
        t = " ".join(text.split())
        if not t or len(t) > 300 or any(m in t.lower() for m in _COT_MARKERS):
            continue
        q.question_text = t
    return questions


# --- the interview service ---------------------------------------------------

class KnowledgeInterview:
    """Targeted knowledge-gap interview over one selected ICP Project."""

    def __init__(self, company: bk.BusinessKnowledge, project: ip.ICPProject, *,
                 include_optional: bool = False, wording_client=None):
        self.company = company
        self.project = project
        self.include_optional = include_optional
        self.wording_client = wording_client
        self.session: Optional[InterviewSession] = None

    @classmethod
    def for_project(cls, company, project, **kw) -> "KnowledgeInterview":
        return cls(company, project, **kw)

    @classmethod
    def from_workspace(cls, workspace, **kw) -> "KnowledgeInterview":
        if workspace.project is None:
            raise ValueError("Select an ICP project before starting a Knowledge Interview.")
        return cls(workspace.company, workspace.project, **kw)

    # --- read models ---------------------------------------------------------

    def composed(self) -> bk.BusinessKnowledge:
        """Company Knowledge + this project's ICP Knowledge (read-only view)."""
        return ip.ComposedProjectKnowledge(self.company, self.project).composed()

    def gap_report(self, composed: Optional[bk.BusinessKnowledge] = None):
        """The raw ``detect_gaps()`` report — unfiltered, exactly as the rest of the platform sees it."""
        return kg.detect_gaps(composed if composed is not None else self.composed())

    def effective_gap_report(self, composed: Optional[bk.BusinessKnowledge] = None):
        """What this interview actually owns: the raw report minus legitimately not-applicable gaps
        and minus Strategy-Review-owned gaps. Completion is judged from this."""
        return effective_gap_report(self.gap_report(composed), self.project)

    def strategy_requirements(self) -> list:
        """Requirements handed to Strategy Review (Sprint 5.4); never satisfied by an answer here."""
        return strategy_requirements(self.project)

    def gap_summary(self) -> dict:
        composed = self.composed()
        report = effective_gap_report(kg.detect_gaps(composed), self.project)
        return _summary(report, composed, self.company, self.project)

    # --- lifecycle -----------------------------------------------------------

    def should_start(self) -> bool:
        """Starting criteria: at least one blocking gap, important gap, or unresolved core conflict
        (in the effective gap state). A user may also request optional clarification explicitly."""
        composed = self.composed()
        report = self.effective_gap_report(composed)
        return bool(report.blocking_gaps or report.important_gaps
                    or _core_open_conflicts(composed) or self.include_optional)

    def start(self) -> InterviewSession:
        composed = self.composed()
        report = self.effective_gap_report(composed)
        summary = _summary(report, composed, self.company, self.project)
        self.session = InterviewSession(
            session_id=bk._new_id("ivw"), project_id=self.project.project_id,
            started_at=bk._now(), updated_at=bk._now(),
            initial_gap_summary=dict(summary), current_gap_summary=dict(summary))
        self._replan(composed, report)
        self._evaluate(report, composed)
        return self.session

    def refresh(self) -> InterviewSession:
        """Recompute composed knowledge + gaps, re-plan, and re-evaluate completion."""
        if self.session is None:
            return self.start()
        composed = self.composed()
        report = self.effective_gap_report(composed)
        self._replan(composed, report)
        self._evaluate(report, composed)
        return self.session

    def current_question(self) -> Optional[InterviewQuestion]:
        if self.session is None or self.session.current_question_id is None:
            return None
        return self.session.get(self.session.current_question_id)

    # --- answering -----------------------------------------------------------

    def submit_answer(self, question_id: str, answer, *, temporal_context: Optional[str] = None,
                      note: str = "") -> AnswerResult:
        """Validate an answer and write it into the selected project's ICP Knowledge.

        Never writes to Company Knowledge and never auto-promotes. After an accepted answer the gap
        report and conflict state are recomputed."""
        q = self._require_session().get(question_id)
        if q.status != PENDING:
            return AnswerResult(ok=False, question_id=question_id,
                                error=f"Question is already {q.status}.")
        ok, err, values = _validate_answer(q, answer)
        if not ok:
            return AnswerResult(ok=False, question_id=question_id, error=err)

        resolved_conflict_id = None
        if q.source_type == SRC_CONFLICT:
            written, resolved_conflict_id, err = self._resolve_conflict(q, values[0], note)
            if err:
                return AnswerResult(ok=False, question_id=question_id, error=err)
        elif q.source_type == SRC_TEMPORAL:
            written = self._clarify_temporal(q, values[0], note)
        else:
            if q.temporal_required and temporal_context is None:
                return AnswerResult(
                    ok=False, question_id=question_id,
                    error="An explicit temporal context is required for this answer "
                          f"(one of: {', '.join(bk.TEMPORAL_CONTEXTS)}).")
            if temporal_context is not None and temporal_context not in bk.TEMPORAL_CONTEXTS:
                return AnswerResult(ok=False, question_id=question_id,
                                    error=f"Unknown temporal context '{temporal_context}'.")
            written = self._write_fact(q, values, temporal_context or bk.TEMPORAL_UNKNOWN, note)

        q.status = ANSWERED
        q.answer_note = note
        q.written_item_ids = written
        self.refresh()
        return AnswerResult(ok=True, question_id=question_id, written_item_ids=written,
                            resolved_conflict_id=resolved_conflict_id,
                            gap_summary=dict(self.session.current_gap_summary),
                            session_status=self.session.status)

    def skip(self, question_id: str, note: str = "") -> AnswerResult:
        """Leave the gap unresolved and invent nothing. A skipped blocking question keeps the
        session incomplete."""
        q = self._require_session().get(question_id)
        q.status = SKIPPED
        q.answer_note = note
        self.refresh()
        return AnswerResult(ok=True, question_id=question_id,
                            gap_summary=dict(self.session.current_gap_summary),
                            session_status=self.session.status)

    def not_applicable(self, question_id: str, note: str = "") -> AnswerResult:
        """Record an explicit, project-level 'not applicable' for a gap that legitimately may not
        apply (``NOT_APPLICABLE_ALLOWED``).

        Deterministic policy: only allowlisted gap types may be marked N/A — anything defining who
        you are, what you sell, or who you target is **rejected** with a clear error, because
        omitting it would invent a fact. An accepted N/A writes **no knowledge item**; it is an
        audited project-level marker that satisfies that gap in the *effective* gap state (and only
        that project's). Completion is still computed from the recomputed effective report."""
        q = self._require_session().get(question_id)
        if q.status != PENDING:
            return AnswerResult(ok=False, question_id=question_id,
                                error=f"Question is already {q.status}.")
        reason = na_rejection_reason(q)
        if reason:
            return AnswerResult(ok=False, question_id=question_id, error=reason)
        q.status = Q_NOT_APPLICABLE
        q.answer_note = note
        # Audit record: what was asked, which gap it closed, why, and when. Never a business fact.
        self.project.not_applicable[question_id] = {
            "question_id": question_id, "gap_field": q.gap_field, "source_type": q.source_type,
            "category": q.category, "attribute": q.attribute, "question_text": q.question_text,
            "priority": q.priority, "note": note, "recorded_at": bk._now(),
        }
        self.project.touch()
        self.refresh()
        return AnswerResult(ok=True, question_id=question_id,
                            gap_summary=dict(self.session.current_gap_summary),
                            session_status=self.session.status)

    # --- explicit, human-only promotion --------------------------------------

    def promote_answer(self, question_id: str, *, knowledge_id: Optional[str] = None,
                       move: bool = False, note: str = "") -> list:
        """Promote an answer's knowledge into Company Knowledge. **Human action only** — never called
        by the model and never part of submitting an answer.

        ``knowledge_id`` promotes exactly one of the answer's values (each selected value is its own
        item); omit it to promote every value the answer produced. Copies by default (the project
        keeps its item); pass ``move=True`` to relocate. Provenance is preserved and the human action
        is recorded as ``user_input`` by the Sprint 5.2 primitive."""
        q = self._require_session().get(question_id)
        if q.status != ANSWERED or not q.written_item_ids:
            raise ValueError("Only an answered question's knowledge can be promoted.")
        if knowledge_id is None:
            targets = list(q.written_item_ids)
        elif knowledge_id in q.written_item_ids:
            targets = [knowledge_id]
        else:
            raise ValueError(f"'{knowledge_id}' is not one of this answer's knowledge items.")
        promoted = [ip.promote_to_company(self.project, self.company, kid,
                                          note=note or f"Promoted from Knowledge Interview answer "
                                                       f"[{q.question_id}].",
                                          remove_from_project=move)
                    for kid in targets]
        if move:
            q.written_item_ids = [k for k in q.written_item_ids if k not in targets]
        self.refresh()
        return promoted

    # --- draft ---------------------------------------------------------------

    def generate_updated_draft(self, *, client=None, icp_name: Optional[str] = None,
                               user_notes: Optional[str] = None):
        """Generate a COMPLETELY NEW Draft ICP from the current composed knowledge and append it to
        the project's draft versions. Reuses the existing generator + review workspace: previous
        drafts are never mutated, the status stays Draft, and the IQS result is returned as-is."""
        workspace = kr.KnowledgeReviewWorkspace.for_project(self.company, self.project)
        return workspace.generate_draft(client=client, icp_name=icp_name or self.project.name,
                                        user_notes=user_notes)

    # --- internals -----------------------------------------------------------

    def _require_session(self) -> InterviewSession:
        if self.session is None:
            raise ValueError("Start the Knowledge Interview before answering.")
        return self.session

    def _replan(self, composed, report) -> None:
        fresh = build_plan(self.project, report, composed,
                           include_optional=self.include_optional,
                           wording_client=self.wording_client)
        existing = {q.question_id: q for q in self.session.questions}
        merged, seen = [], set()
        for q in fresh:
            prev = existing.get(q.question_id)
            merged.append(prev if (prev is not None and prev.status != PENDING) else q)
            seen.add(q.question_id)
        # Audit trail: questions already acted on whose gap has since closed stay visible.
        for qid, q in existing.items():
            if qid not in seen and q.status != PENDING:
                merged.append(q)
                seen.add(qid)
        # A legitimately not-applicable question is closed in the effective gap state, so it is not
        # re-planned above. Re-materialize it from the project record so it is never re-asked and
        # the decision stays visible, even in a brand-new session.
        for q in _na_questions(self.project):
            if q.question_id not in seen:
                merged.append(q)
                seen.add(q.question_id)
        self.session.questions = merged
        self.session.current_question_id = next((q.question_id for q in merged
                                                 if q.status == PENDING), None)

    def _evaluate(self, report, composed) -> None:
        """Completion is derived from the recomputed *effective* gap report and conflict state —
        never from the questions having been clicked through."""
        s = self.session
        s.current_gap_summary = _summary(report, composed, self.company, self.project)
        s.answers_count = sum(1 for q in s.questions if q.status == ANSWERED)
        s.skipped_count = sum(1 for q in s.questions if q.status == SKIPPED)
        if len(report.blocking_gaps) == 0 and len(_core_open_conflicts(composed)) == 0:
            s.status = COMPLETED
            s.completed_at = s.completed_at or bk._now()
        else:
            s.completed_at = None
            s.status = IN_PROGRESS if s.pending() else INCOMPLETE
        s.updated_at = bk._now()

    def _write_fact(self, q: InterviewQuestion, values: list, temporal_context: str,
                    note: str) -> list:
        """Add or update the ICP Knowledge item(s) for this answer. Project scope only."""
        store = self.project.project_knowledge
        spec = _GAP_SPECS[q.gap_field]
        notes = _answer_notes(q, note)
        written = []

        if spec.attribute_from_answer:                    # the answer names the attribute
            for v in values:
                item = store.add_item(q.category, v, v, status=bk.CONFIRMED, origin=bk.ORIGIN_USER,
                                      user_confirmed=True, confidence=1.0,
                                      temporal_context=temporal_context, notes=list(notes))
                written.append(item.knowledge_id)
            self.project.touch()
            return written

        if spec.multi:
            # ONE item per selected value, each independently confirmable / rejectable / editable /
            # promotable. De-duplicated by normalized value, so re-submitting the same value
            # re-confirms it instead of adding a twin. merge=False keeps BusinessKnowledge from
            # reading two *different* values of a list-valued attribute as a contradiction — the
            # interview must never manufacture a self-conflict out of the user's own answer.
            for v in values:
                existing = _find_value(store, q.category, q.attribute, v)
                if existing is not None:
                    written.append(self._reconfirm(store, existing, temporal_context, notes, note))
                    continue
                item = store.add_item(q.category, q.attribute, v, status=bk.CONFIRMED,
                                      origin=bk.ORIGIN_USER, user_confirmed=True, confidence=1.0,
                                      temporal_context=temporal_context, notes=list(notes),
                                      merge=False)
                written.append(item.knowledge_id)
            self.project.touch()
            return written

        # Single-valued fact: one item per (category, attribute) — a new answer updates it in place.
        existing = _first_active(store, q.category, q.attribute)
        if existing is not None:
            store.edit_item(existing.knowledge_id, value=values[0],
                            temporal_context=temporal_context, note=notes[0])
            written.append(self._reconfirm(store, existing, temporal_context, notes, note))
        else:
            item = store.add_item(q.category, q.attribute, values[0], status=bk.CONFIRMED,
                                  origin=bk.ORIGIN_USER, user_confirmed=True, confidence=1.0,
                                  temporal_context=temporal_context, notes=list(notes))
            written.append(item.knowledge_id)
        self.project.touch()
        return written

    @staticmethod
    def _reconfirm(store, item, temporal_context: str, notes: list, note: str) -> str:
        """Re-assert an existing project item as this human answer (same provenance as a fresh one)."""
        store.edit_item(item.knowledge_id, temporal_context=temporal_context, note=notes[0])
        store.confirm_item(item.knowledge_id, note=note)
        item.confidence = 1.0
        for n in notes[1:]:
            if n not in item.notes:
                item.notes.append(n)
        return item.knowledge_id

    def _clarify_temporal(self, q: InterviewQuestion, value: str, note: str) -> list:
        store = self.project.project_knowledge
        kid = q.related_item_ids[0]
        store.edit_item(kid, temporal_context=value, note=_answer_notes(q, note)[0])
        item = store.confirm_item(kid, note=note)
        item.confidence = 1.0
        self.project.touch()
        return [kid]

    def _resolve_conflict(self, q: InterviewQuestion, chosen_value: str, note: str) -> tuple:
        """Reuse BusinessKnowledge.resolve_conflict: the chosen value becomes preferred and every
        contrary value stays on record as evidence. Project-owned conflicts only."""
        store = self.project.project_knowledge
        rec = next((c for c in store.conflicts if c.conflict_id == q.related_conflict_id), None)
        if rec is None:
            return [], None, "That conflict no longer exists."
        preferred_id = None
        for iid in rec.item_ids:
            try:
                if store._get(iid).value == chosen_value:
                    preferred_id = iid
                    break
            except KeyError:
                continue
        if preferred_id is None:
            return [], None, f"'{chosen_value}' is not one of the conflicting values."
        store.resolve_conflict(rec.conflict_id, preferred_id,
                               note=note or f"Resolved in Knowledge Interview [{q.question_id}].")
        self.project.touch()
        return [preferred_id], rec.conflict_id, ""


def _summary(report, composed: bk.BusinessKnowledge, company: bk.BusinessKnowledge,
             project: ip.ICPProject) -> dict:
    """Gap/conflict snapshot for the session and the view. ``report`` is the *effective* report."""
    company_core = _core_open_conflicts(company)
    return {
        "blocking_gaps": len(report.blocking_gaps),
        "important_gaps": len(report.important_gaps),
        "optional_gaps": len(report.optional_gaps),
        "open_conflicts": len(report.unresolved_conflicts),
        "core_open_conflicts": len(_core_open_conflicts(composed)),
        # Core conflicts owned by Company Knowledge: reported here, resolved in Knowledge Review.
        "company_core_conflicts": len(company_core),
        # Gaps the user explicitly and legitimately marked not applicable (no knowledge written).
        "not_applicable_gaps": len(_na_gap_fields(project)),
        # Handed to Strategy Review; never satisfied by an answer here and never gates completion.
        "strategy_requirements": len(strategy_requirements(project)),
        "completeness": report.completeness_score,
        "is_ready_for_icp_generation": report.is_ready_for_icp_generation,
    }
