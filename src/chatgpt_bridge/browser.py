from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from chatgpt_bridge.logging_setup import get_logger

log = get_logger("browser")

RESTART_HINT = (
    "Start your browser with a DevTools port, for example:\n"
    "  brave-browser --remote-debugging-port=9222\n"
    "  google-chrome --remote-debugging-port=9222 "
    "--user-data-dir=~/.local/share/chatgpt-bridge/profile\n"
    "and make sure you are logged in to https://chatgpt.com in that browser."
)


class BrowserError(RuntimeError):
    """Base class for browser-layer failures."""


class BrowserUnreachable(BrowserError):
    """The CDP endpoint did not answer."""


class BrowserUnavailable(BrowserError):
    """Playwright is not installed or cannot start."""


@dataclass(frozen=True)
class BrowserVersion:
    browser: str
    websocket_url: str


def probe(cdp_url: str, timeout_s: float = 2.0) -> BrowserVersion:
    """Check that a CDP endpoint answers. Raises BrowserUnreachable otherwise."""
    url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise BrowserUnreachable(
            f"browser unreachable at {cdp_url}: {exc}\n{RESTART_HINT}"
        ) from exc

    browser = str(payload.get("Browser", "unknown"))
    ws = str(payload.get("webSocketDebuggerUrl", ""))
    if not ws:
        raise BrowserUnreachable(f"{cdp_url} answered but reported no DevTools websocket")
    return BrowserVersion(browser=browser, websocket_url=ws)


class Browser:
    """Owns a CDP connection and the tabs it opens. Never touches the user's tabs."""

    def __init__(self, cdp_url: str) -> None:
        self.cdp_url = cdp_url
        self._playwright: Any = None
        self._browser: Any = None

    def __enter__(self) -> Browser:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def connect(self) -> None:
        probe(self.cdp_url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise BrowserUnavailable("playwright is not installed; run: uv sync") from exc

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:  # playwright raises its own error types
            self._stop_playwright()
            raise BrowserUnreachable(f"could not attach to {self.cdp_url}: {exc}") from exc
        log.info("attached to browser")

    def close(self) -> None:
        # Disconnect only: never call browser.close(), which would close the user's browser.
        if self._browser is not None:
            with contextlib.suppress(Exception):
                self._browser.close()
            self._browser = None
        self._stop_playwright()

    def _stop_playwright(self) -> None:
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                self._playwright.stop()
            self._playwright = None

    def existing_pages(self) -> list[Any]:
        """Tabs the user already has open. Read-only use."""
        browser = self._require_browser()
        pages: list[Any] = []
        for context in browser.contexts:
            pages.extend(context.pages)
        return pages

    @contextmanager
    def open_tab(self, url: str | None = None) -> Iterator[Any]:
        """Open a background tab, yield it, and close it in all cases."""
        browser = self._require_browser()
        if not browser.contexts:
            raise BrowserUnreachable(
                "the browser has no open context; open a normal window and log in to chatgpt.com"
            )
        context = browser.contexts[0]
        page = context.new_page()
        try:
            if url is not None:
                page.goto(url, wait_until="domcontentloaded")
            yield page
        finally:
            with contextlib.suppress(Exception):
                page.close()

    def _require_browser(self) -> Any:
        if self._browser is None:
            raise BrowserError("browser connection is not open")
        return self._browser
