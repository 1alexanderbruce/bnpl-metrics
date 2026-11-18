"""Extract text from PDFs."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from bnpl_metrics.errors import InputError


def extract_text(path: str | Path) -> str:
    """Text of every page, each prefixed with a ``[page N]`` marker."""
    target = Path(path)
    if not target.is_file():
        raise InputError(f"PDF not found: {target}")
    try:
        reader = PdfReader(str(target))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise InputError(f"could not read PDF {target.name}: {exc}") from exc
    text = "\n\n".join(f"[page {i}]\n{body}" for i, body in enumerate(pages, 1) if body)
    if not text:
        raise InputError(f"{target.name} contains no extractable text (scanned images?)")
    return text
