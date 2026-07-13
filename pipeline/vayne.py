"""Vayne lead scraping (Sprint 5.3).

Ports the verified Vayne API flow (Sales Navigator URL -> order -> poll -> export -> CSV) behind a
small client, and adds a deterministic **offline mock** so the campaign flow runs end-to-end without
credits or network. Mirrors the real/mock pattern used by scoring and extraction.

Documented Vayne flow (base https://www.vayne.io, ``Authorization: Bearer <VAYNE_API_TOKEN>``):
  1. POST /api/url_checks {url}                     -> {total, type}   (pre-scrape count; no credits)
  2. POST /api/orders {url,name,export_format,limit,secondary_webhook}
  3. GET  /api/orders/{id}  (poll until scraping_status == "finished")
  4. POST /api/orders/{id}/export {export_format}   (409 => keep polling)
  5. GET  <exports[fmt].file_url>                   -> CSV
Then dedupe by LinkedIn URL. Output rows use the same Vayne column names the scorer normalizes.

Credit safety: scraping is data acquisition, not outreach. Callers must pass an explicit ``limit``;
``check_url`` returns the available count so the UI can confirm before spending credits.
"""
from __future__ import annotations

import csv
import io
import os
import time
from typing import Any, Callable, Optional

import requests

import config

DEFAULT_EXPORT_FORMAT = "simple"
HTTP_TIMEOUT = 60
POLL_INTERVAL = 15
POLL_TIMEOUT = 1800
EXPORT_POLL_INTERVAL = 10
EXPORT_TIMEOUT = 1200

DEFAULT_LIMIT = 100          # UI default
MAX_LEADS_CAP = 1000         # hard safety cap on a single scrape

Progress = Optional[Callable[[str], None]]


def _note(progress: Progress, msg: str) -> None:
    if progress:
        progress(msg)


# ---------------------------------------------------------------------------
# Live client (ported from the verified scrape.py)
# ---------------------------------------------------------------------------

class VayneClient:
    """Real Vayne API client."""

    is_mock = False

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or config.VAYNE_BASE_URL

    # --- low-level API ---
    def _headers(self) -> dict:
        return config.vayne_headers()

    @staticmethod
    def _order(resp: requests.Response) -> dict[str, Any]:
        data = resp.json()
        return data.get("order", data)

    @staticmethod
    def _raise(resp: requests.Response) -> None:
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                body = resp.json()
                details = body.get("details") or body.get("error") or body
            except ValueError:
                details = resp.text.strip()
            raise RuntimeError(f"Vayne API error {resp.status_code}: {details}") from exc

    def check_url(self, url: str) -> dict[str, Any]:
        """Validate a Sales Navigator URL and return {total, type, ...}. Spends no credits."""
        resp = requests.post(f"{self.base_url}/api/url_checks", headers=self._headers(),
                             json={"url": url}, timeout=HTTP_TIMEOUT)
        self._raise(resp)
        return resp.json()

    def _create_order(self, url, name, limit, export_format, webhook) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url, "name": name, "export_format": export_format}
        if limit and limit > 0:
            payload["limit"] = limit
        if webhook:
            payload["secondary_webhook"] = webhook
        resp = requests.post(f"{self.base_url}/api/orders", headers=self._headers(),
                             json=payload, timeout=HTTP_TIMEOUT)
        self._raise(resp)
        return self._order(resp)

    def _get_order(self, order_id: int) -> dict[str, Any]:
        resp = requests.get(f"{self.base_url}/api/orders/{order_id}", headers=self._headers(),
                            timeout=HTTP_TIMEOUT)
        self._raise(resp)
        return self._order(resp)

    def _poll_order(self, order_id: int, progress: Progress) -> dict[str, Any]:
        start = time.time()
        while time.time() - start < POLL_TIMEOUT:
            order = self._get_order(order_id)
            status = order.get("scraping_status", "unknown")
            scraped = order.get("scraped", 0)
            target = order.get("limit") or order.get("total") or "?"
            _note(progress, f"scraping: {status} — {scraped}/{target}")
            if status == "finished":
                return order
            if status == "failed":
                raise RuntimeError(f"Vayne order #{order_id} failed")
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"Timed out waiting for Vayne order #{order_id}")

    @staticmethod
    def _export_file_url(order, fmt) -> Optional[str]:
        export = (order.get("exports") or {}).get(fmt, {})
        if export.get("status") == "completed" and export.get("file_url"):
            return export["file_url"]
        return order.get("file_url") or None

    def _ensure_export(self, order, fmt, progress: Progress) -> str:
        url = self._export_file_url(order, fmt)
        if url:
            return url
        order_id = order.get("id")
        if not order_id:
            raise RuntimeError("Vayne response did not include an order id")
        _note(progress, f"generating '{fmt}' export…")
        resp = requests.post(f"{self.base_url}/api/orders/{order_id}/export", headers=self._headers(),
                             json={"export_format": fmt}, timeout=HTTP_TIMEOUT)
        if resp.status_code != 409:            # 409 => another export already running; just poll
            self._raise(resp)
        start = time.time()
        while time.time() - start < EXPORT_TIMEOUT:
            order = self._get_order(order_id)
            url = self._export_file_url(order, fmt)
            if url:
                return url
            if ((order.get("exports") or {}).get(fmt, {}).get("status")) == "failed":
                raise RuntimeError(f"Vayne '{fmt}' export failed for order #{order_id}")
            time.sleep(EXPORT_POLL_INTERVAL)
        raise TimeoutError(f"Timed out waiting for '{fmt}' export on order #{order_id}")

    @staticmethod
    def _fetch_rows(file_url: str) -> list[dict[str, str]]:
        resp = requests.get(file_url, timeout=120)
        resp.raise_for_status()
        text = resp.content.decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))

    def scrape(self, url: str, *, limit: int, name: Optional[str] = None,
               export_format: str = DEFAULT_EXPORT_FORMAT, webhook: Optional[str] = None,
               progress: Progress = None) -> list[dict[str, str]]:
        """Run one order end-to-end and return deduplicated lead rows."""
        name = name or f"campaign-{time.strftime('%Y%m%d-%H%M%S')}"
        webhook = webhook if webhook is not None else config.VAYNE_WEBHOOK_URL
        _note(progress, f"creating order for {limit} lead(s)…")
        order = self._create_order(url, name, limit, export_format, webhook)
        order_id = order["id"]
        _note(progress, f"order #{order_id} created; waiting for scraping…")
        order = self._poll_order(order_id, progress)
        file_url = self._ensure_export(order, export_format, progress)
        _note(progress, "downloading CSV…")
        return deduplicate(self._fetch_rows(file_url))


# ---------------------------------------------------------------------------
# Offline mock
# ---------------------------------------------------------------------------

_MOCK_FIRST = ["Alex", "Priya", "Marco", "Dana", "Sofia", "Liam", "Noah", "Emma", "Yuki", "Omar"]
_MOCK_LAST = ["Chen", "Patel", "Rossi", "Kim", "Garcia", "Novak", "Silva", "Haddad", "Muller", "Ito"]
_MOCK_TITLES = ["VP Engineering", "CTO", "Head of Product", "Chief Marketing Officer",
                "Founder & CEO", "Talent Acquisition Lead", "Procurement Manager",
                "Director of Operations", "Engineering Manager", "Head of Growth"]
_MOCK_COMPANIES = ["Acme SaaS", "PayFlow", "VerticalHQ", "DataForge", "NimbusPay",
                   "LedgerLabs", "CoreBank Systems", "BrightRetail", "StaffPro Agency", "CryptoX Exchange"]
_MOCK_INDUSTRIES = ["Financial Services", "Software Development", "Fintech", "Retail",
                    "Staffing & Recruiting", "Banking", "Information Technology"]
_MOCK_LOCATIONS = ["San Francisco, US", "New York, US", "London, UK", "Berlin, DE",
                   "Austin, US", "Toronto, CA"]
_MOCK_SIZES = ["11-50", "51-200", "201-500", "501-1000", "1001-5000"]


class MockVayneClient:
    """Deterministic OFFLINE stand-in. Emits well-formed Vayne 'simple'-style rows (clearly marked)
    so the whole campaign flow runs without credits or network. NOT real leads."""

    is_mock = True

    def check_url(self, url: str) -> dict[str, Any]:
        return {"total": 137, "type": "prospects", "mock": True}

    def scrape(self, url: str, *, limit: int, name: Optional[str] = None,
               export_format: str = DEFAULT_EXPORT_FORMAT, webhook: Optional[str] = None,
               progress: Progress = None) -> list[dict[str, str]]:
        n = limit if limit and limit > 0 else 25
        n = min(n, MAX_LEADS_CAP)
        _note(progress, f"[MOCK] generating {n} placeholder lead(s)…")
        rows: list[dict[str, str]] = []
        for i in range(n):
            # Coprime-ish multipliers so name / title / company / industry vary independently
            # (otherwise every company gets the same person and it looks fake).
            fn = _MOCK_FIRST[i % len(_MOCK_FIRST)]
            ln = _MOCK_LAST[(i * 7 + 3) % len(_MOCK_LAST)]
            title = _MOCK_TITLES[(i * 3) % len(_MOCK_TITLES)]
            company = _MOCK_COMPANIES[(i * 4 + 1) % len(_MOCK_COMPANIES)]
            rows.append({
                "First Name": fn, "Last Name": ln, "Job Title": title, "Company": company,
                "Linkedin Url": f"https://www.linkedin.com/in/{fn.lower()}-{ln.lower()}-{i}",
                "Linkedin Industry": _MOCK_INDUSTRIES[(i * 5) % len(_MOCK_INDUSTRIES)],
                "Location": _MOCK_LOCATIONS[(i * 2) % len(_MOCK_LOCATIONS)],
                "Linkedin Employees": _MOCK_SIZES[(i * 3 + 2) % len(_MOCK_SIZES)],
                "Headline": f"{title} at {company}",
                "Corporate Website": f"https://{company.lower().replace(' ', '')}.example",
                "Summary": "[MOCK] Placeholder lead generated offline — not a real prospect.",
            })
        return deduplicate(rows)


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def deduplicate(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Deduplicate by the personal LinkedIn URL column (not the company one), keeping first seen."""
    if not rows:
        return rows
    url_col = next((c for c in rows[0].keys()
                    if "linkedin url" in c.lower() and "company" not in c.lower()), None)
    if not url_col:
        return rows
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row.get(url_col) or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out


def get_vayne_client() -> tuple[Any, bool]:
    """Return ``(client, is_live)``. Live when VAYNE_API_TOKEN is present, else the offline mock."""
    if os.getenv("VAYNE_API_TOKEN"):
        return VayneClient(), True
    return MockVayneClient(), False
