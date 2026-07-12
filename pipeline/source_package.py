"""SourcePackage — merge extracted SourceDocuments into one normalized package (Sprint 4.1B).

Deterministic, offline. Merges only usable, non-duplicate sources into a single ``merged_text`` with
explicit source boundaries, enforces a package-wide character budget and file-count limit, detects
exact-duplicate uploads by content hash, and preserves upload order. It never summarizes or
interprets content, never serializes raw binary, and behaves identically for both future entry
points (generate_new / standardize_existing). Companion: ``source_documents.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

from source_documents import (
    SourceDocument, extract_multiple_sources,
    SUCCESS, PARTIAL, FAILED, UNSUPPORTED,
)

# --- conservative package limits (local MVP) ---------------------------------

MAX_FILES = 30                               # maximum number of uploaded files
MAX_TOTAL_CHARS = 1_000_000                  # maximum merged characters across all sources

_USABLE = (SUCCESS, PARTIAL)


@dataclass
class SourcePackage:
    package_id: str = ""
    created_at: str = ""
    source_documents: list[SourceDocument] = field(default_factory=list)
    successful_source_count: int = 0
    failed_source_count: int = 0
    total_input_bytes: int = 0
    total_extracted_characters: int = 0
    merged_text: str = ""
    merged_sections: list[dict] = field(default_factory=list)
    package_warnings: list[str] = field(default_factory=list)
    is_complete: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _boundary_block(filename: str, category: str, content: str) -> str:
    return (
        "--- SOURCE START ---\n"
        f"Filename: {filename}\n"
        f"Category: {category}\n"
        "--- CONTENT ---\n"
        f"{content}\n"
        "--- SOURCE END ---"
    )


def build_source_package(documents: list[SourceDocument], *,
                         max_total_chars: int = MAX_TOTAL_CHARS) -> SourcePackage:
    """Assemble a SourcePackage from already-extracted SourceDocuments.

    Merge rules: preserve upload order; include filename + category boundaries; never merge
    failed/unsupported text; skip exact duplicates (keep the first); stop cleanly at the character
    budget. Mutates duplicate/truncation flags on the passed documents so the record stays honest.
    """
    docs = sorted(documents, key=lambda d: d.original_order)

    pkg = SourcePackage(
        package_id="pkg-" + uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        source_documents=docs,
        total_input_bytes=sum(d.size_bytes for d in docs),
    )

    seen_hashes: dict[str, str] = {}         # content_hash -> first source_id
    budget = max_total_chars
    parts: list[str] = []
    total_truncated = False

    for doc in docs:
        if doc.extraction_status not in _USABLE or not doc.extracted_text:
            continue                          # failed/unsupported/empty never merged
        if doc.content_hash in seen_hashes:
            original = seen_hashes[doc.content_hash]
            doc.duplicate_of = original
            doc.extraction_warnings.append(
                f"Duplicate of an earlier upload ({original}); content not merged again.")
            pkg.package_warnings.append(
                f"'{doc.filename}' is an exact duplicate of {original}; kept once.")
            continue                          # record the duplicate, but do not insert text twice
        seen_hashes[doc.content_hash] = doc.source_id

        content = doc.extracted_text
        if budget <= 0:
            doc.extraction_warnings.append("Package character budget exhausted; source not merged.")
            pkg.package_warnings.append(
                f"'{doc.filename}' not merged: package character limit ({max_total_chars}) reached.")
            total_truncated = True
            continue
        if len(content) > budget:
            content = content[:budget]
            doc.content_truncated = True
            if doc.extraction_status == SUCCESS:
                doc.extraction_status = PARTIAL
            doc.extraction_warnings.append(
                "Content truncated at the package-wide character limit.")
            pkg.package_warnings.append(
                f"'{doc.filename}' truncated at the package character limit ({max_total_chars}).")
            total_truncated = True

        parts.append(_boundary_block(doc.filename, doc.source_category, content))
        pkg.merged_sections.append({
            "source_id": doc.source_id,
            "filename": doc.filename,
            "source_category": doc.source_category,
            "original_order": doc.original_order,
            "character_count": len(content),
            "truncated": doc.content_truncated,
        })
        budget -= len(content)

    pkg.merged_text = "\n\n".join(parts)
    pkg.total_extracted_characters = sum(s["character_count"] for s in pkg.merged_sections)
    pkg.successful_source_count = sum(1 for d in docs if d.extraction_status in _USABLE)
    pkg.failed_source_count = sum(1 for d in docs
                                  if d.extraction_status in (FAILED, UNSUPPORTED))
    pkg.is_complete = (pkg.failed_source_count == 0) and (not total_truncated)
    return pkg


def build_package_from_files(files, *, max_files: int | None = None,
                             max_chars_per_source=None,
                             max_total_chars: int = MAX_TOTAL_CHARS) -> SourcePackage:
    """Convenience: extract many uploads then build the package (both entry points use this)."""
    from source_documents import MAX_CHARS_PER_SOURCE
    per_source = MAX_CHARS_PER_SOURCE if max_chars_per_source is None else max_chars_per_source
    docs = extract_multiple_sources(files, max_files=max_files, max_chars=per_source)
    return build_source_package(docs, max_total_chars=max_total_chars)
