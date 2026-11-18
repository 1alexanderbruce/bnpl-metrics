"""Two small JSON stores.

``DocumentCatalog`` records files fetched from the web (deduplicated by content hash).
``ValueCatalog`` holds figures extracted from documents, which metrics read via ``catalog``
sources. Both write atomically.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from bnpl_metrics.errors import InputError
from bnpl_metrics.fileio import atomic_write_json, read_json
from bnpl_metrics.models import utc_now_iso
from bnpl_metrics.sources.web import sha256_file


class DocumentCatalog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.entries: dict[str, dict] = read_json(self.path, {})

    def has_hash(self, digest: str) -> bool:
        return digest in self.entries

    def has_url(self, url: str) -> bool:
        return any(
            entry["url"] == url or url in entry.get("aliases", [])
            for entry in self.entries.values()
        )

    def add(self, url: str, file: str | Path, title: str = "", doc_type: str = "") -> bool:
        """Record a downloaded file. Returns False if identical content is already catalogued;
        the new URL is remembered as an alias so it is not downloaded again."""
        digest = sha256_file(file)
        if digest in self.entries:
            aliases = self.entries[digest].setdefault("aliases", [])
            if url != self.entries[digest]["url"] and url not in aliases:
                aliases.append(url)
            return False
        self.entries[digest] = {
            "url": url,
            "file": str(file),
            "title": title,
            "doc_type": doc_type,
            "fetched_at": utc_now_iso(),
        }
        return True

    def save(self) -> None:
        atomic_write_json(self.path, self.entries)


@dataclass
class CatalogValue:
    value: float
    unit: str | None
    source: str
    as_of: str | None = None
    extracted_at: str = ""


class ValueCatalog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, dict]] = read_json(self.path, {})

    def set(
        self,
        company_id: str,
        key: str,
        value: float,
        unit: str | None,
        source: str,
        as_of: str | None = None,
    ) -> None:
        entry = CatalogValue(value, unit, source, as_of, utc_now_iso())
        self._data.setdefault(company_id, {})[key] = asdict(entry)

    def get(self, company_id: str, key: str) -> CatalogValue:
        try:
            return CatalogValue(**self._data[company_id][key])
        except KeyError:
            raise InputError(f"no catalog value '{key}' for {company_id}") from None

    def keys(self, company_id: str) -> list[str]:
        return sorted(self._data.get(company_id, {}))

    def save(self) -> None:
        atomic_write_json(self.path, self._data)
