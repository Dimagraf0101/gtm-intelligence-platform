"""Extract and clean ICP text from a PDF.

The ICP (Ideal Customer Profile) is provided as a PDF. This module turns it into
clean plain text that can be embedded in the scoring prompt. It does NOT try to
parse the ICP into structured JSON — that is a later phase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from pypdf import PdfReader


@dataclass
class ICP:
    """A parsed ICP definition."""
    name: str          # ICP name (PDF filename without extension)
    text: str          # cleaned plain text of the whole document
    n_pages: int       # how many pages were read

    def is_empty(self) -> bool:
        return len(self.text.strip()) == 0


def _clean_text(raw: str) -> str:
    """Normalise whitespace and strip PDF extraction noise."""
    # Normalise line endings and non-breaking spaces.
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    # Drop obviously broken control characters.
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    # Collapse runs of spaces/tabs.
    text = re.sub(r"[ \t]+", " ", text)
    # Trim trailing spaces on each line.
    text = "\n".join(line.strip() for line in text.split("\n"))
    # Collapse 3+ blank lines into a single blank line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_icp(source: Union[str, Path], name: str | None = None) -> ICP:
    """Read a PDF file path and return a cleaned :class:`ICP`.

    ``name`` defaults to the file stem (e.g. ``fintech.pdf`` -> ``fintech``).
    """
    path = Path(source)
    reader = PdfReader(str(path))
    return _build_icp(reader, name or path.stem)


def extract_icp_from_bytes(data: bytes, name: str) -> ICP:
    """Read PDF bytes (e.g. a Streamlit upload) and return a cleaned :class:`ICP`."""
    import io

    reader = PdfReader(io.BytesIO(data))
    return _build_icp(reader, name)


def _build_icp(reader: PdfReader, name: str) -> ICP:
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            # A single unreadable page should not sink the whole document.
            pages.append("")
    text = _clean_text("\n\n".join(pages))
    return ICP(name=name, text=text, n_pages=len(reader.pages))
