"""Google Sheets publisher — the external publishing boundary (Sprint 14).

The ONLY module that speaks to the Google Sheets API. It consumes **already-assembled canonical rows**
(built by ``review_export`` from the Human Review view model) and writes them to three managed
worksheets. It never sees a Lead, QualifiedLead, ReviewRow, or workspace object; it never reconstructs
names, reruns qualification, infers a value, touches priority, or modifies a review decision.

Two clearly separated layers:
  * **deterministic preparation** (pure, no network, fully testable): target parsing, canonical
    validation, duplicate detection, row → 2-D value conversion;
  * **network calls** (injectable client): create/open a spreadsheet and replace managed worksheets.

Verified against gspread 6.2.1 by introspection — every call used here exists with these signatures:
  ``Client.create(title)`` · ``Client.open_by_key(key)`` · ``Spreadsheet.add_worksheet(title, rows, cols)``
  ``Spreadsheet.worksheet(title)`` · ``Spreadsheet.worksheets()`` · ``Spreadsheet.id`` / ``.url``
  ``Worksheet.clear()`` · ``.resize(rows, cols)`` · ``.update(values, range_name)`` · ``.freeze(rows)``
  ``.format(ranges, format)`` · ``.set_basic_filter()`` · ``.columns_auto_resize(start, end)``
  exceptions: ``APIError`` (wraps a requests Response), ``SpreadsheetNotFound``, ``WorksheetNotFound``

Credentials come from the environment only (a service-account key path or inline JSON) and are never
logged, printed, returned, or serialized.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config
import export   # canonical schema constants ONLY (MAIN_COLUMNS / AI_COLUMNS) — the contract to enforce

# --- managed worksheets (this publisher owns ONLY these; others are never touched) ---------------

SHEET_LEADS = "Leads"
SHEET_AI = "AI Details"
SHEET_SUMMARY = "Summary"
MANAGED_WORKSHEETS = (SHEET_LEADS, SHEET_AI, SHEET_SUMMARY)

MODE_CREATE = "create"
MODE_UPDATE = "update"
PUBLISH_MODES = (MODE_CREATE, MODE_UPDATE)

# Google Sheets scopes needed to create/write spreadsheets owned by the service account.
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive.file")

# A spreadsheet id is the opaque key in /spreadsheets/d/<id>/ — Google uses 20+ url-safe chars.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_URL_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_WRAP_COLUMNS = ("Score Reason", "Score Breakdown", "Reviewer Comment", "Rejection Reason",
                 "ICP Signals", "Evidence Summary")


class GoogleSheetsError(Exception):
    """A publishing failure, already translated into a user-safe message. ``transient`` marks a
    retryable condition (rate limit / 5xx / network). Never carries credentials."""

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class PublishTarget:
    """Where to publish. Explicit by construction — the target is never auto-detected or guessed."""
    mode: str = MODE_CREATE
    title: str = ""              # MODE_CREATE: the new spreadsheet's name
    spreadsheet_id: str = ""     # MODE_UPDATE: the existing spreadsheet's id


@dataclass
class PublishResult:
    spreadsheet_id: str = ""
    spreadsheet_url: str = ""
    publish_mode: str = ""
    worksheets_updated: list = field(default_factory=list)
    row_counts: dict = field(default_factory=dict)
    published_at: str = ""
    warnings: list = field(default_factory=list)

    def summary(self) -> dict:
        return {"spreadsheet_id": self.spreadsheet_id, "spreadsheet_url": self.spreadsheet_url,
                "publish_mode": self.publish_mode, "worksheets_updated": list(self.worksheets_updated),
                "row_counts": dict(self.row_counts), "published_at": self.published_at,
                "warnings": list(self.warnings)}


# =============================================================================
# Deterministic preparation (pure — no network, no credentials)
# =============================================================================

def extract_spreadsheet_id(value: str) -> str:
    """Parse an explicit spreadsheet id or a full Google Sheets URL into the id. Returns "" when the
    input is not a recognizable target — the caller refuses rather than guessing."""
    raw = (value or "").strip()
    if not raw:
        return ""
    match = _URL_ID_RE.search(raw)
    if match:
        return match.group(1)
    if raw.startswith(("http://", "https://")):
        return ""                                  # a URL we do not recognize -> unknown, not guessed
    return raw if _ID_RE.match(raw) else ""


def rows_to_values(columns, rows) -> list:
    """Header + data as a 2-D list of strings, in **exact canonical column order**. Missing/empty values
    stay empty strings — nothing is invented or back-filled."""
    values = [list(columns)]
    for row in rows:
        values.append(["" if row.get(c) is None else str(row.get(c, "")) for c in columns])
    return values


def summary_to_values(summary_rows) -> list:
    """The deterministic summary as a 2-column sheet (label, value)."""
    return [["Field", "Value"]] + [[str(a), "" if b is None else str(b)] for a, b in summary_rows]


def _identity(row: dict) -> tuple:
    """Stable identity for duplicate detection: LinkedIn URL when present, else name + company."""
    url = str(row.get("LinkedIn URL", "") or "").strip().lower()
    if url:
        return ("url", url)
    return ("person", str(row.get("First Name", "")).strip().lower(),
            str(row.get("Last Name", "")).strip().lower(),
            str(row.get("Company", "")).strip().lower())


def find_duplicate_rows(main_rows) -> list:
    """Identities appearing more than once in the selected result (deterministic; [] when clean)."""
    seen, dupes = set(), []
    for row in main_rows:
        key = _identity(row)
        if key in seen and key not in dupes:
            dupes.append(key)
        seen.add(key)
    return dupes


def validate_publish(main_rows, ai_rows, target: PublishTarget, *, confirmed: bool,
                     credentials_available: bool = True) -> list:
    """Every check Python performs BEFORE any external call. Returns blocking issues ([] when safe).
    No partial publish can start while this returns anything."""
    issues = []
    if not confirmed:
        issues.append("Publishing to Google Sheets must be explicitly confirmed.")
    if not credentials_available:
        issues.append("Google Sheets credentials are not configured "
                      "(set GOOGLE_SHEETS_CREDENTIALS_FILE or GOOGLE_SHEETS_CREDENTIALS_JSON).")
    if not main_rows:
        issues.append("The selected scope contains no leads — nothing to publish.")
    if target.mode not in PUBLISH_MODES:
        issues.append(f"Unknown publish mode {target.mode!r}.")
    if target.mode == MODE_CREATE and not (target.title or "").strip():
        issues.append("A spreadsheet name is required to create a new spreadsheet.")
    if target.mode == MODE_UPDATE and not extract_spreadsheet_id(target.spreadsheet_id):
        issues.append("A valid Google Sheets spreadsheet ID or URL is required to update an existing "
                      "spreadsheet.")
    # the canonical schema is the contract: exact columns, exact order
    if main_rows and list(main_rows[0].keys()) != list(export.MAIN_COLUMNS):
        issues.append("Main rows do not match the canonical column schema/order.")
    if ai_rows and list(ai_rows[0].keys()) != list(export.AI_COLUMNS):
        issues.append("AI rows do not match the canonical column schema/order.")
    dupes = find_duplicate_rows(main_rows or [])
    if dupes:
        issues.append(f"The selected result contains {len(dupes)} duplicated lead row(s).")
    return issues


# =============================================================================
# Credentials (environment only; never logged, returned, or persisted)
# =============================================================================

def credentials_available() -> bool:
    return config.google_sheets_credentials_available()


def _build_client():
    """Authorize a gspread client from service-account credentials. Lazy imports keep the module (and
    the tests, which inject a fake client) independent of the Google libraries."""
    try:
        import gspread                                            # noqa: PLC0415  (lazy on purpose)
        from google.oauth2.service_account import Credentials     # noqa: PLC0415
    except ImportError as exc:
        raise GoogleSheetsError(
            "Google Sheets support is not installed. Install `gspread` and `google-auth`.") from exc

    if config.GOOGLE_SHEETS_CREDENTIALS_JSON:
        import json                                               # noqa: PLC0415
        try:
            info = json.loads(config.GOOGLE_SHEETS_CREDENTIALS_JSON)
        except ValueError as exc:
            # never echo the value itself
            raise GoogleSheetsError("GOOGLE_SHEETS_CREDENTIALS_JSON is not valid JSON.") from exc
        try:
            creds = Credentials.from_service_account_info(info, scopes=list(SCOPES))
        except Exception as exc:  # noqa: BLE001
            raise GoogleSheetsError("The Google service-account credentials are invalid.") from exc
    elif config.GOOGLE_SHEETS_CREDENTIALS_FILE:
        try:
            creds = Credentials.from_service_account_file(
                config.GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=list(SCOPES))
        except FileNotFoundError as exc:
            raise GoogleSheetsError("The Google credentials file was not found at the configured "
                                    "path.") from exc
        except Exception as exc:  # noqa: BLE001
            raise GoogleSheetsError("The Google service-account credentials file is invalid.") from exc
    else:
        raise GoogleSheetsError("Google Sheets credentials are not configured "
                                "(set GOOGLE_SHEETS_CREDENTIALS_FILE or GOOGLE_SHEETS_CREDENTIALS_JSON).")
    try:
        return gspread.authorize(creds)
    except Exception as exc:  # noqa: BLE001
        raise GoogleSheetsError("Could not authenticate with Google Sheets.") from exc


# =============================================================================
# Publisher (network layer; the client is injectable for tests)
# =============================================================================

class GoogleSheetsPublisher:
    """Publishes canonical rows into three managed worksheets. ``client`` may be any object exposing
    the gspread surface used here (``create`` / ``open_by_key``), which is how tests inject a fake."""

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            self._client = _build_client()
        return self._client

    # --- error translation --------------------------------------------------

    @staticmethod
    def _translate(exc) -> GoogleSheetsError:
        """Map a provider/transport error into a user-safe message. Never includes credentials, tokens,
        headers, or a raw stack trace."""
        name = type(exc).__name__
        if name == "SpreadsheetNotFound":
            return GoogleSheetsError("Spreadsheet not found — check the ID or URL, and that the "
                                     "service account has access to it.")
        if name == "WorksheetNotFound":
            return GoogleSheetsError("A managed worksheet could not be found or created.")
        status = None
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
        if status is None:
            args = getattr(exc, "args", None) or []
            if args and isinstance(args[0], dict):
                status = args[0].get("code")
        if status in (401, 403):
            return GoogleSheetsError("Access denied — share the spreadsheet with the service account "
                                     "and confirm its permissions.")
        if status == 404:
            return GoogleSheetsError("Spreadsheet not found — check the ID or URL.")
        if status == 429:
            return GoogleSheetsError("Google Sheets rate limit reached — wait a moment and retry.",
                                     transient=True)
        if isinstance(status, int) and status >= 500:
            return GoogleSheetsError("Google Sheets is temporarily unavailable — retry shortly.",
                                     transient=True)
        if name in ("ConnectionError", "Timeout", "ReadTimeout", "RequestException"):
            return GoogleSheetsError("Network error contacting Google Sheets — retry shortly.",
                                     transient=True)
        return GoogleSheetsError(f"Google Sheets request failed ({name}).")

    # --- worksheet replacement (deterministic full replacement) -------------

    def _replace_worksheet(self, spreadsheet, title: str, values: list, *, wrap_columns=()) -> list:
        """Clear + resize + rewrite ONE managed worksheet. Creates it when absent. Unrelated worksheets
        in the spreadsheet are never read, modified, or deleted."""
        warnings = []
        rows_needed = max(len(values), 1)
        cols_needed = max((len(r) for r in values), default=1)
        try:
            worksheet = spreadsheet.worksheet(title)
        except Exception as exc:  # noqa: BLE001  -- WorksheetNotFound (or an API error)
            if type(exc).__name__ != "WorksheetNotFound":
                raise self._translate(exc) from exc
            try:
                worksheet = spreadsheet.add_worksheet(title, rows=rows_needed + 10, cols=cols_needed)
            except Exception as add_exc:  # noqa: BLE001
                raise self._translate(add_exc) from add_exc
        try:
            worksheet.clear()
            worksheet.resize(rows=rows_needed, cols=cols_needed)
            worksheet.update(values, range_name="A1")
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

        # minimal, best-effort formatting — content correctness outranks styling, so a formatting
        # failure is reported as a warning instead of failing an already-correct data write.
        try:
            worksheet.freeze(rows=1)
            worksheet.format("1:1", {"textFormat": {"bold": True}})
            if len(values) > 1:
                worksheet.set_basic_filter()
            worksheet.columns_auto_resize(0, cols_needed)
            header = values[0] if values else []
            for name in wrap_columns:
                if name in header:
                    letter = chr(ord("A") + header.index(name)) if header.index(name) < 26 else None
                    if letter:
                        worksheet.format(f"{letter}2:{letter}",
                                         {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"})
        except Exception:  # noqa: BLE001
            warnings.append(f"Data written to '{title}', but formatting could not be applied.")
        return warnings

    # --- public API ---------------------------------------------------------

    def publish_workbook(self, main_rows, ai_rows, summary_rows, target: PublishTarget, *,
                         confirmed: bool = False) -> PublishResult:
        """Validate, then publish the three managed worksheets. Refuses before any external call when
        validation fails, so a rejected publish never writes partially."""
        issues = validate_publish(main_rows, ai_rows, target, confirmed=confirmed,
                                  credentials_available=(self._client is not None
                                                         or credentials_available()))
        if issues:
            raise GoogleSheetsError("; ".join(issues))

        client = self._get_client()
        if target.mode == MODE_CREATE:
            try:
                spreadsheet = client.create(target.title.strip())
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc) from exc
        else:
            sheet_id = extract_spreadsheet_id(target.spreadsheet_id)
            try:
                spreadsheet = client.open_by_key(sheet_id)      # explicit target; never auto-created
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc) from exc

        warnings = []
        warnings += self._replace_worksheet(spreadsheet, SHEET_LEADS,
                                            rows_to_values(export.MAIN_COLUMNS, main_rows),
                                            wrap_columns=_WRAP_COLUMNS)
        warnings += self._replace_worksheet(spreadsheet, SHEET_AI,
                                            rows_to_values(export.AI_COLUMNS, ai_rows),
                                            wrap_columns=_WRAP_COLUMNS)
        warnings += self._replace_worksheet(spreadsheet, SHEET_SUMMARY,
                                            summary_to_values(summary_rows))

        return PublishResult(
            spreadsheet_id=getattr(spreadsheet, "id", ""),
            spreadsheet_url=getattr(spreadsheet, "url", ""),
            publish_mode=target.mode,
            worksheets_updated=list(MANAGED_WORKSHEETS),
            row_counts={SHEET_LEADS: len(main_rows), SHEET_AI: len(ai_rows),
                        SHEET_SUMMARY: len(summary_rows)},
            published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            warnings=warnings)
