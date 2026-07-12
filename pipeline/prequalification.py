"""Deterministic Python pre-qualification (Release 0.3, Sprint 3.6).

Decide, without any model call, whether a lead can be **safely and directly disqualified** by
deterministic rules using explicit ICP exclusions + confirmed current-employment evidence — or
whether it needs semantic judgment and must go to Claude.

Core safety principle — a lead may bypass Claude only when:
  1. the ICP contains a clear applicable exclusion;
  2. the source data has direct, confirmed CURRENT evidence;
  3. there is no relevant conflicting evidence;
  4. the rule is deterministic;
  5. the reason is auditable.
Missing information never triggers pre-disqualification. Ambiguous classification always goes to
Claude. Previous employment never triggers a current-company pre-disqualification.

Commercial rules are ICP-specific (read from the active ``ICPProfile``); no FinTech rule is
hardcoded globally. Pure Python — no network, no LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from icp_profile import ICPProfile, parse_employee_range  # noqa: E402  (consumed, not modified)
from evidence import CONFLICTING                            # noqa: E402


@dataclass
class PrequalResult:
    should_call_model: bool
    is_disqualified: bool
    reason: Optional[str] = None
    matched_rule: Optional[str] = None          # e.g. "universal:duplicate", "icp:employee_range"
    evidence_attribute: Optional[str] = None
    evidence_source_field: Optional[str] = None
    evidence_value: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    confidence: str = "n/a"                     # "high" for a deterministic disqualification
    current_employment_only: bool = True        # only current/person-level evidence is ever used

    def to_dict(self) -> dict:
        return asdict(self)


def lead_identifier(lead) -> Optional[str]:
    """Normalized LinkedIn slug used for duplicate/identity checks."""
    u = (lead.fields.get("linkedin_url") or "").strip().lower().split("?")[0].rstrip("/")
    m = re.search(r"linkedin\.com/in/([^/]+)", u)
    return m.group(1) if m else None


def _word(needle: str, haystack: str) -> bool:
    return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None


# ---------------------------------------------------------------------------
# ICP-specific checks (each returns (reason, rule, attribute, source_field, value) or None)
# ---------------------------------------------------------------------------

def _hard_size_exclusion(profile: ICPProfile) -> tuple[Optional[int], Optional[int]]:
    """Parse EXPLICIT hard employee-size exclusions from the ICP's hard-exclusion list only.

    Returns (reject_below, reject_above): reject a lead whose size is entirely below ``reject_below``
    or entirely above ``reject_above``. A **target / preferred** size range is NOT a hard exclusion
    and is deliberately ignored — the target range never triggers a pre-disqualification. Nothing is
    inferred: only clauses in ``profile.hard_exclusions`` that explicitly compare an employee/size
    count are used (engineer-headcount clauses are never mixed in)."""
    reject_below = reject_above = None
    for excl in profile.hard_exclusions:
        for clause in re.split(r"[·;,]| and ", str(excl).lower()):
            if not re.search(r"employe|staff|headcount|company size|\bsize\b|\bpeople\b", clause):
                continue
            if "engineer" in clause:                         # a different metric — never mix it in
                continue
            for m in re.finditer(r"(?:<|under|below|fewer than|less than)\s*(\d{1,7})", clause):
                n = int(m.group(1))
                reject_below = n if reject_below is None else max(reject_below, n)
            for m in re.finditer(r"(?:>|over|above|more than|greater than)\s*(\d{1,7})", clause):
                n = int(m.group(1))
                reject_above = n if reject_above is None else min(reject_above, n)
    return reject_below, reject_above


def _lead_size(f: dict) -> tuple[Optional[int], Optional[int]]:
    size_range = (f.get("company_size_range") or "").strip()
    count = (f.get("employee_count") or "").strip()
    if size_range:
        er = parse_employee_range(size_range)
        return er.low, er.high
    if count:
        digits = re.sub(r"\D", "", count)
        if digits:
            n = int(digits)
            return n, n
    return None, None


def _check_employee_range(profile: ICPProfile, f: dict, ev_index: dict):
    # Only an EXPLICIT hard size exclusion may reject — the target/preferred range never does.
    reject_below, reject_above = _hard_size_exclusion(profile)
    if reject_below is None and reject_above is None:
        return None                                          # no explicit hard rule -> go to Claude
    # conflicting size evidence must NOT reject (rule 3)
    if any(it.status == CONFLICTING for it in ev_index.get("company_size", [])):
        return None
    lo, hi = _lead_size(f)
    if lo is None and hi is None:
        return None                                          # missing size -> go to Claude
    below = reject_below is not None and hi is not None and hi < reject_below
    above = reject_above is not None and lo is not None and lo > reject_above
    if below or above:
        val = f.get("company_size_range") or f.get("employee_count") or ""
        bound = f"< {reject_below}" if below else f"> {reject_above}"
        return (f"Company size {val} triggers the ICP's explicit hard size exclusion "
                f"({bound} employees)", "icp:hard_employee_size",
                "company_size", "linkedin employees", val)
    return None                                              # overlaps / borderline -> go to Claude


def _check_geography(profile: ICPProfile, f: dict):
    geos = [g.strip().lower() for g in profile.target_geographies if g.strip()]
    if not geos:
        return None
    loc = (f.get("location") or "").strip()
    if not loc:
        return None                                          # missing location -> go to Claude
    if any(g in loc.lower() for g in geos):
        return None                                          # an allowed geography is present
    return (f"Location '{loc}' is outside the ICP target geographies", "icp:geography",
            "location", "location", loc)


def _check_excluded(profile: ICPProfile, f: dict):
    excluded = [e.strip() for e in profile.excluded_company_types if e.strip()]
    if not excluded:
        return None
    industry = (f.get("industry") or "").strip()
    title = (f.get("job_title") or "").strip()
    company = (f.get("company") or "").strip()
    for e in excluded:
        el = e.lower()
        if industry and el == industry.lower():
            return (f"Current industry '{industry}' is an ICP-excluded company type",
                    "icp:excluded_industry", "company_industry", "linkedin industry", industry)
        if title and (el == title.lower() or _word(el, title.lower())):
            return (f"Current title '{title}' matches ICP-excluded title '{e}'",
                    "icp:excluded_title", "current_job_title", "job title", title)
        if company and _word(el, company.lower()):
            return (f"Current company '{company}' matches ICP-excluded type '{e}'",
                    "icp:excluded_company_type", "current_company", "company", company)
        if industry and _word(el, industry.lower()):
            return (f"Current industry '{industry}' matches ICP-excluded type '{e}'",
                    "icp:excluded_industry", "company_industry", "linkedin industry", industry)
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def prequalify(profile: ICPProfile, lead, evidence_items, *, seen_slugs=None,
               require_identifier: bool = True, require_current_employment: bool = True) -> PrequalResult:
    """Return a PrequalResult. ``should_call_model`` is False only for a deterministic disqualification."""
    f = lead.fields or {}
    warnings: list[str] = []

    def disq(reason, rule, attr=None, src=None, val=None):
        return PrequalResult(False, True, reason, rule, attr, src, val, warnings, "high", True)

    # --- universal validity (ICP-independent) ---
    if not f:
        return disq("Unusable / empty lead record", "universal:corrupt_record")
    slug = lead_identifier(lead)
    if require_identifier and not slug:
        return disq("Missing or invalid LinkedIn profile identifier", "universal:missing_identifier",
                    "linkedin_url", "linkedin url", f.get("linkedin_url", ""))
    if slug and seen_slugs is not None and slug in seen_slugs:
        return disq(f"Duplicate record (LinkedIn '{slug}' already processed in this input)",
                    "universal:duplicate", "linkedin_url", "linkedin url", f.get("linkedin_url", ""))
    if require_current_employment and not (f.get("job_title") or f.get("company")):
        return disq("No identifiable current employment", "universal:no_current_employment",
                    "current_job_title", "job title", "")

    # --- ICP-specific (explicit profile data + confirmed CURRENT evidence only) ---
    ev_index: dict[str, list] = {}
    for e in evidence_items:
        ev_index.setdefault(e.attribute, []).append(e)

    for check in (_check_employee_range(profile, f, ev_index),
                  _check_geography(profile, f),
                  _check_excluded(profile, f)):
        if check:
            return disq(*check)

    # nothing deterministic fired -> semantic qualification required
    return PrequalResult(should_call_model=True, is_disqualified=False, warnings=warnings,
                         confidence="n/a", current_employment_only=True)
