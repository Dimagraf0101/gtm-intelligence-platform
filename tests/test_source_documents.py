"""Tests for the document-ingestion layer (pipeline/source_documents.py).

Offline, no LLM, no network, no pytest. All fixtures are built programmatically — no binary blobs
are committed to the repo.
    ./.venv/bin/python tests/test_source_documents.py
"""
import sys
import io
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import source_documents as sd     # noqa: E402


# --- programmatic fixtures ---------------------------------------------------

def make_text_pdf(lines):
    """A minimal, valid, text-based single-page PDF with correct xref offsets."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    y = 720
    stream = "\n".join(f"BT /F1 12 Tf 72 {y - 16 * i} Td ({esc(ln)}) Tj ET"
                       for i, ln in enumerate(lines))
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref_pos = len(out.encode("latin-1"))
    n = len(objs)
    out += f"xref\n0 {n + 1}\n0000000000 65535 f \n"
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets)
    out += f"trailer\n<< /Size {n + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF"
    return out.encode("latin-1")


def make_blank_pdf():
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def make_docx(paragraphs=("Para 1",), rows=None, with_image=False):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    if with_image:
        body += "<w:p><w:r><w:drawing/></w:r></w:p>"
    if rows:
        trs = ""
        for row in rows:
            trs += "<w:tr>" + "".join(
                f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
        body += f"<w:tbl>{trs}</w:tbl>"
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://w"><w:body>'
           + body + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _sp_text(*lines):
    return ("<p:sp><p:txBody>"
            + "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in lines)
            + "</p:txBody></p:sp>")


def _sp_table(rows):
    trs = ""
    for row in rows:
        trs += "<a:tr>" + "".join(
            f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
            for c in row) + "</a:tr>"
    return f"<a:graphicFrame><a:tbl>{trs}</a:tbl></a:graphicFrame>"


def make_pptx(slide_inners):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, inner in enumerate(slide_inners, 1):
            xml = ('<?xml version="1.0"?><p:sld xmlns:p="http://p" xmlns:a="http://a">'
                   f"<p:cSld><p:spTree>{inner}</p:spTree></p:cSld></p:sld>")
            z.writestr(f"ppt/slides/slide{i}.xml", xml)
    return buf.getvalue()


# --- tests -------------------------------------------------------------------

def test_detect_file_type():
    assert sd.detect_file_type("a.pdf") == "pdf"
    assert sd.detect_file_type("A.DOCX") == "docx"
    assert sd.detect_file_type("deck.pptx") == "pptx"
    assert sd.detect_file_type("notes.md") == "markdown"
    assert sd.detect_file_type("notes.markdown") == "markdown"
    assert sd.detect_file_type("x.txt") == "txt"
    assert sd.detect_file_type("data.xlsx") == "unsupported"


def test_normalize_text():
    raw = "a\x00b\r\nc\r\n\n\n\nd    e\t\tf   "
    out = sd.normalize_text(raw)
    assert "\x00" not in out
    assert "\r" not in out
    assert "d e f" in out          # collapsed spaces/tabs, trimmed trailing
    assert "\n\n\n" not in out     # blank lines collapsed


def test_pdf_text_extraction():
    doc = sd.extract_source_document("brief.pdf", make_text_pdf(["Hello World", "Second line"]))
    assert doc.file_type == "pdf"
    assert doc.extraction_status == sd.SUCCESS
    assert "Hello World" in doc.extracted_text
    assert doc.extracted_text.index("Hello World") < doc.extracted_text.index("Second line")
    assert doc.extracted_character_count == len(doc.extracted_text)


def test_pdf_scanned_or_empty_warns():
    doc = sd.extract_source_document("scan.pdf", make_blank_pdf())
    assert doc.extraction_status == sd.FAILED
    assert any("no text" in w.lower() or "scanned" in w.lower() for w in doc.extraction_warnings)
    assert doc.extracted_text == ""


def test_corrupt_pdf_handled():
    doc = sd.extract_source_document("broken.pdf", b"%PDF-1.4 not really a pdf \x00\x01\x02")
    assert doc.extraction_status == sd.FAILED
    assert doc.extracted_text == ""
    assert doc.extraction_warnings                      # a warning, not a crash


def test_docx_paragraph_extraction():
    doc = sd.extract_source_document("doc.docx", make_docx(["First", "Second", "Third"]))
    assert doc.extraction_status == sd.SUCCESS
    assert doc.extracted_text.splitlines()[:3] == ["First", "Second", "Third"]


def test_docx_table_extraction():
    doc = sd.extract_source_document(
        "doc.docx", make_docx(["Intro"], rows=[["Cell A", "Cell B"], ["Cell C", "Cell D"]]))
    for cell in ("Cell A", "Cell B", "Cell C", "Cell D"):
        assert cell in doc.extracted_text
    assert doc.extracted_text.index("Intro") < doc.extracted_text.index("Cell A")


def test_docx_embedded_object_warning():
    doc = sd.extract_source_document("doc.docx", make_docx(["Text here"], with_image=True))
    assert "Text here" in doc.extracted_text
    assert doc.extraction_status == sd.PARTIAL
    assert any("embedded" in w.lower() or "image" in w.lower() for w in doc.extraction_warnings)


def test_pptx_slide_order():
    inners = [_sp_text(f"Slide {i}") for i in range(1, 12)]   # 11 slides
    doc = sd.extract_source_document("deck.pptx", make_pptx(inners))
    assert doc.extraction_status == sd.SUCCESS
    # numeric slide order, not lexical (slide2 before slide10)
    assert doc.extracted_text.index("Slide 2") < doc.extracted_text.index("Slide 10")
    assert doc.extracted_text.index("Slide 10") < doc.extracted_text.index("Slide 11")


def test_pptx_table_extraction():
    inner = _sp_text("Title") + _sp_table([["R1C1", "R1C2"], ["R2C1", "R2C2"]])
    doc = sd.extract_source_document("deck.pptx", make_pptx([inner]))
    for cell in ("R1C1", "R1C2", "R2C1", "R2C2"):
        assert cell in doc.extracted_text


def test_pptx_empty_slide_warns():
    doc = sd.extract_source_document("deck.pptx", make_pptx([_sp_text("Has text"), "<p:sp/>"]))
    assert "Has text" in doc.extracted_text
    assert any("no readable text" in w.lower() for w in doc.extraction_warnings)


def test_txt_utf8():
    doc = sd.extract_source_document("n.txt", "Hello — café".encode("utf-8"))
    assert doc.extraction_status == sd.SUCCESS
    assert "café" in doc.extracted_text


def test_txt_encoding_fallback():
    doc = sd.extract_source_document("n.txt", "résumé".encode("cp1252"))
    assert doc.extraction_status == sd.PARTIAL
    assert "résumé" in doc.extracted_text
    assert any("fallback" in w.lower() for w in doc.extraction_warnings)


def test_markdown_preserved():
    md = "# Heading\n\n- item one\n- item two\n\n**bold** text"
    doc = sd.extract_source_document("readme.md", md.encode("utf-8"))
    assert doc.file_type == "markdown"
    assert "# Heading" in doc.extracted_text
    assert "- item one" in doc.extracted_text
    assert "**bold**" in doc.extracted_text


def test_unsupported_format():
    doc = sd.extract_source_document("book.xlsx", b"PK\x03\x04 whatever")
    assert doc.extraction_status == sd.UNSUPPORTED
    assert doc.extracted_text == ""
    assert any("unsupported" in w.lower() for w in doc.extraction_warnings)


def test_empty_file():
    doc = sd.extract_source_document("empty.txt", b"")
    assert doc.extraction_status == sd.FAILED
    assert any("empty" in w.lower() for w in doc.extraction_warnings)


def test_individual_truncation():
    big = ("word " * 5000).encode("utf-8")
    doc = sd.extract_source_document("big.txt", big, max_chars=100)
    assert doc.content_truncated is True
    assert doc.extracted_character_count == 100
    assert doc.extraction_status == sd.PARTIAL
    assert any("truncat" in w.lower() for w in doc.extraction_warnings)


def test_category_defaults_to_other_when_invalid():
    doc = sd.extract_source_document("x.txt", b"hi", category="totally_made_up")
    assert doc.source_category == "other"


def test_category_not_inferred_from_filename():
    # A filename that looks like a sensitive category must NOT set that category by itself.
    doc = sd.extract_source_document("existing_icp_confidential.pdf", make_text_pdf(["x"]))
    assert doc.source_category == "other"


def test_valid_category_kept():
    doc = sd.extract_source_document("d.pdf", make_text_pdf(["x"]), category="pitch_deck")
    assert doc.source_category == "pitch_deck"


def test_extract_multiple_preserves_order():
    files = [("a.txt", b"aaa"), ("b.md", b"# b"), ("c.txt", b"ccc")]
    docs = sd.extract_multiple_sources(files)
    assert [d.original_order for d in docs] == [0, 1, 2]
    assert [d.filename for d in docs] == ["a.txt", "b.md", "c.txt"]


def test_max_file_count_limit():
    files = [(f"f{i}.txt", f"content{i}".encode()) for i in range(5)]
    docs = sd.extract_multiple_sources(files, max_files=3)
    assert docs[0].extraction_status == sd.SUCCESS
    assert docs[3].extraction_status == sd.FAILED
    assert any("maximum" in w.lower() for w in docs[3].extraction_warnings)


def test_to_dict_json_serializable_no_binary():
    doc = sd.extract_source_document("d.docx", make_docx(["Body"]))
    d = doc.to_dict()
    j = json.dumps(d)                                   # must be JSON-serializable
    assert "extracted_text" in d
    assert isinstance(d["content_hash"], str)
    # raw bytes never appear
    assert "data" not in d and b"" != d.get("extracted_text", "")


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
