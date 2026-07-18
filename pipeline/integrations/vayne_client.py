"""Vayne client — the replaceable external integration boundary (Sprint 12).

The ONLY module that speaks HTTP to Vayne. It authenticates, submits a Sales Navigator URL as a
scraping order, reads job status, retrieves the completed CSV result, and translates transport/API
errors into ``VayneClientError``. It knows nothing about MarketHypothesis, SearchStrategy, ICP
lineage, LeadBatch, qualification, persistence, or Streamlit.

Verified contract (from the archived production scraper ``archive/legacy_pipeline/scrape.py``):
  * auth      Authorization: Bearer <VAYNE_API_TOKEN>   (config.vayne_headers)
  * base      https://www.vayne.io                       (config.VAYNE_BASE_URL)
  * submit    POST /api/orders            {url, name, export_format, limit?, secondary_webhook?}
  * status    GET  /api/orders/{id}       -> {order:{ scraping_status, exports:{fmt:{status,file_url}}, ...}}
  * export    POST /api/orders/{id}/export {export_format}  (409 = an export is already in progress)
  * download  GET  <file_url>             -> CSV bytes
Async + polling; ``scraping_status`` is one of running/``finished``/``failed``. No webhook required.

Credentials come from the existing env/config pattern and are never logged, printed, or returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config

DEFAULT_EXPORT_FORMAT = "simple"     # verified default in the archived scraper
HTTP_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 120

# normalized scraping states (Vayne-faithful)
STATE_RUNNING = "running"
STATE_FINISHED = "finished"
STATE_FAILED = "failed"


class VayneClientError(Exception):
    """A Vayne transport/API error. ``transient`` marks a retryable condition (network/timeout/5xx or
    an export not ready yet) versus a terminal Vayne failure. Never carries credentials."""

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class VayneJobStatus:
    state: str = STATE_RUNNING           # running | finished | failed
    scraped: int = 0
    target: Optional[int] = None
    export_ready: bool = False


class VayneClient:
    """Thin, faithful wrapper over the verified Vayne API. Replaceable in tests by any object exposing
    ``submit`` / ``status`` / ``download_csv``."""

    def __init__(self, *, token: Optional[str] = None, base_url: Optional[str] = None,
                 export_format: str = DEFAULT_EXPORT_FORMAT, timeout: int = HTTP_TIMEOUT):
        self._token = token if token is not None else config.VAYNE_API_TOKEN
        self._base_url = (base_url or config.VAYNE_BASE_URL).rstrip("/")
        self._export_format = export_format
        self._timeout = timeout

    # --- transport ----------------------------------------------------------

    def _headers(self) -> dict:
        if not self._token:
            raise VayneClientError("Vayne API token is not configured (set VAYNE_API_TOKEN).")
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, url: str, *, json=None, timeout=None):
        import requests   # lazy: keeps the module importable and tests fake-only
        try:
            resp = requests.request(method, url, headers=self._headers(), json=json,
                                    timeout=timeout or self._timeout)
        except requests.Timeout as exc:
            raise VayneClientError("Vayne request timed out.", transient=True) from exc
        except requests.RequestException as exc:
            raise VayneClientError("Network error contacting Vayne.", transient=True) from exc
        return resp

    def _check(self, resp) -> None:
        if resp.status_code >= 400:
            transient = resp.status_code >= 500 or resp.status_code == 429
            try:
                body = resp.json()
                detail = body.get("details") or body.get("error") or f"HTTP {resp.status_code}"
            except ValueError:
                detail = f"HTTP {resp.status_code}"
            # never include request headers/credentials in the message
            raise VayneClientError(f"Vayne API error: {detail}", transient=transient)

    @staticmethod
    def _order(resp_json) -> dict:
        return resp_json.get("order", resp_json) if isinstance(resp_json, dict) else {}

    def _get_order(self, order_id) -> dict:
        resp = self._request("GET", f"{self._base_url}/api/orders/{order_id}")
        self._check(resp)
        return self._order(resp.json())

    def _file_url(self, order: dict) -> Optional[str]:
        export = (order.get("exports") or {}).get(self._export_format) or {}
        if export.get("status") == "completed" and export.get("file_url"):
            return export["file_url"]
        return order.get("file_url") or None

    # --- public API ---------------------------------------------------------

    def submit(self, sales_navigator_url: str, name: str, *, lead_limit: Optional[int] = None) -> str:
        """Create a scraping order; return the external job id (as a string).

        ``lead_limit`` is the provider-INDEPENDENT retrieval intent: ``None`` = no limit (scrape all
        available), a positive int = the maximum number of leads. This method is the ONLY place that
        translation is turned into the Vayne-specific payload. Verified Vayne contract
        (``archive/legacy_pipeline/scrape.py``): an OMITTED ``limit`` key means "all available"; a
        positive ``limit`` caps the order. Vayne has no ``null``/``-1`` sentinel, so unlimited simply
        omits the key."""
        payload = {"url": sales_navigator_url, "name": name, "export_format": self._export_format}
        if lead_limit is not None and lead_limit > 0:
            payload["limit"] = lead_limit
        resp = self._request("POST", f"{self._base_url}/api/orders", json=payload)
        self._check(resp)
        order = self._order(resp.json())
        order_id = order.get("id")
        if not order_id:
            raise VayneClientError("Vayne did not return an order id.")
        return str(order_id)

    def status(self, external_job_id: str) -> VayneJobStatus:
        """Read the current job status (Vayne-faithful, normalized)."""
        order = self._get_order(external_job_id)
        raw = str(order.get("scraping_status") or "").lower()
        state = STATE_FINISHED if raw == "finished" else STATE_FAILED if raw == "failed" else STATE_RUNNING
        return VayneJobStatus(state=state, scraped=int(order.get("scraped") or 0),
                              target=order.get("limit") or order.get("total"),
                              export_ready=bool(self._file_url(order)))

    def download_csv(self, external_job_id: str) -> bytes:
        """Ensure the CSV export exists for a finished order and download it. Raises a *transient*
        error if the export is still being generated (the caller should retry later)."""
        order = self._get_order(external_job_id)
        if str(order.get("scraping_status") or "").lower() != STATE_FINISHED:
            raise VayneClientError("Vayne order is not finished yet.", transient=True)
        file_url = self._file_url(order)
        if not file_url:
            resp = self._request("POST", f"{self._base_url}/api/orders/{external_job_id}/export",
                                 json={"export_format": self._export_format})
            if resp.status_code != 409:      # 409 = an export is already in progress -> just re-poll
                self._check(resp)
                order = self._order(resp.json())
            else:
                order = self._get_order(external_job_id)
            file_url = self._file_url(order)
        if not file_url:
            raise VayneClientError("Vayne export is still being generated.", transient=True)
        resp = self._request("GET", file_url, timeout=DOWNLOAD_TIMEOUT)
        self._check(resp)
        return resp.content
