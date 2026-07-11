"""Sprint 2.1 — corrected FinTech real-model scoring pilot (20 leads).

Corrections vs Sprint 2:
  * INPUT = raw Vayne export (data/fintech_raw_vayne.csv) — rich fields;
  * BENCHMARK = fintech2-scored.csv (the manual output this raw file was scored into;
    matched 100% by normalized LinkedIn slug);
  * STRATIFIED sample of 20 guaranteeing A+/A/B coverage (not just C/Low);
  * robust JSON parsing + ONE per-batch retry (never restarts the full 20-lead run);
  * current vs previous employment separated (req 7); tightened dealbreaker discipline
    (filters that need absent data — 5/6/9/11/12 — may not fire from absence).

Hard limits unchanged: 20 leads, batch size 5, single run, no full-run retry.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from icp_pdf import extract_icp          # noqa: E402
from config import BASE_DIR              # noqa: E402  (loads .env)

csv.field_size_limit(10_000_000)

# --- fixed pilot parameters --------------------------------------------------
HARD_LEAD_LIMIT = 20
BATCH_SIZE = 5
MAX_BATCH_ATTEMPTS = 2          # one retry per batch; does NOT restart the run
MODEL = "claude-haiku-4-5-20251001"
PRICE_IN_PER_MTOK = 1.00
PRICE_OUT_PER_MTOK = 5.00
PROJECTION_N = 20_000
STRATA = [("A+ / Hot", 5), ("A / High", 5), ("B / Normal", 5), ("C / Low", 5)]  # = 20

ICP_PDF = ROOT / "icp" / "Innotechfy_Fintech_Playbook.pdf"
RAW_CSV = ROOT / "data" / "raw" / "fintech_raw_vayne.csv"
BENCHMARK_CSV = ROOT / "data" / "benchmarks" / "legacy" / "fintech_manual_v2_unvalidated.csv"
PROMPT_FILE = ROOT / "prompts" / "pilot_fintech_system_v2.md"

DIMENSIONS = {
    "subsegment_fit": 25, "stage_funding_fit": 20, "buyer_persona": 15,
    "company_size": 10, "eng_hiring_signal": 10, "geography": 10, "reachability": 10,
}
BONUS_MAX = 10
SCORE_CAP = 100
BANDS = [(85, "A+ / Hot"), (70, "A / High"), (55, "B / Normal"), (40, "C / Low"), (0, "Not Relevant")]
DEALBREAKER_CATEGORY = "Not Relevant"
DEALBREAKER_SCORE_CAP = 39

# raw column -> field sent to model (CURRENT employment + profile signals)
CURRENT_FIELDS = {
    "current_job_title": "job title",
    "current_job_description": "job description",
    "current_job_started": "job started on",
    "current_company": "company",
    "company_industry": "linkedin industry",
    "company_size_employees": "linkedin employees",
    "company_employee_count": "linkedin company employee count",
    "company_revenue_range": "linkedin company revenue range",
    "company_founded_year": "linkedin founded year",
    "company_description": "linkedin description",
    "company_specialities": "linkedin specialities",
    "company_website": "corporate website",
    "location": "location",
    "headline": "headline",
    "summary": "summary",
    "skills": "skills",
    "premium_member": "premium member",
    "number_of_connections": "number of connections",
}
LONG_FIELDS = {"summary": 700, "company_description": 500, "current_job_description": 500, "skills": 400}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def norm_slug(u: str) -> str | None:
    u = (u or "").strip().lower().split("?")[0].rstrip("/")
    m = re.search(r"linkedin\.com/in/([^/]+)", u)
    return m.group(1) if m else None


def category_for(score: int) -> str:
    for threshold, label in BANDS:
        if score >= threshold:
            return label
    return BANDS[-1][1]


# --- selection: stratified across manual categories --------------------------
def build_lead(raw_row: dict, bench_row: dict, idx: int) -> dict:
    fields = {}
    for out_key, col in CURRENT_FIELDS.items():
        val = (raw_row.get(col) or "").strip()
        if val:
            if out_key in LONG_FIELDS:
                val = val[: LONG_FIELDS[out_key]]
            fields[out_key] = val
    previous = []
    for n in (2, 3, 4):
        t = (raw_row.get(f"job title ({n})") or "").strip()
        c = (raw_row.get(f"company ({n})") or "").strip()
        if t or c:
            previous.append({
                "title": t, "company": c,
                "industry": (raw_row.get(f"linkedin industry ({n})") or "").strip(),
                "started": (raw_row.get(f"job started on ({n})") or "").strip(),
                "ended": (raw_row.get(f"job ended on ({n})") or "").strip(),
            })
    if previous:
        fields["previous_roles"] = previous
    return {
        "lead_index": idx,
        "name": f"{raw_row.get('first name','')} {raw_row.get('last name','')}".strip(),
        "linkedin_url": raw_row.get("linkedin url", "").strip(),
        "old_score": int(bench_row.get("Lead Score") or 0),
        "old_category": (bench_row.get("Priority Status") or "").strip(),
        "old_reason": bench_row.get("Score Reason", ""),
        "fields": fields,
    }


def select_leads() -> list[dict]:
    raw = list(csv.DictReader(RAW_CSV.open(encoding="utf-8-sig")))
    raw_by_slug = {norm_slug(r.get("linkedin url")): r for r in raw if norm_slug(r.get("linkedin url"))}
    bench = list(csv.DictReader(BENCHMARK_CSV.open(encoding="utf-8-sig")))

    by_cat: dict[str, list[dict]] = {}
    for b in bench:
        slug = norm_slug(b.get("LinkedIn URL"))
        if slug and slug in raw_by_slug:
            by_cat.setdefault((b.get("Priority Status") or "").strip(), []).append((slug, b))

    selected, idx = [], 0
    for cat, n in STRATA:
        pool = by_cat.get(cat, [])
        step = max(1, len(pool) // n) if pool else 1
        picks = [pool[i * step] for i in range(min(n, len(pool)))]
        for slug, bench_row in picks:
            selected.append(build_lead(raw_by_slug[slug], bench_row, idx))
            idx += 1
    return selected


# --- model call --------------------------------------------------------------
def make_client():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ABORT: ANTHROPIC_API_KEY not set — pilot requires the real API, never MockClient.")
    import anthropic
    return anthropic.Anthropic()


def build_system(icp_text: str) -> list[dict]:
    return [
        {"type": "text", "text": PROMPT_FILE.read_text(encoding="utf-8")},
        {"type": "text", "text": "# FinTech ICP playbook (source of truth)\n\n" + icp_text,
         "cache_control": {"type": "ephemeral"}},
    ]


def call_model(client, system, batch):
    payload = [dict(lead_index=l["lead_index"], **l["fields"]) for l in batch]
    user = ("Score these leads against the FinTech ICP. Return a JSON array only.\n\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```")
    resp = client.messages.create(model=MODEL, max_tokens=6000, system=system,
                                  messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    usage = {"model": resp.model, "input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens,
             "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0}
    return text, usage


def parse_array(text: str) -> list[dict]:
    """Tolerant JSON-array extraction: strip fences, isolate [...], repair trailing commas."""
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    blob = m.group(1) if m else text[text.find("["): text.rfind("]") + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", blob)          # trailing commas
        repaired = re.sub(r"}\s*{", "},{", repaired)            # missing commas between objects
        data = json.loads(repaired)
    if not isinstance(data, list):
        raise ValueError("model response was not a JSON array")
    return data


def clamp(v, lo, hi) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def finalize(obj: dict) -> dict:
    dims = {}
    raw_dims = obj.get("dimensions") or {}
    for name, cap in DIMENSIONS.items():
        d = raw_dims.get(name) or {}
        dims[name] = {"points": clamp(d.get("points"), 0, cap), "max": cap,
                      "evidence": str(d.get("evidence", ""))[:200]}
    bonus = clamp((obj.get("bonus") or {}).get("points"), 0, BONUS_MAX)
    total = min(SCORE_CAP, sum(d["points"] for d in dims.values()) + bonus)
    hard = bool(obj.get("hard_dealbreaker"))
    db_reason = obj.get("dealbreaker_reason")
    db_reason = str(db_reason)[:240] if db_reason else None
    if hard:
        total, category = min(total, DEALBREAKER_SCORE_CAP), DEALBREAKER_CATEGORY
    else:
        category = category_for(total)
    conf = str(obj.get("confidence", "low")).lower()
    conf = conf if conf in ("high", "medium", "low") else "low"
    return {"score": total, "category": category, "dimensions": dims, "bonus": bonus,
            "hard_dealbreaker": hard, "dealbreaker_reason": db_reason,
            "reason": str(obj.get("reason", ""))[:240],
            "unknowns": [str(u) for u in (obj.get("unknowns") or []) if str(u).strip()],
            "confidence": conf}


# --- main --------------------------------------------------------------------
def main() -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "pilot_results" / f"fintech_20_v2_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log("=== Sprint 2.1 FinTech corrected pilot ===")
    icp = extract_icp(ICP_PDF)
    log(f"ICP: {icp.name} ({icp.n_pages}p, {len(icp.text)} chars)")
    log(f"INPUT (raw): {RAW_CSV.name}  |  BENCHMARK: {BENCHMARK_CSV.name}")

    selected = select_leads()
    assert len(selected) == HARD_LEAD_LIMIT, f"HARD LIMIT VIOLATION: {len(selected)}"
    log(f"Selected exactly {len(selected)} leads (stratified, HARD LIMIT={HARD_LEAD_LIMIT}):")
    for l in selected:
        log(f"   [{l['lead_index']:2d}] {l['name'][:24]:24} | old={l['old_score']:>3} {l['old_category']:<12}"
            f" | prev_roles={len(l['fields'].get('previous_roles', []))} | {l['linkedin_url']}")

    client = make_client()
    log(f"Client: real AnthropicClient | model={MODEL} | batch={BATCH_SIZE} (MockClient NOT used)")
    system = build_system(icp.text)

    results: dict[int, dict] = {}
    failed: dict[int, str] = {}
    usages: list[dict] = []
    n_batches = (len(selected) + BATCH_SIZE - 1) // BATCH_SIZE

    for b in range(n_batches):
        batch = selected[b * BATCH_SIZE:(b + 1) * BATCH_SIZE]
        idxs = [l["lead_index"] for l in batch]
        parsed = None
        for attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
            log(f"Batch {b+1}/{n_batches} leads {idxs} — attempt {attempt}/{MAX_BATCH_ATTEMPTS}…")
            try:
                text, usage = call_model(client, system, batch)
                usages.append(usage)
                log(f"  usage: in={usage['input_tokens']} out={usage['output_tokens']} cache_read={usage['cache_read']}")
                parsed = {int(o.get("lead_index", -1)): o for o in parse_array(text) if isinstance(o, dict)}
                break
            except Exception as exc:  # noqa: BLE001
                log(f"  attempt {attempt} failed: {type(exc).__name__}: {exc}")
                if attempt == MAX_BATCH_ATTEMPTS:
                    for l in batch:
                        failed[l["lead_index"]] = f"{type(exc).__name__}: {exc}"
        if parsed is None:
            continue
        for l in batch:
            i = l["lead_index"]
            if i in parsed:
                results[i] = finalize(parsed[i])
                r = results[i]
                log(f"  [{i:2d}] old={l['old_score']:>3} {l['old_category']:<12} -> new={r['score']:3d} "
                    f"{r['category']:<12} db={r['hard_dealbreaker']} conf={r['confidence']}")
            else:
                failed[i] = "missing from model response"
                log(f"  [{i:2d}] FAILED — missing from model response")

    # --- cost -----------------------------------------------------------------
    tot_in = sum(u["input_tokens"] for u in usages)
    tot_out = sum(u["output_tokens"] for u in usages)
    cost_in, cost_out = tot_in / 1e6 * PRICE_IN_PER_MTOK, tot_out / 1e6 * PRICE_OUT_PER_MTOK
    total_cost = cost_in + cost_out
    n_scored = len(results)
    avg_cost = total_cost / n_scored if n_scored else 0.0
    projected = avg_cost * PROJECTION_N

    # --- comparison -----------------------------------------------------------
    comp_cols = ["LinkedIn URL", "Lead Name", "Old Score", "New Score", "Score Diff",
                 "Old Category", "New Category", "Category Match", "Old Reason", "New Reason",
                 "Old Dealbreaker", "New Dealbreaker", "Mismatch Flag"]
    cat_mismatch = db_mismatch = big_diff = cat_agree = 0
    comp_rows = []
    for l in selected:
        i = l["lead_index"]
        res = results.get(i)
        old_db = l["old_category"] == "Not Relevant"
        if res is None:
            comp_rows.append({"LinkedIn URL": l["linkedin_url"], "Lead Name": l["name"],
                              "Old Score": l["old_score"], "New Score": "", "Score Diff": "",
                              "Old Category": l["old_category"], "New Category": "FAILED",
                              "Category Match": "", "Old Reason": l["old_reason"],
                              "New Reason": failed.get(i, ""), "Old Dealbreaker": old_db,
                              "New Dealbreaker": "", "Mismatch Flag": "FAILED"})
            continue
        diff = res["score"] - l["old_score"]
        cmatch = l["old_category"] == res["category"]
        dbmatch = old_db == res["hard_dealbreaker"]
        flags = []
        if cmatch:
            cat_agree += 1
        else:
            flags.append("CATEGORY"); cat_mismatch += 1
        if not dbmatch:
            flags.append("DEALBREAKER"); db_mismatch += 1
        if abs(diff) > 15:
            flags.append("SCORE>15"); big_diff += 1
        comp_rows.append({"LinkedIn URL": l["linkedin_url"], "Lead Name": l["name"],
                          "Old Score": l["old_score"], "New Score": res["score"], "Score Diff": diff,
                          "Old Category": l["old_category"], "New Category": res["category"],
                          "Category Match": cmatch, "Old Reason": l["old_reason"],
                          "New Reason": res["reason"], "Old Dealbreaker": old_db,
                          "New Dealbreaker": res["hard_dealbreaker"], "Mismatch Flag": "; ".join(flags)})
    with (out_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=comp_cols); w.writeheader(); w.writerows(comp_rows)

    (out_dir / "results.json").write_text(json.dumps(
        {"selected": [{**{k: l[k] for k in ("lead_index", "name", "linkedin_url", "old_score", "old_category")},
                       "result": results.get(l["lead_index"]), "error": failed.get(l["lead_index"])}
                      for l in selected], "usages": usages}, indent=2, ensure_ascii=False), encoding="utf-8")

    db_agree = sum(1 for cr in comp_rows if cr["New Dealbreaker"] != "" and cr["Old Dealbreaker"] == cr["New Dealbreaker"])
    cat_rate = cat_agree / n_scored if n_scored else 0
    db_rate = db_agree / n_scored if n_scored else 0
    within10 = sum(1 for cr in comp_rows if cr["Score Diff"] != "" and abs(cr["Score Diff"]) <= 10)

    summary = f"""Sprint 2.1 — FinTech corrected real-model scoring pilot
=======================================================
Run: {ts}
ICP: {icp.name} ({icp.n_pages}p) — rubric: 7 dims + 10 bonus, 5 categories, 12 dealbreakers
INPUT (raw): {RAW_CSV.name}   BENCHMARK: {BENCHMARK_CSV.name}   (matched by normalized LinkedIn slug)
Model: {MODEL} (real AnthropicClient; MockClient not used)
Batch size: {BATCH_SIZE}   Hard lead limit: {HARD_LEAD_LIMIT}   Per-batch retry: {MAX_BATCH_ATTEMPTS-1} (full run NOT retried)
Sampling: stratified {STRATA}

Leads: selected={len(selected)}  scored_ok={n_scored}  failed={len(failed)}
Tokens: input={tot_in}  output={tot_out}  total={tot_in + tot_out}
Cost: input=${cost_in:.4f}  output=${cost_out:.4f}  total=${total_cost:.4f}
Avg cost/lead: ${avg_cost:.5f}
Projected cost for {PROJECTION_N:,} leads: ${projected:,.2f}

Category agreement vs manual: {cat_agree}/{n_scored} = {cat_rate:.0%}
Hard-dealbreaker agreement:   {db_agree}/{n_scored} = {db_rate:.0%}
Score within 10 pts:          {within10}/{n_scored}
Mismatches needing review: category={cat_mismatch}  dealbreaker={db_mismatch}  score>15pts={big_diff}

Outputs:
  {out_dir / 'comparison.csv'}
  {out_dir / 'results.json'}
  {out_dir / 'summary.txt'}
"""
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    log("PILOT COMPLETE — single run finished. Stopping.")


if __name__ == "__main__":
    main()
