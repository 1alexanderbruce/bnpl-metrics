"""Download new documents listed on a configured page."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from bnpl_metrics.errors import InputError
from bnpl_metrics.logging_utils import get_logger
from bnpl_metrics.sources.browser import BrowserSession
from bnpl_metrics.sources.catalog import DocumentCatalog
from bnpl_metrics.sources.web import PoliteSession, find_links
from bnpl_metrics.specs import DocumentSource

log = get_logger(__name__)


def fetch_new_documents(
    source: DocumentSource,
    session: PoliteSession,
    catalog: DocumentCatalog,
    directory: str | Path,
    browser: BrowserSession | None = None,
) -> list[Path]:
    """Download documents from ``source`` that are not already catalogued.

    Links are taken in page order, limited to ``max_documents``. A document whose URL is
    already known is skipped without a request; one whose content hash is already known is
    removed after download. Returns the newly saved files.
    """
    if source.javascript:
        if browser is None:
            raise InputError(f"{source.id}: needs a browser; install the selenium extra")
        html = browser.get_html(source.index_url)
    else:
        html = session.get(source.index_url).text

    links = find_links(html, source.index_url, source.link_pattern)[: source.max_documents]
    saved: list[Path] = []
    for title, url in links:
        if catalog.has_url(url):
            continue
        # The URL hash keeps two different documents with the same file name apart.
        name = f"{hashlib.sha256(url.encode()).hexdigest()[:8]}-{Path(urlparse(url).path).name}"
        try:
            path = session.download(url, directory, filename=name)
        except InputError as exc:
            log.warning("skipping %s: %s", url, exc)
            continue
        if catalog.add(url, path, title=title, doc_type=source.doc_type):
            saved.append(path)
        else:
            path.unlink(missing_ok=True)
    catalog.save()
    return saved
