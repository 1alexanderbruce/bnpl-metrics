"""A polite HTTP client.

It identifies itself honestly, obeys robots.txt, rate-limits per host, retries transient
failures, and verifies TLS certificates. There is deliberately no user-agent spoofing, no
certificate bypass and no URL guessing.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bnpl_metrics.errors import InputError
from bnpl_metrics.logging_utils import get_logger

log = get_logger(__name__)


class RobotsDisallowed(InputError):
    """The site's robots.txt does not allow this URL for our user agent."""


class PoliteSession:
    def __init__(
        self,
        user_agent: str,
        *,
        proxies: dict[str, str] | None = None,
        min_delay: float = 1.0,
        respect_robots: bool = True,
        timeout: float = 30.0,
        retries: int = 3,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.min_delay = min_delay
        self.respect_robots = respect_robots
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        if proxies:
            self._session.proxies.update(proxies)
        retry = Retry(
            total=retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            respect_retry_after_header=True,
            allowed_methods=("GET", "HEAD"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            self.throttle(parts.netloc)
            try:
                response = self._session.get(robots_url, timeout=self.timeout)
            except requests.RequestException as exc:
                log.warning("could not fetch %s (%s); treating site as disallowed", robots_url, exc)
                self._robots[origin] = None
                return None
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
                self._robots[origin] = parser
            elif 400 <= response.status_code < 500:
                parser.parse([])  # no robots.txt: everything is allowed (RFC 9309)
                self._robots[origin] = parser
            else:
                self._robots[origin] = None
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        return parser is not None and parser.can_fetch(self.user_agent, url)

    def get(self, url: str, **kwargs) -> requests.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt does not allow fetching {url}")
        self.throttle(urlparse(url).netloc)
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self._session.get(url, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise InputError(f"request failed for {url}: {exc}") from exc
        return response

    def download(self, url: str, directory: str | Path, filename: str | None = None) -> Path:
        """Download to ``directory``; returns the saved path."""
        response = self.get(url, stream=True)
        name = filename or Path(urlparse(url).path).name or "download"
        target = Path(directory) / re.sub(r"[^A-Za-z0-9._-]", "_", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as handle:
            for block in response.iter_content(chunk_size=65536):
                handle.write(block)
        return target


def find_links(html: str, base_url: str, pattern: str) -> list[tuple[str, str]]:
    """Links whose href or text matches ``pattern`` (a regex), as ``(text, absolute_url)``."""
    regex = re.compile(pattern, re.IGNORECASE)
    soup = BeautifulSoup(html, "html.parser")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True)
        href = urljoin(base_url, anchor["href"])
        if href not in seen and (regex.search(href) or regex.search(text)):
            seen.add(href)
            found.append((text, href))
    return found


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()
