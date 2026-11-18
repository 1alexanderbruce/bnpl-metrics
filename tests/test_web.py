import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from bnpl_metrics.errors import InputError
from bnpl_metrics.fileio import atomic_write_json, atomic_write_text, read_json
from bnpl_metrics.sources.browser import BrowserSession
from bnpl_metrics.sources.catalog import DocumentCatalog, ValueCatalog
from bnpl_metrics.sources.web import PoliteSession, RobotsDisallowed, find_links, sha256_file

UA = "bnpl-metrics-tests (tests@example.com)"
PAGES = {
    "/robots.txt": (200, "User-agent: *\nDisallow: /private/\n"),
    "/index.html": (
        200,
        '<a href="/files/a.pdf">Annual report</a> <a href="b.html">News</a> '
        '<a href="/files/a.pdf">dup</a>',
    ),
    "/private/secret.html": (200, "secret"),
    "/files/a.pdf": (200, "PDFDATA"),
    "/boom": (404, "missing"),
}


@pytest.fixture
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("User-Agent")))
            status, body = PAGES.get(self.path, (404, "not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def make_session(**kwargs):
    session = requests.Session()
    session.trust_env = False
    return PoliteSession(UA, session=session, min_delay=0, retries=0, **kwargs)


def test_sends_honest_user_agent(server):
    base, seen = server
    make_session().get(f"{base}/index.html")
    assert {agent for _, agent in seen} == {UA}


def test_robots_disallow_is_enforced_and_fetches_nothing(server):
    base, seen = server
    polite = make_session()
    assert polite.allowed(f"{base}/index.html")
    assert not polite.allowed(f"{base}/private/secret.html")
    with pytest.raises(RobotsDisallowed):
        polite.get(f"{base}/private/secret.html")
    assert "/private/secret.html" not in [path for path, _ in seen]


def test_robots_fetched_once_per_origin(server):
    base, seen = server
    polite = make_session()
    polite.get(f"{base}/index.html")
    polite.get(f"{base}/files/a.pdf")
    assert [p for p, _ in seen].count("/robots.txt") == 1


def test_missing_robots_means_allowed(server):
    base, _ = server
    PAGES_COPY = dict(PAGES)
    PAGES.pop("/robots.txt")
    try:
        assert make_session().allowed(f"{base}/private/secret.html")
    finally:
        PAGES.update(PAGES_COPY)


def test_unreachable_robots_means_disallowed():
    assert not make_session().allowed("http://127.0.0.1:9/page")


def test_robots_can_be_disabled_explicitly(server):
    base, _ = server
    assert make_session(respect_robots=False).allowed(f"{base}/private/secret.html")


def test_http_errors_become_input_errors(server):
    base, _ = server
    with pytest.raises(InputError, match="request failed"):
        make_session().get(f"{base}/boom")


def test_rate_limit_waits_between_requests(server):
    base, _ = server
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    session = requests.Session()
    session.trust_env = False
    polite = PoliteSession(UA, session=session, min_delay=2.0, sleep=sleep, clock=lambda: now[0])
    polite.get(f"{base}/index.html")
    polite.get(f"{base}/files/a.pdf")
    assert sleeps and all(0 < s <= 2.0 for s in sleeps)


def test_download_and_catalog_dedupe(server, tmp_path):
    base, _ = server
    polite = make_session()
    first = polite.download(f"{base}/files/a.pdf", tmp_path)
    second = polite.download(f"{base}/files/a.pdf", tmp_path, filename="copy.pdf")
    assert first.read_text() == "PDFDATA"
    assert sha256_file(first) == sha256_file(second)
    catalog = DocumentCatalog(tmp_path / "catalog.json")
    assert catalog.add(f"{base}/files/a.pdf", first, title="Annual report")
    assert not catalog.add(f"{base}/files/a.pdf", second)
    catalog.save()
    reloaded = DocumentCatalog(tmp_path / "catalog.json")
    assert reloaded.has_url(f"{base}/files/a.pdf")
    assert reloaded.has_hash(sha256_file(first))


def test_filename_is_sanitized(server, tmp_path):
    base, _ = server
    path = make_session().download(f"{base}/files/a.pdf", tmp_path, filename="../../evil name.pdf")
    assert path.parent == tmp_path
    assert "/" not in path.name


def test_find_links_resolves_and_dedupes():
    html = PAGES["/index.html"][1]
    links = find_links(html, "http://site.test/dir/index.html", r"\.pdf$|report")
    assert links == [("Annual report", "http://site.test/files/a.pdf")]


class FakeDriver:
    def __init__(self):
        self.visited = []
        self.quit_called = False
        self.page_source = "<html>rendered</html>"

    def get(self, url):
        self.visited.append(url)

    def quit(self):
        self.quit_called = True


def test_browser_respects_robots_before_launching(server):
    base, _ = server
    created = []
    browser = BrowserSession(make_session(), driver_factory=lambda ua, h: created.append(1))
    with pytest.raises(RobotsDisallowed):
        browser.get_html(f"{base}/private/secret.html")
    assert created == []


def test_browser_fetches_allowed_pages_with_the_honest_agent(server):
    base, _ = server
    driver = FakeDriver()
    agents = []

    def factory(user_agent, headless):
        agents.append(user_agent)
        return driver

    with BrowserSession(make_session(), driver_factory=factory) as browser:
        assert browser.get_html(f"{base}/index.html") == "<html>rendered</html>"
    assert agents == [UA]
    assert driver.quit_called


def test_value_catalog_round_trip(tmp_path):
    path = tmp_path / "values.json"
    catalog = ValueCatalog(path)
    catalog.set("CO", "total_assets", 12.5, "billion AUD", "annual.pdf p3", "2025-06-30")
    catalog.save()
    value = ValueCatalog(path).get("CO", "total_assets")
    assert (value.value, value.unit, value.as_of) == (12.5, "billion AUD", "2025-06-30")
    assert ValueCatalog(path).keys("CO") == ["total_assets"]
    with pytest.raises(InputError):
        ValueCatalog(path).get("CO", "missing")


def test_atomic_write_leaves_no_temp_files_and_corrupt_json_raises(tmp_path):
    atomic_write_json(tmp_path / "a.json", {"x": 1})
    atomic_write_text(tmp_path / "a.json", json.dumps({"x": 2}))
    assert read_json(tmp_path / "a.json", None) == {"x": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]
    assert read_json(tmp_path / "missing.json", "default") == "default"
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        read_json(tmp_path / "bad.json", {})
