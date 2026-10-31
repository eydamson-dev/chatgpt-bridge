from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chatgpt_bridge import __version__, paths, store
from chatgpt_bridge.adapters.chatgpt import ChatGPTAdapter, ChatNotFound, LoginRequired
from chatgpt_bridge.browser import Browser, BrowserError
from chatgpt_bridge.config import Settings
from chatgpt_bridge.parse import ParseDriftError


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        return f"[{mark}] {self.name}: {self.detail}"


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def render(self) -> str:
        lines = [check.render() for check in self.checks]
        lines.append("")
        lines.append("all checks passed" if self.ok else "some checks failed")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def run_checks(settings: Settings, *, sample_chat: bool = False) -> DoctorReport:
    """Check every layer without fetching anything into the cache."""
    report = DoctorReport()
    report.checks.append(_check_config(settings))
    report.checks.append(_check_database())

    try:
        browser = Browser(settings.cdp_url)
        browser.connect()
    except BrowserError as exc:
        report.checks.append(Check("browser", False, str(exc).splitlines()[0]))
        return report
    report.checks.append(Check("browser", True, f"reachable at {settings.cdp_url}"))

    try:
        adapter = ChatGPTAdapter(browser, page_delay_s=0.3)
        try:
            refs = adapter.list_chats(scroll=False, limit=settings.lookup_limit)
        except LoginRequired as exc:
            report.checks.append(Check("login", False, str(exc)))
            return report
        report.checks.append(Check("sidebar", True, f"{len(refs)} chats visible for lookup"))
        if sample_chat and refs:
            _check_parse(adapter, refs[0].id, report)
    except BrowserError as exc:
        report.checks.append(Check("page", False, str(exc)))
    finally:
        browser.close()
    return report


def _check_config(settings: Settings) -> Check:
    return Check(
        "config",
        True,
        f"cdp_url={settings.cdp_url} data_dir={settings.data_dir} version={__version__}",
    )


def _check_database() -> Check:
    try:
        conn = store.connect()
    except store.StoreError as exc:
        return Check("database", False, str(exc))
    try:
        counts = store.counts(conn)
        version = store.schema_version(conn)
    finally:
        conn.close()
    return Check(
        "database",
        True,
        f"{paths.db_path()} schema={version} cached_chats={counts.chats} messages={counts.turns}",
    )


def _check_parse(adapter: ChatGPTAdapter, chat_id: str, report: DoctorReport) -> None:
    try:
        chat = adapter.fetch_chat(chat_id)
    except ParseDriftError as exc:
        report.checks.append(Check("parser", False, f"{chat_id}: {exc}"))
        return
    except ChatNotFound as exc:
        report.checks.append(Check("parser", False, str(exc)))
        return
    report.checks.append(
        Check("parser", True, f"sample chat {chat_id[:8]} parsed {len(chat.turns)} messages")
    )
