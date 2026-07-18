"""Google Sheets publisher tests (Sprint 14).

Proves the external publishing boundary: deterministic preparation over **canonical rows only**,
validation before any external call, explicit Create-vs-Update targets, idempotent re-publishing,
user-safe failure translation, and credentials that never leak. Uses a deterministic **fake** gspread
client — NO live Google credentials and no network. Offline, no pytest:

    ./.venv/bin/python tests/test_google_sheets_publisher.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import export                                              # noqa: E402
import config                                              # noqa: E402
import lead_review as lr                                   # noqa: E402
import review_view as rv                                   # noqa: E402
import review_export as rx                                 # noqa: E402
import workspace_store as store                            # noqa: E402
from integrations import google_sheets_publisher as gs     # noqa: E402
from review_view import ReviewRow                          # noqa: E402

_SHEET_ID = "1AbC_dEfGhIjKlMnOpQrStUvWxYz012345"
_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{_SHEET_ID}/edit#gid=0"


# --- deterministic fake gspread surface (mirrors the verified real signatures) ---------------

class FakeWorksheet:
    def __init__(self, title, rows=100, cols=26):
        self.title, self.values = title, []
        self.rows, self.cols = rows, cols
        self.cleared = 0
        self.formatted = False
        self.fail_on_update = None

    def clear(self):
        self.cleared += 1
        self.values = []

    def resize(self, rows=None, cols=None):
        self.rows, self.cols = rows or self.rows, cols or self.cols

    def update(self, values, range_name=None):
        if self.fail_on_update:
            raise self.fail_on_update
        self.values = [list(r) for r in values]

    def freeze(self, rows=None, cols=None):
        self.formatted = True

    def format(self, ranges, format):
        self.formatted = True

    def set_basic_filter(self, name=None):
        self.formatted = True

    def columns_auto_resize(self, start, end):
        self.formatted = True


class FakeSpreadsheet:
    def __init__(self, sheet_id, title):
        self.id, self.title = sheet_id, title
        self.url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        self._sheets = {}

    def worksheet(self, title):
        if title not in self._sheets:
            raise type("WorksheetNotFound", (Exception,), {})()
        return self._sheets[title]

    def worksheets(self, exclude_hidden=False):
        return list(self._sheets.values())

    def add_worksheet(self, title, rows, cols, index=None):
        ws = FakeWorksheet(title, rows, cols)
        self._sheets[title] = ws
        return ws


class FakeClient:
    """Records every spreadsheet ever created so tests can assert no silent duplication."""

    def __init__(self, *, existing=None, create_error=None, open_error=None):
        self.spreadsheets = dict(existing or {})
        self.created = []
        self.create_error, self.open_error = create_error, open_error

    def create(self, title, folder_id=None):
        if self.create_error:
            raise self.create_error
        sheet_id = f"NEWID{len(self.created):015d}xxxxx"
        sheet = FakeSpreadsheet(sheet_id, title)
        self.spreadsheets[sheet_id] = sheet
        self.created.append(sheet)
        return sheet

    def open_by_key(self, key):
        if self.open_error:
            raise self.open_error
        if key not in self.spreadsheets:
            raise type("SpreadsheetNotFound", (Exception,), {})()
        return self.spreadsheets[key]


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _api_error(status):
    return type("APIError", (Exception,), {})(*[], **{}) if status is None else _mk_api_error(status)


def _mk_api_error(status):
    err = type("APIError", (Exception,), {})()
    err.response = FakeResponse(status)
    return err


# --- canonical row fixtures --------------------------------------------------

def _rows(n=3, status=lr.REVIEW_APPROVED):
    return [ReviewRow(lead_id=f"ld{i}", company=f"Co{i}", contact=f"P{i}", first_name=f"F{i}",
                      last_name=f"L{i}", title="CTO", location="US", industry="FinTech",
                      company_size="51-200", linkedin_url=f"https://linkedin.com/in/p{i}",
                      company_website=f"https://co{i}.com", score=80 - i,
                      priority="Priority 2", confidence="high", reason="r",
                      signals=["a"], score_breakdown="Fit:36 Eng:24",
                      internal_category="B / Normal", review_status=status)
            for i in range(n)]


def _payload(n=3):
    rows = _rows(n)
    return (rx.build_main_rows(rows), rx.build_ai_rows(rows),
            rx.build_summary_rows(rows, "camp", "icp", "now"))


# =============================================================================
# Preparation (pure, deterministic)
# =============================================================================

def test_target_parsing_id_url_and_refusal():
    assert gs.extract_spreadsheet_id(_SHEET_URL) == _SHEET_ID
    assert gs.extract_spreadsheet_id(_SHEET_ID) == _SHEET_ID
    assert gs.extract_spreadsheet_id("  " + _SHEET_ID + " ") == _SHEET_ID
    for junk in ("", "not-a-sheet", "https://example.com/x", "short"):
        assert gs.extract_spreadsheet_id(junk) == "", junk       # unknown, never guessed


def test_values_use_exact_canonical_columns_and_order():
    main, ai, summary = _payload()
    mv = gs.rows_to_values(export.MAIN_COLUMNS, main)
    av = gs.rows_to_values(export.AI_COLUMNS, ai)
    assert mv[0] == list(export.MAIN_COLUMNS) and av[0] == list(export.AI_COLUMNS)
    assert len(mv) == len(main) + 1 and len(av) == len(ai) + 1
    assert all(len(r) == len(export.MAIN_COLUMNS) for r in mv)
    sv = gs.summary_to_values(summary)
    assert sv[0] == ["Field", "Value"] and len(sv) == len(summary) + 1


def test_empty_values_stay_empty_nothing_reconstructed():
    rows = [ReviewRow(lead_id="ld1", company="Acme", priority="Priority 3", score=50)]
    main = rx.build_main_rows(rows)
    values = gs.rows_to_values(export.MAIN_COLUMNS, main)
    header, data = values[0], values[1]
    for col in ("First Name", "Last Name", "Job Started", "Number of Connections",
                "Company LinkedIn URL", "LinkedIn Founded Year", "LinkedIn Specialities"):
        assert data[header.index(col)] == "", col                # never invented
    assert data[header.index("Company")] == "Acme"


def test_duplicate_detection():
    main, _, _ = _payload(3)
    assert gs.find_duplicate_rows(main) == []
    assert len(gs.find_duplicate_rows(main + [main[0]])) == 1
    # falls back to person+company when no LinkedIn URL is present
    a = {"LinkedIn URL": "", "First Name": "J", "Last Name": "D", "Company": "Acme"}
    assert len(gs.find_duplicate_rows([a, dict(a)])) == 1


def test_row_counts_are_correct():
    main, ai, summary = _payload(5)
    pub = gs.GoogleSheetsPublisher(client=FakeClient())
    res = pub.publish_workbook(main, ai, summary,
                               gs.PublishTarget(mode=gs.MODE_CREATE, title="T"), confirmed=True)
    assert res.row_counts == {"Leads": 5, "AI Details": 5, "Summary": len(summary)}


# =============================================================================
# Validation before any external call
# =============================================================================

def test_validation_blocks_before_publishing():
    main, ai, _ = _payload()
    create = gs.PublishTarget(mode=gs.MODE_CREATE, title="T")
    assert gs.validate_publish(main, ai, create, confirmed=True) == []
    # confirmation required
    assert gs.validate_publish(main, ai, create, confirmed=False)
    # empty scope
    assert gs.validate_publish([], ai, create, confirmed=True)
    # missing credentials
    assert gs.validate_publish(main, ai, create, confirmed=True, credentials_available=False)
    # create without a name / update without a valid target
    assert gs.validate_publish(main, ai, gs.PublishTarget(mode=gs.MODE_CREATE, title=" "),
                               confirmed=True)
    assert gs.validate_publish(main, ai, gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id="junk"),
                               confirmed=True)
    # non-canonical rows
    broken = [{"Company": "X"}]
    assert gs.validate_publish(broken, ai, create, confirmed=True)
    # duplicates
    assert gs.validate_publish(main + [main[0]], ai, create, confirmed=True)


def test_no_external_call_when_validation_fails():
    main, ai, summary = _payload()
    client = FakeClient()
    pub = gs.GoogleSheetsPublisher(client=client)
    try:
        pub.publish_workbook(main, ai, summary, gs.PublishTarget(mode=gs.MODE_CREATE, title="T"),
                             confirmed=False)                     # not confirmed
        assert False, "published without confirmation"
    except gs.GoogleSheetsError:
        pass
    assert client.created == []                                   # nothing was created


# =============================================================================
# Create New
# =============================================================================

def test_create_new_makes_one_spreadsheet_with_three_worksheets():
    main, ai, summary = _payload(4)
    client = FakeClient()
    res = gs.GoogleSheetsPublisher(client=client).publish_workbook(
        main, ai, summary, gs.PublishTarget(mode=gs.MODE_CREATE, title="Reviewed"), confirmed=True)
    assert len(client.created) == 1
    sheet = client.created[0]
    assert sorted(w.title for w in sheet.worksheets()) == sorted(gs.MANAGED_WORKSHEETS)
    leads = sheet.worksheet(gs.SHEET_LEADS)
    assert leads.values[0] == list(export.MAIN_COLUMNS)           # canonical header
    assert len(leads.values) == 5                                 # header + 4 rows
    assert sheet.worksheet(gs.SHEET_AI).values[0] == list(export.AI_COLUMNS)
    assert res.publish_mode == gs.MODE_CREATE
    assert res.spreadsheet_id and res.spreadsheet_url.startswith("https://")
    assert res.published_at and res.worksheets_updated == list(gs.MANAGED_WORKSHEETS)


# =============================================================================
# Update Existing + idempotency
# =============================================================================

def test_update_existing_does_not_create_and_preserves_other_worksheets():
    main, ai, summary = _payload(3)
    existing = FakeSpreadsheet(_SHEET_ID, "Existing")
    existing.add_worksheet("My Notes", 10, 5)                     # unrelated worksheet
    client = FakeClient(existing={_SHEET_ID: existing})
    res = gs.GoogleSheetsPublisher(client=client).publish_workbook(
        main, ai, summary, gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_URL),
        confirmed=True)
    assert client.created == []                                   # never silently created another
    assert res.spreadsheet_id == _SHEET_ID and res.publish_mode == gs.MODE_UPDATE
    titles = {w.title for w in existing.worksheets()}
    assert "My Notes" in titles                                   # unrelated sheet untouched
    assert set(gs.MANAGED_WORKSHEETS).issubset(titles)


def test_repeated_publish_is_idempotent_no_duplicate_rows():
    main, ai, summary = _payload(3)
    existing = FakeSpreadsheet(_SHEET_ID, "Existing")
    client = FakeClient(existing={_SHEET_ID: existing})
    pub = gs.GoogleSheetsPublisher(client=client)
    target = gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_ID)
    for _ in range(3):
        pub.publish_workbook(main, ai, summary, target, confirmed=True)
    leads = existing.worksheet(gs.SHEET_LEADS)
    assert len(leads.values) == 4                                 # header + 3 rows, never appended
    assert leads.cleared == 3                                     # deterministic full replacement
    assert client.created == []


def test_update_shrinks_when_fewer_rows():
    existing = FakeSpreadsheet(_SHEET_ID, "E")
    client = FakeClient(existing={_SHEET_ID: existing})
    pub = gs.GoogleSheetsPublisher(client=client)
    target = gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_ID)
    big = _payload(6)
    pub.publish_workbook(*big, target, confirmed=True)
    assert len(existing.worksheet(gs.SHEET_LEADS).values) == 7
    small = _payload(2)
    pub.publish_workbook(*small, target, confirmed=True)
    assert len(existing.worksheet(gs.SHEET_LEADS).values) == 3    # stale rows removed


# =============================================================================
# Failure handling (user-safe, no secrets, no stack traces)
# =============================================================================

def test_failure_translation_is_user_safe():
    main, ai, summary = _payload()
    target = gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_ID)
    cases = [(403, "Access denied", False), (404, "not found", False),
             (429, "rate limit", True), (503, "temporarily unavailable", True)]
    for status, fragment, transient in cases:
        client = FakeClient(existing={_SHEET_ID: FakeSpreadsheet(_SHEET_ID, "E")},
                            open_error=_mk_api_error(status))
        try:
            gs.GoogleSheetsPublisher(client=client).publish_workbook(
                main, ai, summary, target, confirmed=True)
            assert False, f"no error raised for {status}"
        except gs.GoogleSheetsError as exc:
            assert fragment.lower() in str(exc).lower(), (status, str(exc))
            assert exc.transient is transient, status
            assert "Traceback" not in str(exc)


def test_spreadsheet_not_found_and_network_error():
    main, ai, summary = _payload()
    client = FakeClient()                                          # id absent -> SpreadsheetNotFound
    try:
        gs.GoogleSheetsPublisher(client=client).publish_workbook(
            main, ai, summary, gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_ID),
            confirmed=True)
        assert False, "missing spreadsheet accepted"
    except gs.GoogleSheetsError as exc:
        assert "not found" in str(exc).lower()

    net = type("ConnectionError", (Exception,), {})()
    client2 = FakeClient(create_error=net)
    try:
        gs.GoogleSheetsPublisher(client=client2).publish_workbook(
            main, ai, summary, gs.PublishTarget(mode=gs.MODE_CREATE, title="T"), confirmed=True)
        assert False, "network error swallowed"
    except gs.GoogleSheetsError as exc:
        assert exc.transient and "network" in str(exc).lower()


def test_write_failure_surfaces_and_does_not_claim_success():
    main, ai, summary = _payload()
    existing = FakeSpreadsheet(_SHEET_ID, "E")
    ws = existing.add_worksheet(gs.SHEET_LEADS, 10, 30)
    ws.fail_on_update = _mk_api_error(500)
    client = FakeClient(existing={_SHEET_ID: existing})
    try:
        gs.GoogleSheetsPublisher(client=client).publish_workbook(
            main, ai, summary, gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_ID),
            confirmed=True)
        assert False, "write failure reported as success"
    except gs.GoogleSheetsError as exc:
        assert exc.transient                                      # 5xx -> retryable, surfaced


def test_formatting_failure_is_a_warning_not_a_failed_publish():
    main, ai, summary = _payload(2)
    existing = FakeSpreadsheet(_SHEET_ID, "E")
    ws = existing.add_worksheet(gs.SHEET_LEADS, 10, 30)
    ws.freeze = lambda **kw: (_ for _ in ()).throw(RuntimeError("no formatting"))
    client = FakeClient(existing={_SHEET_ID: existing})
    res = gs.GoogleSheetsPublisher(client=client).publish_workbook(
        main, ai, summary, gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=_SHEET_ID),
        confirmed=True)
    assert res.warnings and "formatting" in res.warnings[0].lower()
    assert len(ws.values) == 3                                     # data still correct


# =============================================================================
# Credentials / security
# =============================================================================

def test_missing_credentials_blocks_publishing():
    saved = (config.GOOGLE_SHEETS_CREDENTIALS_FILE, config.GOOGLE_SHEETS_CREDENTIALS_JSON)
    config.GOOGLE_SHEETS_CREDENTIALS_FILE = None
    config.GOOGLE_SHEETS_CREDENTIALS_JSON = None
    try:
        assert gs.credentials_available() is False
        main, ai, _ = _payload()
        issues = gs.validate_publish(main, ai, gs.PublishTarget(mode=gs.MODE_CREATE, title="T"),
                                     confirmed=True, credentials_available=False)
        assert any("credentials" in i.lower() for i in issues)
        try:
            gs.GoogleSheetsPublisher()._get_client()               # no injected client, no creds
            assert False, "built a client without credentials"
        except gs.GoogleSheetsError as exc:
            assert "credentials" in str(exc).lower()
    finally:
        config.GOOGLE_SHEETS_CREDENTIALS_FILE, config.GOOGLE_SHEETS_CREDENTIALS_JSON = saved


def test_invalid_inline_credentials_are_refused_without_echoing_the_value():
    saved = (config.GOOGLE_SHEETS_CREDENTIALS_FILE, config.GOOGLE_SHEETS_CREDENTIALS_JSON)
    secret = '{"type":"service_account","private_key":"SUPER-SECRET-VALUE"'   # malformed JSON
    config.GOOGLE_SHEETS_CREDENTIALS_FILE = None
    config.GOOGLE_SHEETS_CREDENTIALS_JSON = secret
    try:
        gs.GoogleSheetsPublisher()._get_client()
        assert False, "invalid credentials accepted"
    except gs.GoogleSheetsError as exc:
        assert "SUPER-SECRET-VALUE" not in str(exc)                # never echoed
        assert "not valid JSON" in str(exc)
    finally:
        config.GOOGLE_SHEETS_CREDENTIALS_FILE, config.GOOGLE_SHEETS_CREDENTIALS_JSON = saved


def test_credentials_never_serialized_into_publish_result_or_workspace():
    main, ai, summary = _payload()
    client = FakeClient()
    res = gs.GoogleSheetsPublisher(client=client).publish_workbook(
        main, ai, summary, gs.PublishTarget(mode=gs.MODE_CREATE, title="T"), confirmed=True)
    blob = json.dumps(res.summary()).lower()
    for marker in ("private_key", "credential", "token", "secret", "service_account"):
        assert marker not in blob, marker


def test_publisher_has_no_domain_or_engine_dependency():
    src = (ROOT / "pipeline" / "integrations" / "google_sheets_publisher.py").read_text(encoding="utf-8")
    import re
    imports = set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.MULTILINE))
    forbidden = {"scoring", "decision", "priority_policy", "qualification_bridge", "qualification_run",
                 "qualification_mapper", "vayne_adapter", "lead_batch", "qualified_lead", "lead_review",
                 "review_view", "icp_project", "workspace_store", "anthropic", "streamlit",
                 "lead_import", "search_strategy"}
    leaked = forbidden & imports
    assert not leaked, f"publisher imports forbidden module(s): {leaked}"


# =============================================================================
# Regression
# =============================================================================

def test_canonical_schema_and_existing_exports_unchanged():
    assert len(export.MAIN_COLUMNS) == 24 and len(export.AI_COLUMNS) == 18
    assert export.MAIN_COLUMNS[0] == "#" and export.MAIN_COLUMNS[-1] == "Reviewer Comment"
    rows = _rows(2)
    assert list(rx.to_main_dataframe(rows).columns) == export.MAIN_COLUMNS
    assert isinstance(rx.to_workbook_bytes(rows, icp_name="ICP"), bytes)   # XLSX path still works
    assert rx.to_main_csv_bytes(rows).decode("utf-8-sig").splitlines()[0].split(",")[:2] == \
        ["#", "Lead Score"]


def test_ui_scope_defaults_and_selection_respected():
    approved = _rows(2, status=lr.REVIEW_APPROVED)
    pending = _rows(2, status=lr.REVIEW_PENDING)
    for i, r in enumerate(pending):
        object.__setattr__(r, "lead_id", f"p{i}")
    allrows = approved + pending
    assert len(rx.select_rows(allrows, rx.SCOPE_APPROVED)) == 2          # default scope
    assert len(rx.select_rows(allrows, rx.SCOPE_ALL)) == 4
    assert len(rx.select_rows(allrows, rx.SCOPE_SELECTED, selected_ids=["p0"])) == 1
    # an empty selection blocks publishing via validation
    assert gs.validate_publish([], [], gs.PublishTarget(mode=gs.MODE_CREATE, title="T"),
                               confirmed=True)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
