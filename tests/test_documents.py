import pytest
import requests

from bnpl_metrics import cli
from bnpl_metrics.errors import InputError
from bnpl_metrics.sources.catalog import DocumentCatalog
from bnpl_metrics.sources.documents import fetch_new_documents
from bnpl_metrics.sources.web import PoliteSession
from bnpl_metrics.specs import DocumentSource, SpecError, parse_specs

UA = "bnpl-metrics-tests (tests@example.com)"
INDEX = (
    '<a href="/files/annual-2024.pdf">Annual 2024</a>'
    '<a href="/files/annual-2025.pdf">Annual 2025</a>'
    '<a href="/other/annual-2025.pdf">Copy elsewhere</a>'
    '<a href="/files/press.html">Press release</a>'
)


def polite():
    session = requests.Session()
    session.trust_env = False
    return PoliteSession(UA, session=session, min_delay=0, retries=0)


def pages(**extra):
    base = {
        "/robots.txt": (200, "User-agent: *\nDisallow: /private/\n"),
        "/reports": (200, INDEX),
        "/files/annual-2024.pdf": (200, "PDF-2024"),
        "/files/annual-2025.pdf": (200, "PDF-2025"),
        "/other/annual-2025.pdf": (200, "PDF-2025"),  # same bytes at another URL
    }
    base.update(extra)
    return base


def source(base, **overrides):
    values = {"id": "reports", "index_url": f"{base}/reports", "link_pattern": r"annual.*\.pdf"}
    values.update(overrides)
    return DocumentSource(**values)


def test_downloads_matching_documents_once(serve, tmp_path):
    base, seen = serve(pages())
    catalog = DocumentCatalog(tmp_path / "docs.json")
    saved = fetch_new_documents(source(base), polite(), catalog, tmp_path / "files")
    # the third link has identical content to the second, so it is dropped as a duplicate
    assert sorted(p.read_text() for p in saved) == ["PDF-2024", "PDF-2025"]
    assert "/files/press.html" not in seen
    assert all(p.name.endswith(("annual-2024.pdf", "annual-2025.pdf")) for p in saved)

    seen.clear()
    again = fetch_new_documents(
        source(base), polite(), DocumentCatalog(tmp_path / "docs.json"), tmp_path / "files"
    )
    assert again == []
    assert [path for path in seen if path.endswith(".pdf")] == []  # nothing re-downloaded


def test_max_documents_limits_downloads(serve, tmp_path):
    base, _ = serve(pages())
    catalog = DocumentCatalog(tmp_path / "docs.json")
    saved = fetch_new_documents(
        source(base, max_documents=1), polite(), catalog, tmp_path / "files"
    )
    assert len(saved) == 1


def test_failed_download_is_skipped_not_fatal(serve, tmp_path):
    base, _ = serve(pages(**{"/files/annual-2024.pdf": (500, "boom")}))
    saved = fetch_new_documents(
        source(base), polite(), DocumentCatalog(tmp_path / "d.json"), tmp_path / "files"
    )
    assert [p.read_text() for p in saved] == ["PDF-2025"]


def test_robots_blocks_the_index_page(serve, tmp_path):
    base, seen = serve(pages(**{"/robots.txt": (200, "User-agent: *\nDisallow: /reports\n")}))
    with pytest.raises(InputError, match="robots.txt"):
        fetch_new_documents(
            source(base), polite(), DocumentCatalog(tmp_path / "d.json"), tmp_path / "f"
        )
    assert "/reports" not in seen


def test_javascript_sources_need_a_browser(serve, tmp_path):
    base, _ = serve(pages())
    with pytest.raises(InputError, match="needs a browser"):
        fetch_new_documents(
            source(base, javascript=True),
            polite(),
            DocumentCatalog(tmp_path / "d.json"),
            tmp_path / "f",
        )


def company_with(documents):
    return {
        "companies": [
            {
                "id": "CO",
                "metrics": [
                    {
                        "name": "m",
                        "unit": "ratio",
                        "formula": "x",
                        "inputs": {
                            "x": {"unit": "ratio", "sources": [{"type": "constant", "value": 1}]}
                        },
                    }
                ],
                "documents": documents,
            }
        ]
    }


def test_document_source_validation():
    ok = parse_specs(
        company_with([{"id": "d", "index_url": "https://x.test/a", "link_pattern": "pdf$"}])
    )
    assert ok[0].documents[0].max_documents == 10
    for bad, message in [
        ({"id": "d", "index_url": "ftp://x.test", "link_pattern": "x"}, "http"),
        ({"id": "d", "index_url": "https://x.test", "link_pattern": "("}, "invalid link_pattern"),
        ({"id": "d", "index_url": "https://x.test"}, "link_pattern"),
    ]:
        with pytest.raises(SpecError, match=message):
            parse_specs(company_with([bad]))


def test_cli_fetch_requires_a_real_user_agent_and_reports_unknown_company(tmp_path, capsys):
    config = tmp_path / "c.yaml"
    config.write_text(
        "companies:\n  - id: CO\n    metrics:\n      - name: m\n        unit: ratio\n"
        "        formula: x\n        inputs:\n          x:\n            unit: ratio\n"
        "            sources: [{type: constant, value: 1}]\n"
        "    documents:\n      - {id: d, index_url: 'https://x.test/a', link_pattern: pdf}\n"
    )
    assert cli.main(["fetch", "--config", str(config), "--company", "CO"]) == 1
    assert "SEC_USER_AGENT" in capsys.readouterr().err
    assert cli.main(["fetch", "--config", str(config), "--company", "NOPE"]) == 1
    assert "unknown company" in capsys.readouterr().err
