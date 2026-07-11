"""Sprint 3.4.1 — real-API integration pilot (10 FinTech leads).

Runs the CURRENT integrated flow (pipeline.scoring.score_leads) with the real AnthropicClient on
exactly 10 leads. Harness only — it does not modify architecture, UI, export, or docs.

Single run. Captures tokens/cost, retries, parser failures, and validates every result.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
csv.field_size_limit(10_000_000)

import config                 # noqa: E402  loads .env
import evidence as ev         # noqa: E402
import scoring as sc          # noqa: E402
from icp_pdf import extract_icp  # noqa: E402

RAW = ROOT / "data" / "raw" / "fintech_raw_vayne.csv"
BENCH = ROOT / "data" / "benchmarks" / "legacy" / "fintech_manual_v2_unvalidated.csv"
ICP_PDF = ROOT / "icp" / "Innotechfy_Fintech_Playbook.pdf"
MODEL = "claude-haiku-4-5-20251001"
# Haiku 4.5 pricing (USD per MTok)
P_IN, P_OUT, P_CACHE_READ, P_CACHE_WRITE = 1.00, 5.00, 0.10, 1.25
PROJECTION_N = 20_000


def log(m): print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def slug(u):
    u = (u or "").strip().lower().split("?")[0].rstrip("/")
    m = re.search(r"linkedin\.com/in/([^/]+)", u)
    return m.group(1) if m else None


class CapturingClient(sc.AnthropicClient):
    """Real AnthropicClient that records token usage per call (pilot instrumentation only)."""
    def __init__(self):
        super().__init__(model=MODEL)
        self.usages = []

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=6000, system=system,
            messages=[{"role": "user", "content": user}])
        u = msg.usage
        self.usages.append({
            "input": u.input_tokens, "output": u.output_tokens,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        })
        return "".join(b.text for b in msg.content if b.type == "text")


def select_ten():
    raw = list(csv.DictReader(RAW.open(encoding="utf-8-sig")))
    bench = list(csv.DictReader(BENCH.open(encoding="utf-8-sig")))
    raw_by = {}
    for r in raw:
        s = slug(r.get("linkedin url"))
        if s and s not in raw_by:
            raw_by[s] = r

    def score(b):
        try:
            return int(b.get("Lead Score") or 0)
        except ValueError:
            return 0

    matched = [b for b in bench if slug(b.get("LinkedIn URL")) in raw_by]
    matched.sort(key=score, reverse=True)
    cat = lambda b: (b.get("Priority Status") or "").strip()
    high = [b for b in matched if cat(b) in ("A+ / Hot", "A / High")][:3]
    med = [b for b in matched if cat(b) == "B / Normal"]
    med_sel = [med[0], med[len(med) // 2], med[-1]]
    lowc = [b for b in matched if cat(b) == "C / Low"]
    low_sel = [lowc[len(lowc) // 3], lowc[len(lowc) // 2]]
    lowest_sel = lowc[-2:]
    picked, seen, final = high + med_sel + low_sel + lowest_sel, set(), []
    for b in picked:
        s = slug(b["LinkedIn URL"])
        if s not in seen:
            seen.add(s)
            final.append((s, b, raw_by[s]))
    return final, len(raw), len(raw[0]), len(bench), len(bench[0])


def main():
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "outputs" / "pilots" / f"fintech_3_4_1_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    selected, raw_rows, raw_cols, bench_rows, bench_cols = select_ten()
    assert len(selected) == 10, f"expected 10, got {len(selected)}"

    log("=== Sprint 3.4.1 real-API integration pilot ===")
    log(f"SOURCE: {RAW.name} ({raw_rows} rows x {raw_cols} cols)")
    log(f"BENCHMARK (comparison only): {BENCH.name} ({bench_rows} rows x {bench_cols} cols)")
    log("Selected 10 leads (3 high / 3 medium / 2 low / 2 lowest):")
    for i, (s, b, _) in enumerate(selected):
        log(f"   [{i}] old={b['Lead Score']:>3} {b['Priority Status']:<12} {b['First Name']} {b['Last Name']}  | {b['LinkedIn URL']}")

    icp = extract_icp(ICP_PDF)
    log(f"ICP: {icp.name} ({icp.n_pages}p, {len(icp.text)} chars)")

    # capture engine logs (retries / parser failures)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    qlog = logging.getLogger("qualification")
    qlog.setLevel(logging.INFO)
    qlog.addHandler(h)

    client = CapturingClient()
    log(f"Client: {type(client).__name__} (real) | model={client.model} | MockClient NOT used")

    leads = [sc.normalize_lead(raw_row, i) for i, (_, _, raw_row) in enumerate(selected)]
    log("Running integrated score_leads() once (real API)…")
    results = sc.score_leads(leads, icp.text, "FinTech", client=client)  # single run

    by_idx = {r.lead_index: r for r in results}

    # --- cost ---------------------------------------------------------------
    usages = client.usages
    tin = sum(u["input"] for u in usages)
    tout = sum(u["output"] for u in usages)
    tcr = sum(u["cache_read"] for u in usages)
    tcw = sum(u["cache_write"] for u in usages)
    cost = tin/1e6*P_IN + tout/1e6*P_OUT + tcr/1e6*P_CACHE_READ + tcw/1e6*P_CACHE_WRITE
    ok = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]
    avg = cost/len(ok) if ok else 0.0
    projected = avg * PROJECTION_N

    logtext = buf.getvalue()
    n_calls = len(usages)
    retries = max(0, n_calls - 2)                     # 2 batches expected (10 leads / 5)
    parser_failures = logtext.count("no JSON array") + logtext.count("JSONDecodeError")

    # --- per-lead validation ------------------------------------------------
    rows = []
    for i, (s, b, raw_row) in enumerate(selected):
        r = by_idx.get(i)
        # previous-employment isolation check (evidence level)
        evs = ev.extract_evidence(leads[i].fields, leads[i].previous_roles)
        prev_ok = all(e.employment_scope == ev.SCOPE_PREVIOUS
                      for e in evs if e.attribute == ev.ATTR_PREVIOUS_ROLE)
        rows.append({
            "lead_index": i, "linkedin_url": b["LinkedIn URL"],
            "name": f"{b['First Name']} {b['Last Name']}".strip(),
            "old_score": b["Lead Score"], "old_category": b["Priority Status"],
            "parsed": r is not None and r.error is None,
            "new_raw_score": getattr(r, "raw_icp_score", None),
            "new_priority": getattr(r, "provisional_priority", None),
            "decision_confidence": getattr(r, "decision_confidence", None),
            "evidence_coverage": getattr(r, "evidence_coverage", None),
            "evidence_adjusted_fit": getattr(r, "evidence_adjusted_fit", None),
            "dealbreaker_state": getattr(r, "dealbreaker_state", None),
            "confirmed_dealbreakers": getattr(r, "confirmed_dealbreakers", []),
            "suspected_dealbreakers": getattr(r, "suspected_dealbreakers", []),
            "unknown_fields": getattr(r, "unknowns", []),
            "final_confidence_python": getattr(r, "confidence", None),
            "model_confidence_raw": getattr(r, "model_confidence", None),
            "prev_employment_isolated": prev_ok,
            "n_previous_roles": len(leads[i].previous_roles),
            "reason": getattr(r, "reason", ""),
            "error": getattr(r, "error", None),
        })

    (out_dir / "results.json").write_text(json.dumps(
        {"selected": rows, "usages": usages,
         "cost": {"input_tokens": tin, "output_tokens": tout, "cache_read": tcr, "cache_write": tcw,
                  "total_cost_usd": cost, "avg_cost_per_ok_lead": avg, "projected_20000": projected}},
        indent=2, ensure_ascii=False), encoding="utf-8")

    comp_cols = ["lead_index", "linkedin_url", "name", "old_score", "old_category", "parsed",
                 "new_raw_score", "new_priority", "decision_confidence", "evidence_coverage",
                 "evidence_adjusted_fit", "dealbreaker_state", "confirmed_dealbreakers",
                 "suspected_dealbreakers", "unknown_fields", "final_confidence_python",
                 "model_confidence_raw", "prev_employment_isolated", "error"]
    with (out_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=comp_cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({**row, "confirmed_dealbreakers": "; ".join(row["confirmed_dealbreakers"]),
                        "suspected_dealbreakers": "; ".join(row["suspected_dealbreakers"]),
                        "unknown_fields": "; ".join(row["unknown_fields"])})

    summary = f"""Sprint 3.4.1 — real-API integration pilot
==========================================
Run: {ts}
Source: {RAW.name} ({raw_rows}x{raw_cols})   Benchmark (comparison only): {BENCH.name} ({bench_rows}x{bench_cols})
ICP: {icp.name}   Model: {MODEL} (real AnthropicClient; MockClient not used)
Integrated flow: pipeline.scoring.score_leads (Knowledge + Evidence + Decision)

Leads: selected=10  successful={len(ok)}  failed={len(failed)}
API calls={n_calls}  retries={retries}  parser_failures={parser_failures}
Tokens: input={tin}  output={tout}  cache_read={tcr}  cache_write={tcw}  total={tin+tout+tcr+tcw}
Cost: total=${cost:.4f}  avg/successful-lead=${avg:.5f}  projected {PROJECTION_N:,}=${projected:,.2f}

Per-lead:
"""
    for row in rows:
        summary += (f"  [{row['lead_index']}] old={row['old_score']} {row['old_category']:<12}"
                    f" -> raw={row['new_raw_score']} {str(row['new_priority']):<26}"
                    f" conf={row['decision_confidence']} cov={row['evidence_coverage']}%"
                    f" db={row['dealbreaker_state']} model_conf={row['model_confidence_raw']}"
                    f" final_conf={row['final_confidence_python']} prev_iso={row['prev_employment_isolated']}\n")

    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    qlog.removeHandler(h)
    print("\n" + summary)
    log(f"Outputs: {out_dir}")
    log("PILOT COMPLETE — single run finished. Stopping.")


if __name__ == "__main__":
    main()
