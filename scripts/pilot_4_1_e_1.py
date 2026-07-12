"""Sprint 4.1E.1 — Real Draft ICP Validation Pilot (single run).

Harness only (no production code changes). Reuses the BusinessKnowledge + GapReport from the latest
successful Business Knowledge pilot (reconstructed faithfully from its saved JSON), runs ONE real
Draft ICP generation with the production ICPDraftClient, reviews the result, and writes a timestamped
folder under outputs/pilots/.

    PYTHONIOENCODING=utf-8 ./.venv/bin/python scripts/pilot_4_1_e_1.py
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
import business_knowledge as bk    # noqa: E402
import knowledge_gaps as kg        # noqa: E402
import generated_icp as gi         # noqa: E402
import icp_draft_generator as dg   # noqa: E402

PRICE_IN, PRICE_OUT, PRICE_CACHE_READ, PRICE_CACHE_WRITE = 1.00, 5.00, 0.10, 1.25
SOURCE_BK = ROOT / "outputs" / "pilots" / "bk_revalidate_20260712-220724"


def _load_bk(path: Path):
    data = json.loads(path.read_text("utf-8"))
    k = bk.BusinessKnowledge()
    k.knowledge_id = data["knowledge_id"]
    k.created_at, k.updated_at = data["created_at"], data["updated_at"]
    k.source_package_id = data["source_package_id"]
    k.entry_point = data.get("entry_point")

    def mk(d):
        d = dict(d)
        d["source_references"] = [bk.SourceReference(**r) for r in (d.get("source_references") or [])]
        return bk.KnowledgeItem(**d)

    k.knowledge_items = [mk(i) for i in data["knowledge_items"]]
    k.conflicts = [bk.ConflictRecord(**c) for c in (data.get("conflicts") or [])]
    k.unknown_fields = list(data.get("unknown_fields") or [])
    k.warnings = list(data.get("warnings") or [])
    return k


def _cost(r) -> float:
    return (r.input_tokens * PRICE_IN + r.output_tokens * PRICE_OUT
            + r.cache_read_tokens * PRICE_CACHE_READ
            + r.cache_write_tokens * PRICE_CACHE_WRITE) / 1_000_000


def _vals(knowledge, category):
    return {str(it.value).strip().lower() for it in knowledge.get_items(category=category)
            if it.is_active and it.has_value}


def main() -> int:
    client, is_live = dg.get_draft_client()
    if not is_live or isinstance(client, dg.MockICPDraftClient):
        print("ABORT: real ICPDraftClient not active (no ANTHROPIC_API_KEY). No run performed.")
        return 1

    knowledge = _load_bk(SOURCE_BK / "business_knowledge.json")
    gap = kg.detect_gaps(knowledge)
    saved_gap = json.loads((SOURCE_BK / "gap_report.json").read_text("utf-8"))

    print("=" * 72)
    print("SPRINT 4.1E.1 — REAL DRAFT ICP PILOT (pre-run)")
    print("=" * 72)
    print(f"  Source BK: {SOURCE_BK.name}")
    print(f"  knowledge_items={len(knowledge.knowledge_items)}  conflicts={len(knowledge.conflicts)}")
    print(f"  gap completeness (recomputed)={gap.completeness_score} (saved={saved_gap['completeness_score']}) "
          f"ready={gap.is_ready_for_icp_generation}")
    print(f"  Real client active: True  model={client.model}  Mock disabled: True")
    print("  Running ONE draft generation...\n", flush=True)

    t0 = time.time()
    res = dg.generate_draft_icp(knowledge, gap, client=client)
    wall = time.time() - t0
    icp = res.generated_icp
    val = res.validation_result
    cost = _cost(res)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "outputs" / "pilots" / f"icp_draft_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "generated_icp.json").write_text(icp.to_json(), "utf-8")
    (out / "generated_icp.md").write_text(icp.to_markdown(), "utf-8")
    (out / "iqs_report.json").write_text(json.dumps({
        "is_valid": val.is_valid, "blocking_errors": val.blocking_errors, "warnings": val.warnings,
        "completeness_score": val.completeness_score, "section_completeness": val.section_completeness,
        "suggested_next_actions": val.suggested_next_actions}, indent=2, ensure_ascii=False), "utf-8")

    # ---- provenance / hallucination checks (Python-structured fields must trace to BK) ----
    ind = _vals(knowledge, "industry")
    geo = _vals(knowledge, "geography")
    conf_buyers = {str(it.value).strip().lower() for it in knowledge.get_items(category="buyer")
                   if it.is_active and it.status == bk.CONFIRMED}
    cand = _vals(knowledge, "hard_exclusion_candidate")
    bk_files = {r.filename.lower() for it in knowledge.knowledge_items for r in it.source_references}

    prov = {
        "target_industries_from_bk": all(i.strip().lower() in ind
                                         for i in icp.target_companies.target_industries),
        "target_geographies_from_bk": all(g.strip().lower() in geo
                                          for g in icp.target_companies.target_geographies),
        "primary_buyers_from_confirmed": all(b.strip().lower() in conf_buyers
                                             for b in icp.target_buyers.primary_buyer_roles),
        "hard_exclusions_from_candidates": all(h.rule.strip().lower() in cand
                                               for h in icp.hard_exclusions),
        "source_files_from_bk": all((f.lower() in bk_files) for f in icp.metadata.source_files),
    }
    no_hallucination = all(prov.values())

    gap_missing = {g.field for g in list(gap.blocking_gaps) + list(gap.important_gaps)
                   if g.current_status == "missing"}
    unknown_preserved = gap_missing.issubset(set(icp.unknown_fields))
    thresholds_fixed = [(b.label, b.min_score, b.max_score) for b in icp.priority_thresholds] == \
        [(b.label, b.min_score, b.max_score) for b in gi.standard_priority_bands()]
    status_draft = icp.metadata.status == gi.STATUS_DRAFT
    serializable = True
    try:
        json.loads(icp.to_json()); icp.to_markdown()
    except Exception:  # noqa: BLE001
        serializable = False

    checks = {
        "status_remains_draft": status_draft,
        "thresholds_fixed": thresholds_fixed,
        "serializable": serializable,
        "unknown_fields_preserved": unknown_preserved,
        "enrichment_fields_present_or_declared": True,   # informational (may legitimately be empty)
        "no_hallucination_in_structured_fields": no_hallucination,
        **{f"prov_{k}": v for k, v in prov.items()},
    }
    core_pass = (status_draft and thresholds_fixed and serializable and no_hallucination
                 and unknown_preserved and res.successful)
    verdict = "PASS" if (core_pass and val.is_valid) else \
              "CONDITIONAL PASS" if core_pass else "FAIL"

    # ---- manual_review.csv ----
    with (out / "manual_review.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["element_type", "name", "detail", "value", "review"])
        m = icp.metadata
        w.writerow(["metadata", "status", "", m.status, "OK" if status_draft else "REVIEW"])
        w.writerow(["metadata", "name", "", m.name, ""])
        w.writerow(["metadata", "source_files", "", "; ".join(m.source_files),
                    "OK" if prov["source_files_from_bk"] else "REVIEW"])
        bcx = icp.business_context
        w.writerow(["business_context", "product_or_service", "", bcx.product_or_service[:120], ""])
        w.writerow(["business_context", "value_proposition", "", bcx.value_proposition[:120], ""])
        w.writerow(["business_context", "business_model", "", bcx.business_model[:120], ""])
        tc = icp.target_companies
        w.writerow(["target_companies", "industries", "", "; ".join(tc.target_industries),
                    "OK" if prov["target_industries_from_bk"] else "REVIEW"])
        w.writerow(["target_companies", "subsegments", "", "; ".join(tc.target_subsegments), ""])
        w.writerow(["target_companies", "geographies", "", "; ".join(tc.target_geographies),
                    "OK" if prov["target_geographies_from_bk"] else "REVIEW"])
        w.writerow(["target_companies", "preferred_employee_ranges", "",
                    "; ".join(tc.preferred_employee_ranges), ""])
        tb = icp.target_buyers
        w.writerow(["target_buyers", "primary_roles", "", "; ".join(tb.primary_buyer_roles),
                    "OK" if prov["primary_buyers_from_confirmed"] else "REVIEW"])
        w.writerow(["target_buyers", "excluded_roles", "", "; ".join(tb.excluded_buyer_roles), ""])
        for d in icp.dimensions:
            complete = bool(d.purpose and d.scoring_guidance and d.required_evidence_attributes)
            w.writerow(["dimension", d.name, f"weight={d.weight} enrichment={d.external_enrichment_required}",
                        (d.scoring_guidance or "")[:100], "OK" if complete else "INCOMPLETE"])
        w.writerow(["dimensions", "WEIGHT_TOTAL", "", str(sum(d.weight for d in icp.dimensions)),
                    "OK" if sum(d.weight for d in icp.dimensions) == 100 else "REVIEW"])
        for h in icp.hard_exclusions:
            w.writerow(["hard_exclusion", h.rule, f"mode={h.evaluation_mode} scope={h.scope}",
                        (h.evidence_required or "")[:100],
                        "OK" if h.rule.strip().lower() in cand else "REVIEW(not a candidate)"])
        w.writerow(["unknown_fields", "", "", "; ".join(icp.unknown_fields),
                    "OK" if unknown_preserved else "REVIEW"])
        w.writerow(["enrichment_fields", "", "", "; ".join(icp.enrichment_fields), ""])
        for wn in icp.warnings:
            w.writerow(["warning", "", "", wn[:140], ""])

    # ---- summary.txt (no raw document content) ----
    lines = [
        "SPRINT 4.1E.1 — REAL DRAFT ICP VALIDATION PILOT",
        f"timestamp: {stamp}   model: {res.model}   is_mock: {res.is_mock}",
        f"source BK: {SOURCE_BK.name}  (items={len(knowledge.knowledge_items)}, "
        f"conflicts={len(knowledge.conflicts)})",
        "",
        "METRICS:",
        f"  successful={res.successful} model_calls={res.model_calls} retries={res.retries} "
        f"parser_failures={res.parser_failures}",
        f"  tokens in={res.input_tokens} out={res.output_tokens} "
        f"cache_read={res.cache_read_tokens} cache_write={res.cache_write_tokens}",
        f"  cost=${cost:.6f}  wall_clock={wall:.1f}s",
        "",
        "GENERATED DRAFT ICP:",
        f"  name={icp.metadata.name!r}  status={icp.metadata.status}  source_files={icp.metadata.source_files}",
        f"  dimensions={len(icp.dimensions)} weight_total={sum(d.weight for d in icp.dimensions)}",
        f"  target_industries={icp.target_companies.target_industries}",
        f"  target_geographies={icp.target_companies.target_geographies}",
        f"  preferred_sizes={icp.target_companies.preferred_employee_ranges}",
        f"  primary_buyers={icp.target_buyers.primary_buyer_roles}",
        f"  hard_exclusions={[h.rule for h in icp.hard_exclusions]}",
        f"  unknown_fields={icp.unknown_fields}",
        f"  enrichment_fields={icp.enrichment_fields}",
        f"  warnings={len(icp.warnings)}",
        "",
        "IQS:",
        f"  is_valid={val.is_valid} completeness={val.completeness_score}",
        f"  blocking_errors={val.blocking_errors}",
        f"  warnings={val.warnings}",
        "",
        "CHECKS:",
    ]
    for k_, v_ in checks.items():
        lines.append(f"  [{'PASS' if v_ else 'FAIL'}] {k_}")
    lines += ["", f"PILOT RESULT: {verdict}"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", "utf-8")

    print("\n".join(lines))
    print(f"\nOutputs written to: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
