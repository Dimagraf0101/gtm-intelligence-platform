"""Canonical operational priority policy (Sprint 12.0.2).

The single source of truth for the standard operational priority bands. This is a deterministic,
**immutable policy leaf**: it imports nothing else from the pipeline, so both the decision engine
(`decision`) and the ICP-generation defaults (`generated_icp.standard_priority_bands`) can derive from
it with no circular dependency. Dependency direction:

    priority_policy (this leaf) → decision engine → ICP generation defaults → UI / export

`operational_priority` (in `decision`) is the sole business authority for qualification
(Priority 1-5 / Disqualified). An ICP artifact's own `category_thresholds` remain an independent,
author-controlled **audit** concern (`internal_category`) and are NOT governed by this policy after
authoring — only the *default generated* bands derive from it. This module never mutates and cannot be
mutated by callers (all exported structures are tuples).
"""
from __future__ import annotations

# Standard operational bands as (inclusive minimum score, priority label), highest first. A score at or
# above a band's minimum — and below the next higher band's minimum — takes that band's label; a score
# below the lowest minimum is Disqualified. Immutable (a tuple of tuples): never mutate it. Callers that
# need a mutable structure build a fresh one (see ``default_priority_band_rows`` /
# ``generated_icp.standard_priority_bands``).
OPERATIONAL_PRIORITY_BANDS = (
    (90, "Priority 1"),
    (75, "Priority 2"),
    (60, "Priority 3"),
    (45, "Priority 4"),
    (30, "Priority 5"),
)

# Terminal label for any score below the lowest operational band (i.e. < 30).
DISQUALIFIED_LABEL = "Disqualified"

# The full score range the default bands cover (used to derive band max_scores for audit structures).
MIN_SCORE = 0
MAX_SCORE = 100


def default_priority_band_rows() -> tuple:
    """Derive the full default band table as immutable ``(label, min_score, max_score)`` rows, highest
    first and including the terminal Disqualified band, covering ``MIN_SCORE..MAX_SCORE`` with no gaps.

    This is the canonical derivation the ICP-generation defaults consume. It is pure and returns a
    tuple (immutable), so the policy can never be mutated through it; callers that need mutable domain
    objects (e.g. ``PriorityBand`` dataclasses) construct fresh ones from these rows.
    """
    rows = []
    upper = MAX_SCORE
    for minimum, label in OPERATIONAL_PRIORITY_BANDS:
        rows.append((label, minimum, upper))
        upper = minimum - 1
    rows.append((DISQUALIFIED_LABEL, MIN_SCORE, upper))   # terminal band: 0 .. (lowest_min - 1)
    return tuple(rows)
