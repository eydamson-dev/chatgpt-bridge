from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from chatgpt_bridge import browser as browser_mod
from chatgpt_bridge.browser import Browser, BrowserError, BrowserUnreachable, probe


class _Handler(BaseHTTPRequestHandler):
    payload: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path != "/json/version":
            self.send_error(404)
            return
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def cdp_server() -> Iterator[tuple[str, dict[str, Any]]]:
    _Handler.payload = {
        "Browser": "Chrome/156.0",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
    }
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _Handler.payload
    finally:
        server.shutdown()
        server.server_close()


def test_probe_returns_version(cdp_server: tuple[str, dict[str, Any]]) -> None:
    url, payload = cdp_server
    version = probe(url)
    assert version.browser == payload["Browser"]
    assert version.websocket_url == payload["webSocketDebuggerUrl"]


def test_probe_strips_trailing_slash(cdp_server: tuple[str, dict[str, Any]]) -> None:
    url, _ = cdp_server
    assert probe(url + "/").browser.startswith("Chrome")


def test_probe_unreachable_has_hint() -> None:
    with pytest.raises(BrowserUnreachable) as excinfo:
        probe("http://127.0.0.1:1", timeout_s=0.5)
    message = str(excinfo.value)
    assert "unreachable" in message
    assert "--remote-debugging-port" in message


def test_probe_requires_websocket(cdp_server: tuple[str, dict[str, Any]]) -> None:
    url, payload = cdp_server
    payload.pop("webSocketDebuggerUrl")
    with pytest.raises(BrowserUnreachable, match="no DevTools websocket"):
        probe(url)


class _FakePage:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or "about:blank"
        self.closed = False
        self.goto_calls: list[str] = []

    def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        self.goto_calls.append(url)

    def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[_FakePage] = [_FakePage("https://chatgpt.com/")]
        self.opened: list[_FakePage] = []

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.opened.append(page)
        return page


class _FakeBrowser:
    def __init__(self, contexts: list[_FakeContext] | None = None) -> None:
        self.contexts = contexts if contexts is not None else [_FakeContext()]
        self.close_called = False

    def close(self) -> None:
        self.close_called = True


@pytest.fixture
def attached(monkeypatch: pytest.MonkeyPatch, cdp_server: tuple[str, dict[str, Any]]):
    url, _ = cdp_server
    fake = _FakeBrowser()
    monkeypatch.setattr(
        browser_mod, "probe", lambda _url, timeout_s=2.0: browser_mod.probe(_url, 1.0)
    )
    instance = Browser(url)
    instance._browser = fake
    instance._playwright = None
    monkeypatch.setattr(instance, "close", lambda: None)
    return instance, fake


def test_open_tab_creates_and_closes_its_own_tab(attached: Any) -> None:
    instance, fake = attached
    context = fake.contexts[0]
    existing = list(context.pages)

    with instance.open_tab("https://chatgpt.com/c/abc") as page:
        assert page.url == "https://chatgpt.com/c/abc"
        assert page in context.opened
        assert not page.closed

    assert page.closed
    assert context.pages == existing
    assert not any(p.closed for p in existing)


def test_open_tab_closes_on_error(attached: Any) -> None:
    instance, fake = attached
    context = fake.contexts[0]

    with pytest.raises(RuntimeError), instance.open_tab("https://chatgpt.com/c/abc") as page:
        raise RuntimeError("boom")

    assert context.opened[-1].closed
    assert page.closed


def test_open_tab_without_context_raises(tmp_path: Any) -> None:
    instance = Browser("http://127.0.0.1:9222")
    instance._browser = _FakeBrowser(contexts=[])
    with pytest.raises(BrowserUnreachable, match="no open context"), instance.open_tab():
        pass


def test_existing_pages_lists_user_tabs(attached: Any) -> None:
    instance, fake = attached
    pages = instance.existing_pages()
    assert len(pages) == 1
    assert pages[0].url == "https://chatgpt.com/"


def test_close_disconnects_without_closing_browser() -> None:
    fake = _FakeBrowser()
    instance = Browser("http://127.0.0.1:9222")
    instance._browser = fake
    stopped: list[bool] = []

    class _Pw:
        def stop(self) -> None:
            stopped.append(True)

    instance._playwright = _Pw()
    instance.close()

    assert fake.close_called, "CDP connection should be detached"
    assert stopped == [True]
    assert instance._browser is None
    assert instance._playwright is None


def test_require_browser_raises_when_not_connected() -> None:
    with pytest.raises(BrowserError, match="not open"):
        Browser("http://127.0.0.1:9222").existing_pages()
