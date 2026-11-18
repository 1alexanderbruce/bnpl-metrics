"""Optional Selenium access for pages that need JavaScript.

Requires the ``selenium`` extra and a browser. It identifies itself with the same honest
user agent as ``PoliteSession`` and checks robots.txt before every navigation.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from bnpl_metrics.sources.web import PoliteSession, RobotsDisallowed


def _firefox_driver(user_agent: str, headless: bool) -> Any:
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
    except ImportError as exc:
        raise RuntimeError(
            "install the 'selenium' extra: pip install 'bnpl-metrics[selenium]'"
        ) from exc
    options = Options()
    if headless:
        options.add_argument("-headless")
    options.set_preference("general.useragent.override", user_agent)
    driver_path = os.getenv("GECKODRIVER_PATH")
    service = Service(driver_path) if driver_path else Service()
    return webdriver.Firefox(options=options, service=service)


class BrowserSession:
    def __init__(
        self,
        polite: PoliteSession,
        *,
        headless: bool = True,
        driver_factory: Callable[[str, bool], Any] = _firefox_driver,
    ) -> None:
        self._polite = polite
        self._headless = headless
        self._factory = driver_factory
        self._driver: Any = None

    def get_html(self, url: str) -> str:
        if not self._polite.allowed(url):
            raise RobotsDisallowed(f"robots.txt does not allow fetching {url}")
        if self._driver is None:
            self._driver = self._factory(self._polite.user_agent, self._headless)
        self._polite.throttle(urlparse(url).netloc)
        self._driver.get(url)
        return self._driver.page_source

    def close(self) -> None:
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
