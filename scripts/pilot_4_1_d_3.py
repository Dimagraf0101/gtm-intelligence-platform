"""Sprint 4.1D.3 — Real API RE-VALIDATION of Business Knowledge Extraction (single run).

Harness only (no extraction code changes). Same 3 inputs as 4.1D.1, current hardened extractor/prompt,
real Anthropic client, prompt caching when naturally available. Writes a new timestamped folder under
outputs/pilots/. Runs exactly once.

    PYTHONIOENCODING=utf-8 ./.venv/bin/python scripts/pilot_4_1_d_3.py
"""
from __future__ import annotations

import sys
import csv
import json
import time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import config  # noqa: F401,E402  (loads .env)
import source_package as sp        # noqa: E402
import source_documents as sd      # noqa: E402
import business_knowledge as bk    # noqa: E402
import knowledge_extractor as ke   # noqa: E402

PRICE_IN, PRICE_OUT, PRICE_CACHE_READ, PRICE_CACHE_WRITE = 1.00, 5.00, 0.10, 1.25

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
        return "artificial conflict" if item.category in ke.MULTI_VALUE_CATEGORIES else "genuine conflict"
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
        print("ABORT: real Anthropic client not active. No run performed.")
        return 1

    files = [(Path(p).name, (ROOT / p).read_bytes(), cat) for p, cat in INPUTS]
    pkg = sp.build_package_from_files(files)
    batches = ke._build_batches(pkg, char_budget=ke.BATCH_CHAR_BUDGET)
    usable = [d for d in pkg.source_documents
              if d.extraction_status in (sd.SUCCESS, sd.PARTIAL) and d.extracted_text
              and d.duplicate_of is None]
    source_count = len(usable)
    source_chunks = sum(len(b) for b in batches)   # every unit lives in exactly one batch
    batch_count = len(batches)

    # ---- Pre-run report ----
    print("=" * 72)
    print("SPRINT 4.1D.3 — RE-VALIDATION (pre-run)")
    print("=" * 72)
    total_chars = 0
    for doc in pkg.source_documents:
        total_chars += doc.extracted_character_count
        print(f"  {doc.filename:44} type={doc.file_type:5} "
              f"cat={doc.source_category:20} chars={doc.extracted_character_count}")
    print(f"  TOTAL extracted characters: {total_chars}")
    print(f"  source_count(original)={source_count}  source_chunks(post-split)={source_chunks}  "
          f"batch_count={batch_count}")
    print(f"  batches: {[[u.filename for u in b] for b in batches]}")
    print(f"  Real client active: True   model={client.model}")
    print("  MockKnowledgeClient disabled: True")
    print("  Running extraction ONCE...\n", flush=True)

    # ---- Single run (timed) ----
    t0 = time.time()
    result = ke.extract_business_knowledge(pkg, client=client, structured=True)
    wall = time.time() - t0
    knowledge = result.business_knowledge
    gap = result.gap_report
    cost = _cost(result)
    per_source = cost / max(1, source_count)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "outputs" / "pilots" / f"bk_revalidate_{stamp}"
    out.mkdir(parents=True, exist_ok=False)

    metrics = {
        "source_count": source_count, "source_chunks": source_chunks, "batch_count": batch_count,
        "successful_batches": result.successful_batches, "failed_batches": result.failed_batches,
        "retries": result.retries, "parser_failures": result.parser_failures,
        "proposals_received": result.proposals_received, "proposals_accepted": result.proposals_accepted,
        "proposals_confirmed": result.proposals_confirmed,
        "proposals_left_proposed": result.proposals_left_proposed,
        "proposals_rejected": result.proposals_rejected, "conflicts_created": result.conflicts_created,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "cache_read_tokens": result.cache_read_tokens, "cache_write_tokens": result.cache_write_tokens,
        "model_calls": result.model_calls, "model": result.model, "is_mock": result.is_mock,
        "total_api_cost_usd": round(cost, 6), "avg_cost_per_source_usd": round(per_source, 6),
        "wall_clock_seconds": round(wall, 1), "warnings": result.warnings,
    }
    (out / "results.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), "utf-8")
    (out / "business_knowledge.json").write_text(knowledge.to_json(), "utf-8")
    (out / "gap_report.json").write_text(json.dumps(gap.to_dict(), indent=2, ensure_ascii=False), "utf-8")

    # ---- manual_review.csv ----
    conflict_ids = {i for c in knowledge.conflicts for i in c.item_ids}
    with (out / "manual_review.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "attribute", "value", "status", "confidence", "source_filename",
                    "evidence_excerpt", "source_reference", "classification"])
        for it in knowledge.knowledge_items:
            ref = it.source_references[0] if it.source_references else None
            w.writerow([it.category, it.attribute, (it.value or "")[:140], it.status,
                        f"{it.confidence:.2f}", ref.filename if ref else "",
                        (it.evidence_excerpt or "").replace("\n", " ")[:140],
                        (ref.section_index if ref else ""), _classify(it, conflict_ids)])

    # ---- validation targets (1-9) ----
    idx = {d.filename.lower(): d for d in pkg.source_documents}
    confirmed = [i for i in knowledge.knowledge_items if i.status == bk.CONFIRMED]
    every_confirmed_literal = all(
        i.evidence_excerpt and (lambda ref: ref and ke._excerpt_in(idx.get(ref.filename.lower()),
                                                                   i.evidence_excerpt) if ref else False)(
            i.source_references[0] if i.source_references else None)
        for i in confirmed)
    artificial_conflicts = sum(1 for c in knowledge.conflicts if c.category in ke.MULTI_VALUE_CATEGORIES)
    genuine_conflicts = len(knowledge.conflicts) - artificial_conflicts
    serializable = True
    try:
        json.dumps(knowledge.to_dict()); json.dumps(gap.to_dict())
    except Exception:  # noqa: BLE001
        serializable = False

    targets = {
        "1_no_source_or_batch_lost": result.failed_batches == 0 and result.proposals_received > 0,
        "2_failed_batches_zero": result.failed_batches == 0,
        "3_parser_failures_not_fatal": result.proposals_received > 0,
        "4_confirmed_have_literal_excerpt": every_confirmed_literal,
        "5_no_paraphrase_confirmed": every_confirmed_literal,
        "6_no_confirmed_buyer_or_hard_exclusion":
            not any(i.category in ("buyer", "excluded_buyer", "hard_exclusion_candidate")
                    for i in confirmed),
        "7_no_confirmed_interpretive(historical_guard)":
            not any(i.category in ke.INTERPRETIVE_CATEGORIES for i in confirmed),
        "8_no_artificial_conflicts": artificial_conflicts == 0,
        "9_serializable_and_coherent": serializable and gap is not None,
    }
    all_pass = all(targets.values())

    by_status, by_cat, by_class = {}, {}, {}
    for it in knowledge.knowledge_items:
        by_status[it.status] = by_status.get(it.status, 0) + 1
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
        c = _classify(it, conflict_ids)
        by_class[c] = by_class.get(c, 0) + 1

    lines = [
        "SPRINT 4.1D.3 — BUSINESS KNOWLEDGE EXTRACTION RE-VALIDATION",
        f"timestamp: {stamp}   model: {result.model}   is_mock: {result.is_mock}",
        "",
        "INPUT FILES:",
    ]
    for doc in pkg.source_documents:
        lines.append(f"  - {doc.filename} ({doc.source_category}, {doc.extracted_character_count} chars)")
    lines += [
        f"  total chars={total_chars}  source_count={source_count}  chunks={source_chunks}  "
        f"batches={batch_count}",
        "",
        "METRICS:",
        f"  successful_batches={result.successful_batches} failed_batches={result.failed_batches} "
        f"retries={result.retries} parser_failures={result.parser_failures}",
        f"  proposals received={result.proposals_received} accepted={result.proposals_accepted} "
        f"confirmed={result.proposals_confirmed} left_proposed={result.proposals_left_proposed} "
        f"rejected={result.proposals_rejected} conflicts={result.conflicts_created}",
        f"  tokens in={result.input_tokens} out={result.output_tokens} "
        f"cache_read={result.cache_read_tokens} cache_write={result.cache_write_tokens} "
        f"model_calls={result.model_calls}",
        f"  cost=${cost:.6f}  avg_per_source=${per_source:.6f}  wall_clock={wall:.1f}s",
        "",
        f"KNOWLEDGE ITEMS: {len(knowledge.knowledge_items)}  by_status={by_status}",
        f"  by_category={by_cat}",
        f"  conflicts: total={len(knowledge.conflicts)} genuine={genuine_conflicts} "
        f"artificial={artificial_conflicts}",
        f"  classification={by_class}",
        "",
        "GAP REPORT:",
        f"  ready_for_icp_generation={gap.is_ready_for_icp_generation} "
        f"completeness={gap.completeness_score} blocking={len(gap.blocking_gaps)} "
        f"important={len(gap.important_gaps)} optional={len(gap.optional_gaps)}",
        "",
        "VALIDATION TARGETS:",
    ]
    for k, v in targets.items():
        lines.append(f"  [{'PASS' if v else 'FAIL'}] {k}")
    lines += ["", f"PILOT RESULT: {'PASS' if all_pass else 'CONDITIONAL/FAIL — see targets'}"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", "utf-8")

    print("\n".join(lines))
    print(f"\nOutputs written to: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
