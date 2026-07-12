"""Sprint 4.1D.1 — Real API Business Knowledge Extraction Pilot (single run).

Harness only (not product). Uses the production extraction flow with the real Anthropic client,
prompt caching, structured output (with tolerant fallback). Writes a timestamped output folder under
outputs/pilots/. Runs exactly once.

    PYTHONIOENCODING=utf-8 ./.venv/bin/python scripts/pilot_4_1_d_1.py
"""
from __future__ import annotations

import sys
import csv
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import config  # noqa: F401,E402  (loads .env so ANTHROPIC_API_KEY is available)
import source_package as sp        # noqa: E402
import business_knowledge as bk    # noqa: E402
import knowledge_extractor as ke   # noqa: E402

# --- Haiku 4.5 pricing ($/MTok) ---
PRICE_IN, PRICE_OUT, PRICE_CACHE_READ, PRICE_CACHE_WRITE = 1.00, 5.00, 0.10, 1.25

# Selected local materials (filename, category).
INPUTS = [
    ("icp/Innotechfy_Fintech_Playbook.pdf", "service_catalogue"),
    ("icp/Innotechfy_Xamarin_Playbook.pdf", "portfolio"),
    ("icp/AI.pdf", "company_presentation"),
]


def _cost(r) -> float:
    return (r.input_tokens * PRICE_IN + r.output_tokens * PRICE_OUT
            + r.cache_read_tokens * PRICE_CACHE_READ
            + r.cache_write_tokens * PRICE_CACHE_WRITE) / 1_000_000


def _classify(item, conflict_ids: set) -> str:
    if item.knowledge_id in conflict_ids:
        return "conflict"
    if len(item.source_references) > 1:
        return "duplicate-merged"
    if item.status == bk.CONFIRMED:
        return "correct confirmed fact"
    if item.status == bk.UNKNOWN:
        return "unknown"
    if item.category in ke.INTERPRETIVE_CATEGORIES:
        return "correct proposed interpretation"
    if not item.evidence_excerpt:
        return "unsupported/proposed"
    return "proposed (source-backed, interpretive gate)"


def main() -> int:
    client, is_live = ke.get_client()
    if not is_live or isinstance(client, ke.MockKnowledgeClient):
        print("ABORT: real Anthropic client not active (ANTHROPIC_API_KEY missing). No run performed.")
        return 1

    files = [(Path(p).name, Path(ROOT / p).read_bytes(), cat) for p, cat in INPUTS]
    pkg = sp.build_package_from_files(files)
    batches = ke._build_batches(pkg, char_budget=ke.BATCH_CHAR_BUDGET)

    # ---- Pre-run report ----
    print("=" * 72)
    print("SPRINT 4.1D.1 — BUSINESS KNOWLEDGE EXTRACTION PILOT (pre-run)")
    print("=" * 72)
    total_chars = 0
    for doc in pkg.source_documents:
        total_chars += doc.extracted_character_count
        print(f"  {doc.filename:44} type={doc.file_type:8} "
              f"cat={doc.source_category:20} chars={doc.extracted_character_count}")
    print(f"  TOTAL extracted characters: {total_chars}")
    print(f"  Number of batches: {len(batches)}  "
          f"({[[d.filename for d in b] for b in batches]})")
    print(f"  Real client active: True   model={client.model}")
    print("  MockKnowledgeClient disabled: True")
    print("  Running extraction ONCE...\n")

    # ---- Single run ----
    result = ke.extract_business_knowledge(pkg, client=client, structured=True)
    knowledge = result.business_knowledge
    gap = result.gap_report
    cost = _cost(result)
    per_source = cost / max(1, result.source_count)

    # ---- Output folder (never overwrite) ----
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "outputs" / "pilots" / f"bk_extract_{stamp}"
    out.mkdir(parents=True, exist_ok=False)

    metrics = {k: getattr(result, k) for k in (
        "source_count", "successful_batches", "failed_batches", "retries", "parser_failures",
        "proposals_received", "proposals_accepted", "proposals_confirmed",
        "proposals_left_proposed", "proposals_rejected", "conflicts_created",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "model_calls", "model", "is_mock")}
    metrics["total_api_cost_usd"] = round(cost, 6)
    metrics["avg_cost_per_source_usd"] = round(per_source, 6)
    metrics["warnings"] = result.warnings

    (out / "results.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), "utf-8")
    (out / "business_knowledge.json").write_text(
        knowledge.to_json(), "utf-8")
    (out / "gap_report.json").write_text(
        json.dumps(gap.to_dict(), indent=2, ensure_ascii=False), "utf-8")

    # ---- manual_review.csv ----
    conflict_ids = {i for c in knowledge.conflicts for i in c.item_ids}
    with (out / "manual_review.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "attribute", "value", "status", "confidence",
                    "source_filename", "evidence_excerpt", "source_reference",
                    "python_decision", "classification"])
        for it in knowledge.knowledge_items:
            ref = it.source_references[0] if it.source_references else None
            excerpt = (it.evidence_excerpt or "").replace("\n", " ")[:120]
            w.writerow([
                it.category, it.attribute, (it.value or "")[:120], it.status,
                f"{it.confidence:.2f}", ref.filename if ref else "",
                excerpt, (ref.section_index if ref else ""),
                it.status, _classify(it, conflict_ids)])

    # ---- hard safety checks ----
    confirmed = [i for i in knowledge.knowledge_items if i.status == bk.CONFIRMED]
    safety = {
        "no_confirmed_without_excerpt": all(i.evidence_excerpt for i in confirmed),
        "no_confirmed_buyer_roles": not any(i.category in ("buyer", "excluded_buyer")
                                            for i in confirmed),
        "no_confirmed_hard_exclusions": not any(i.category == "hard_exclusion_candidate"
                                                for i in confirmed),
        "no_confirmed_interpretive": not any(i.category in ke.INTERPRETIVE_CATEGORIES
                                             for i in confirmed),
    }
    # re-verify each confirmed excerpt against its source text
    idx = {d.filename.lower(): d for d in pkg.source_documents}
    excerpt_ok = True
    for i in confirmed:
        ref = i.source_references[0] if i.source_references else None
        doc = idx.get(ref.filename.lower()) if ref else None
        if not (doc and i.evidence_excerpt.strip().lower() in doc.extracted_text.lower()):
            excerpt_ok = False
    safety["confirmed_excerpts_reverified"] = excerpt_ok
    all_safe = all(safety.values())

    # ---- summary.txt (no full document content) ----
    by_status: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for it in knowledge.knowledge_items:
        by_status[it.status] = by_status.get(it.status, 0) + 1
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
    lines = [
        "SPRINT 4.1D.1 — BUSINESS KNOWLEDGE EXTRACTION PILOT",
        f"timestamp: {stamp}",
        f"model: {result.model}   is_mock: {result.is_mock}",
        "",
        "INPUT FILES:",
    ]
    for doc in pkg.source_documents:
        lines.append(f"  - {doc.filename} ({doc.file_type}, {doc.source_category}, "
                     f"{doc.extracted_character_count} chars)")
    lines += [
        f"  total chars: {total_chars}   batches: {len(batches)}",
        "",
        "METRICS:",
        f"  source_count={result.source_count} successful_batches={result.successful_batches} "
        f"failed_batches={result.failed_batches} retries={result.retries} "
        f"parser_failures={result.parser_failures}",
        f"  proposals received={result.proposals_received} accepted={result.proposals_accepted} "
        f"confirmed={result.proposals_confirmed} left_proposed={result.proposals_left_proposed} "
        f"rejected={result.proposals_rejected} conflicts={result.conflicts_created}",
        f"  tokens in={result.input_tokens} out={result.output_tokens} "
        f"cache_read={result.cache_read_tokens} cache_write={result.cache_write_tokens} "
        f"model_calls={result.model_calls}",
        f"  cost=${cost:.6f}  avg_per_source=${per_source:.6f}",
        "",
        f"KNOWLEDGE ITEMS: {len(knowledge.knowledge_items)}  by_status={by_status}",
        f"  by_category={by_cat}",
        f"  unresolved_conflicts={len(knowledge.conflicts)}",
        "",
        "GAP REPORT:",
        f"  ready_for_icp_generation={gap.is_ready_for_icp_generation} "
        f"completeness={gap.completeness_score}",
        f"  blocking={len(gap.blocking_gaps)} important={len(gap.important_gaps)} "
        f"optional={len(gap.optional_gaps)}",
        "",
        "HARD SAFETY CHECKS:",
    ]
    for k, v in safety.items():
        lines.append(f"  [{'PASS' if v else 'FAIL'}] {k}")
    lines += ["", f"PILOT RESULT: {'PASS' if all_safe and result.failed_batches == 0 else 'REVIEW'}"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", "utf-8")

    # ---- console ----
    print("\n".join(lines))
    print(f"\nOutputs written to: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
