"""Tests for the SourcePackage merge layer (pipeline/source_package.py).

Offline, no LLM, no network, no pytest. Fixtures are reused from test_source_documents (built
programmatically; no binary blobs committed).
    ./.venv/bin/python tests/test_source_package.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tests"))

import source_documents as sd     # noqa: E402
import source_package as sp       # noqa: E402
from test_source_documents import make_text_pdf, make_blank_pdf, make_docx  # noqa: E402


def _pkg_from(files, **kw):
    return sp.build_package_from_files(files, **kw)


def test_merge_has_boundaries_and_order():
    files = [("a.txt", b"Alpha content", "pitch_deck"),
             ("b.txt", b"Beta content", "case_study")]
    pkg = _pkg_from(files)
    mt = pkg.merged_text
    assert mt.count("--- SOURCE START ---") == 2
    assert "Filename: a.txt" in mt and "Category: pitch_deck" in mt
    assert "Alpha content" in mt and "Beta content" in mt
    assert mt.index("Alpha content") < mt.index("Beta content")     # upload order preserved


def test_failed_and_unsupported_excluded_from_merge():
    files = [("good.txt", b"Usable text", "other"),
             ("book.xlsx", b"PK junk", "other"),          # unsupported
             ("scan.pdf", make_blank_pdf(), "other")]     # failed (no text)
    pkg = _pkg_from(files)
    assert "Usable text" in pkg.merged_text
    assert "book.xlsx" not in pkg.merged_text
    assert "scan.pdf" not in pkg.merged_text
    assert pkg.failed_source_count == 2
    assert pkg.successful_source_count == 1
    assert pkg.is_complete is False


def test_duplicate_detection():
    same = b"Identical bytes here"
    files = [("first.txt", same, "other"),
             ("second.txt", same, "other"),
             ("unique.txt", b"different", "other")]
    pkg = _pkg_from(files)
    # merged only once for the duplicated content
    assert pkg.merged_text.count("Identical bytes here") == 1
    dup = pkg.source_documents[1]
    assert dup.duplicate_of == pkg.source_documents[0].source_id
    assert any("duplicate" in w.lower() for w in pkg.package_warnings)
    assert "different" in pkg.merged_text


def test_deterministic_merged_output():
    files = [("a.txt", b"Alpha", "pitch_deck"), ("b.md", b"# Beta", "case_study")]
    a = _pkg_from(files).merged_text            # fresh extraction each build
    b = _pkg_from(files).merged_text
    assert a == b


def test_package_total_truncation():
    files = [("a.txt", b"X" * 60, "other"), ("b.txt", b"Y" * 60, "other")]
    pkg = _pkg_from(files, max_total_chars=80)
    assert pkg.total_extracted_characters <= 80
    assert any("limit" in w.lower() or "truncat" in w.lower() for w in pkg.package_warnings)
    assert pkg.is_complete is False


def test_counts_and_bytes():
    files = [("a.txt", b"aaaa", "other"), ("b.txt", b"bbbbbb", "other")]
    pkg = _pkg_from(files)
    assert pkg.total_input_bytes == 10
    assert pkg.successful_source_count == 2
    assert pkg.failed_source_count == 0
    assert pkg.is_complete is True


def test_merged_sections_metadata():
    files = [("a.txt", b"Alpha", "pitch_deck")]
    pkg = _pkg_from(files)
    assert len(pkg.merged_sections) == 1
    s = pkg.merged_sections[0]
    assert s["filename"] == "a.txt"
    assert s["source_category"] == "pitch_deck"
    assert s["original_order"] == 0
    assert s["character_count"] == len("Alpha")


def test_json_serializable():
    files = [("a.txt", b"Alpha", "pitch_deck"),
             ("deck.pdf", make_text_pdf(["Slide text"]), "sales_deck")]
    pkg = _pkg_from(files)
    j = json.dumps(pkg.to_dict())                # whole package serializes
    assert "merged_text" in json.loads(j)
    assert pkg.package_id.startswith("pkg-")
    assert pkg.created_at


def test_max_file_count_via_package():
    files = [(f"f{i}.txt", f"c{i}".encode(), "other") for i in range(sp.MAX_FILES + 2)]
    pkg = sp.build_package_from_files(files)
    assert pkg.failed_source_count >= 2          # the extras beyond MAX_FILES
    assert pkg.is_complete is False


def test_entry_point_generate_new_pattern():
    # Multiple business materials.
    files = [("pitch.txt", b"pitch content", "pitch_deck"),
             ("cases.txt", b"case study content", "case_study"),
             ("catalogue.txt", b"service catalogue content", "service_catalogue")]
    pkg = _pkg_from(files)
    assert pkg.successful_source_count == 3
    assert pkg.is_complete is True
    assert pkg.merged_text.count("--- SOURCE START ---") == 3


def test_entry_point_standardize_existing_pattern():
    # One existing ICP + optional supporting material — same extraction behavior.
    files = [("current_icp.docx", make_docx(["Our ICP", "Target: FinTech"]), "existing_icp"),
             ("notes.txt", b"supporting discovery notes", "discovery_notes")]
    pkg = _pkg_from(files)
    assert pkg.successful_source_count == 2
    assert "Our ICP" in pkg.merged_text
    assert "Category: existing_icp" in pkg.merged_text
    assert pkg.is_complete is True


def test_extraction_is_entry_point_agnostic():
    # The same file yields the same extracted text regardless of declared category/entry point.
    data = make_docx(["Same body text"])
    a = sd.extract_source_document("x.docx", data, category="existing_icp")
    b = sd.extract_source_document("x.docx", data, category="pitch_deck")
    assert a.extracted_text == b.extracted_text
    assert a.extraction_status == b.extraction_status


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
