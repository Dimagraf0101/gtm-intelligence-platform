"""Deterministic document-ingestion for the ICP Generator (Sprint 4.1B).

Accepts uploaded company materials (PDF / DOCX / PPTX / TXT / Markdown), extracts readable text
safely, preserves source attribution, and reports extraction warnings. Pure/offline:

  - no LLM, no network, no Anthropic API;
  - no OCR, no images, no websites/URLs, no spreadsheets, no CRM;
  - PDF via pypdf (already a dependency); DOCX/PPTX via the stdlib (zipfile + xml.etree), since
    OOXML files are zipped XML — no extra dependency required.

It never executes embedded content, never logs full document text, and behaves identically
regardless of which future entry point (generate_new / standardize_existing) supplied the files.
Companion: ``source_package.py``.
"""
from __future__ import annotations

import io
import re
import logging
import hashlib
import zipfile
from dataclasses import dataclass, field, asdict
from xml.etree import ElementTree as ET

# pypdf logs recoverable structural issues (e.g. "EOF marker not found") on corrupt PDFs; we handle
# those as per-file failures, so quiet its logger to keep output clean. No document text is logged.
logging.getLogger("pypdf").setLevel(logging.ERROR)

# --- extraction statuses -----------------------------------------------------

SUCCESS = "success"
PARTIAL = "partial"
FAILED = "failed"
UNSUPPORTED = "unsupported"

# --- source categories (never inferred from filename) ------------------------

SOURCE_CATEGORIES = (
    "existing_icp", "pitch_deck", "sales_deck", "portfolio", "case_study",
    "service_catalogue", "commercial_proposal", "discovery_notes", "meeting_notes",
    "company_presentation", "other",
)
DEFAULT_CATEGORY = "other"

# --- supported formats -------------------------------------------------------

_EXT_TO_TYPE = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
}
_TYPE_TO_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "markdown": "text/markdown",
    "unsupported": "application/octet-stream",
}
SUPPORTED_TYPES = ("pdf", "docx", "pptx", "txt", "markdown")

# --- conservative size limits (local MVP) ------------------------------------

MAX_FILE_BYTES = 25 * 1024 * 1024          # 25 MB per file
MAX_CHARS_PER_SOURCE = 200_000             # ~200k chars per extracted source


# --- model -------------------------------------------------------------------

@dataclass
class SourceDocument:
    source_id: str = ""
    filename: str = ""
    file_type: str = "unsupported"
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    extraction_status: str = FAILED
    extracted_text: str = ""
    extracted_character_count: int = 0
    extraction_warnings: list[str] = field(default_factory=list)
    content_truncated: bool = False
    source_category: str = DEFAULT_CATEGORY
    original_order: int = 0
    content_hash: str = ""                 # sha256 of raw bytes (for duplicate detection)
    duplicate_of: str | None = None        # source_id of the first identical upload, if any

    def to_dict(self) -> dict:
        # asdict never includes raw binary — only the derived, text-safe fields above.
        return asdict(self)


# --- public helpers ----------------------------------------------------------

def detect_file_type(filename: str) -> str:
    """Return one of SUPPORTED_TYPES or 'unsupported', based on the filename extension only."""
    name = (filename or "").lower().strip()
    for ext, ftype in _EXT_TO_TYPE.items():
        if name.endswith(ext):
            return ftype
    return "unsupported"


def normalize_text(text: str) -> str:
    """Normalize whitespace/control chars without rewriting or summarizing content.

    - strip null bytes and non-tab/newline C0 control chars + DEL;
    - normalize CRLF/CR to LF;
    - collapse runs of spaces/tabs to a single space and trim trailing spaces per line;
    - collapse 2+ blank lines to a single blank line.
    Meaningful punctuation, headings, and list markers are preserved.
    """
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # drop C0 controls except \n and \t, plus DEL
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # collapse intra-line whitespace, trim trailing spaces
    lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    # collapse 3+ newlines (2+ blank lines) to exactly one blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_source_document(
    filename: str,
    data: bytes,
    *,
    category: str = DEFAULT_CATEGORY,
    order: int = 0,
    max_chars: int = MAX_CHARS_PER_SOURCE,
) -> SourceDocument:
    """Extract one uploaded source into a SourceDocument (never raises on bad input)."""
    data = data or b""
    ftype = detect_file_type(filename)
    category = category if category in SOURCE_CATEGORIES else DEFAULT_CATEGORY
    doc = SourceDocument(
        source_id=f"src-{order:03d}",
        filename=filename or "(unnamed)",
        file_type=ftype,
        mime_type=_TYPE_TO_MIME.get(ftype, "application/octet-stream"),
        size_bytes=len(data),
        source_category=category,
        original_order=order,
        content_hash=hashlib.sha256(data).hexdigest(),
    )

    if ftype == "unsupported":
        doc.extraction_status = UNSUPPORTED
        doc.extraction_warnings.append("Unsupported file format; not extracted.")
        return doc

    if doc.size_bytes == 0:
        doc.extraction_status = FAILED
        doc.extraction_warnings.append("Empty document (0 bytes).")
        return doc

    if doc.size_bytes > MAX_FILE_BYTES:
        doc.extraction_status = FAILED
        doc.extraction_warnings.append(
            f"File exceeds maximum size ({doc.size_bytes} > {MAX_FILE_BYTES} bytes); not extracted.")
        return doc

    warnings: list[str] = []
    try:
        if ftype == "pdf":
            text, warnings = _extract_pdf(data)
        elif ftype == "docx":
            text, warnings = _extract_docx(data)
        elif ftype == "pptx":
            text, warnings = _extract_pptx(data)
        else:  # txt / markdown
            text, warnings = _extract_text(data)
    except Exception as exc:  # noqa: BLE001 — any parse failure is a per-file failure, not a crash
        doc.extraction_status = FAILED
        doc.extraction_warnings.append(f"Corrupt or unreadable document ({type(exc).__name__}).")
        return doc

    text = normalize_text(text)
    doc.extraction_warnings.extend(warnings)

    if not text:
        doc.extraction_status = FAILED
        doc.extraction_warnings.append(
            "No extractable text (possibly a scanned/image-only document or an empty file).")
        doc.extracted_text = ""
        doc.extracted_character_count = 0
        return doc

    if len(text) > max_chars:
        text = text[:max_chars]
        doc.content_truncated = True
        doc.extraction_warnings.append(
            f"Content truncated to the per-source limit of {max_chars} characters.")

    doc.extracted_text = text
    doc.extracted_character_count = len(text)
    # PARTIAL when something was dropped/truncated but usable text remains; else SUCCESS.
    doc.extraction_status = PARTIAL if (doc.content_truncated or warnings) else SUCCESS
    return doc


def extract_multiple_sources(files, *, max_files: int | None = None,
                             max_chars: int = MAX_CHARS_PER_SOURCE) -> list[SourceDocument]:
    """Extract many uploads, preserving upload order.

    ``files`` is a sequence of (filename, data) or (filename, data, category) tuples, or dicts with
    keys {filename, data, category}. Files beyond ``max_files`` are recorded as failed (not
    processed) so the limit is explicit and auditable — never silently dropped.
    """
    from source_package import MAX_FILES  # single source of truth for the count limit
    limit = MAX_FILES if max_files is None else max_files

    docs: list[SourceDocument] = []
    for i, item in enumerate(files):
        filename, data, category = _unpack(item)
        if i >= limit:
            skipped = SourceDocument(
                source_id=f"src-{i:03d}", filename=filename or "(unnamed)",
                file_type=detect_file_type(filename), size_bytes=len(data or b""),
                extraction_status=FAILED, source_category=(category or DEFAULT_CATEGORY),
                original_order=i, content_hash=hashlib.sha256(data or b"").hexdigest())
            skipped.extraction_warnings.append(
                f"Exceeds the maximum of {limit} files; not processed.")
            docs.append(skipped)
            continue
        docs.append(extract_source_document(filename, data, category=category or DEFAULT_CATEGORY,
                                            order=i, max_chars=max_chars))
    return docs


def _unpack(item):
    if isinstance(item, dict):
        return item.get("filename", ""), item.get("data", b""), item.get("category", DEFAULT_CATEGORY)
    if len(item) == 3:
        return item[0], item[1], item[2]
    return item[0], item[1], DEFAULT_CATEGORY


# --- per-format extractors ---------------------------------------------------

def _local(el) -> str:
    """Local XML tag name without namespace (OOXML uses namespaced tags)."""
    return el.tag.rsplit("}", 1)[-1]


def _extract_pdf(data: bytes):
    from pypdf import PdfReader
    warnings: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            warnings.append("PDF is encrypted; could not decrypt.")
            return "", warnings
    pages = []
    for page in reader.pages:               # preserve page order
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            warnings.append("A PDF page could not be read; skipped.")
            pages.append("")
    text = "\n".join(pages)
    if not text.strip():
        # No OCR: do not treat a scanned/image-only PDF as a success.
        warnings.append("No text layer found (the PDF may be scanned or image-only; OCR is not used).")
    return text, warnings


def _extract_docx(data: bytes):
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "word/document.xml" not in z.namelist():
            raise ValueError("not a DOCX (missing word/document.xml)")
        root = ET.fromstring(z.read("word/document.xml"))
    lines: list[str] = []
    has_embedded = False
    # Iterating paragraphs in document order captures body paragraphs AND table-cell paragraphs
    # (table cells contain w:p), preserving logical order.
    for el in root.iter():
        ln = _local(el)
        if ln == "p":
            parts = [t.text for t in el.iter() if _local(t) == "t" and t.text]
            if parts:
                lines.append("".join(parts))
        elif ln in ("drawing", "pict", "object", "OLEObject"):
            has_embedded = True
    if has_embedded:
        warnings.append("Images/embedded objects present; ignored (text only).")
    return "\n".join(lines), warnings


def _extract_pptx(data: bytes):
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        if not names:
            raise ValueError("not a PPTX (no slides found)")
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))  # slide order
        slides = []
        for idx, name in enumerate(names, start=1):
            root = ET.fromstring(z.read(name))
            lines = []
            for el in root.iter():
                if _local(el) == "p":       # a:p — paragraphs in titles, text boxes, table cells
                    parts = [t.text for t in el.iter() if _local(t) == "t" and t.text]
                    if parts:
                        lines.append("".join(parts))
            if lines:
                slides.append("\n".join(lines))
            else:
                warnings.append(f"Slide {idx} contains no readable text.")
    return "\n\n".join(slides), warnings


def _extract_text(data: bytes):
    warnings: list[str] = []
    for enc in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(enc), warnings
        except UnicodeDecodeError:
            continue
    for enc in ("cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            warnings.append(f"UTF-8 decode failed; used encoding fallback ({enc}).")
            return text, warnings
        except UnicodeDecodeError:
            continue
    text = data.decode("utf-8", errors="replace")
    warnings.append("UTF-8 decode failed; used replacement fallback (some characters lost).")
    return text, warnings
