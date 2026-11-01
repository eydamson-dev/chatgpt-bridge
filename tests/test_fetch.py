from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat, chat_id_from_url
from chatgpt_bridge.fetch import fetch_chat
from chatgpt_bridge.parse import ParsedTurn
from chatgpt_bridge.resolve import AmbiguousTarget, TargetNotFound
from chatgpt_bridge.store import ChatRef

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


def extracted(chat_id: str, text: str = "hi", title: str = "Chat") -> ExtractedChat:
    return ExtractedChat(
        id=chat_id,
        title=title,
        url=f"https://chatgpt.com/c/{chat_id}",
        turns=(
            ParsedTurn(role="user", markdown=text, text=text),
            ParsedTurn(role="assistant", markdown=f"re: {text}", text=f"re: {text}"),
        ),
    )


class FakeSource:
    def __init__(self, refs: list[ChatRef], body: str = "hi") -> None:
        self.refs = refs
        self.body = body
        self.fetched: list[str] = []
        self.list_calls = 0

    def list_chats(self, scroll: bool = False, limit: int | None = None) -> list[ChatRef]:
        self.list_calls += 1
        return self.refs[:limit] if limit else self.refs

    def fetch_chat(self, url_or_id: str) -> ExtractedChat:
        self.fetched.append(url_or_id)
        return extracted(chat_id_from_url(url_or_id), self.body)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "bridge.db"


def session_for(source: FakeSource):
    @contextmanager
    def _session():
        yield source

    return _session


def test_fetch_by_title_uses_sidebar_then_stores(db: Path) -> None:
    source = FakeSource([ChatRef(id=UUID, title="Sample hardware review", url="u")])
    result = fetch_chat("Sample hardware review", db_path=db, session=session_for(source))

    assert result.outcome == "new"
    assert result.chat.id == UUID
    assert source.fetched == [UUID]
    assert source.list_calls == 1

    conn = store.connect(db)
    assert store.counts(conn).chats == 1
    conn.close()


def test_fetch_by_url_skips_sidebar(db: Path) -> None:
    source = FakeSource([])
    result = fetch_chat(f"https://chatgpt.com/c/{UUID}", db_path=db, session=session_for(source))
    assert result.chat.id == UUID
    assert source.list_calls == 0


def test_cached_chat_is_served_without_browser(db: Path) -> None:
    first = FakeSource([ChatRef(id=UUID, title="Chat", url="u")])
    fetch_chat(UUID, db_path=db, session=session_for(first))

    result = fetch_chat(UUID, db_path=db, session=None)
    assert result.outcome == "cached"
    assert result.fetched is False
    assert first.fetched == [UUID]


def test_refresh_refetches_and_reports_unchanged(db: Path) -> None:
    source = FakeSource([ChatRef(id=UUID, title="Chat", url="u")])
    fetch_chat(UUID, db_path=db, session=session_for(source))

    refreshed = FakeSource([ChatRef(id=UUID, title="Chat", url="u")])
    result = fetch_chat(UUID, refresh=True, db_path=db, session=session_for(refreshed))
    assert result.outcome == "unchanged"
    assert result.changed is False
    assert refreshed.fetched == [UUID]


def test_refresh_detects_changed_content(db: Path) -> None:
    fetch_chat(UUID, db_path=db, session=session_for(FakeSource([], body="before")))
    changed = FakeSource([], body="after")
    result = fetch_chat(UUID, refresh=True, db_path=db, session=session_for(changed))
    assert result.outcome == "refreshed"
    assert result.changed is True
    assert result.chat.turns[0].text == "after"


def test_uncached_target_without_session_raises(db: Path) -> None:
    with pytest.raises(TargetNotFound, match="no browser session"):
        fetch_chat("Some title", db_path=db, session=None)


def test_ambiguous_title_raises_with_candidates(db: Path) -> None:
    source = FakeSource(
        [
            ChatRef(id="c1", title="Sample laptop A", url="u"),
            ChatRef(id="c2", title="Sample laptop B", url="u"),
        ]
    )
    with pytest.raises(AmbiguousTarget) as excinfo:
        fetch_chat("Sample", db_path=db, session=session_for(source))
    assert len(excinfo.value.candidates) == 2
    assert source.fetched == []


def test_lookup_limit_is_passed(db: Path) -> None:
    refs = [ChatRef(id=f"c{i}", title=f"Chat {i}", url="u") for i in range(5)]
    source = FakeSource(refs)
    with pytest.raises(TargetNotFound):
        fetch_chat("Chat 4", db_path=db, session=session_for(source), lookup_limit=2)
    assert source.fetched == []


def test_fetch_log_records_outcome(db: Path) -> None:
    fetch_chat(UUID, db_path=db, session=session_for(FakeSource([], body="x")))
    conn = store.connect(db)
    entry = store.last_fetch(conn)
    assert entry is not None
    assert entry.chat_id == UUID
    assert entry.status == "ok"
    conn.close()


def test_failure_stores_nothing(db: Path) -> None:
    class Boom(FakeSource):
        def fetch_chat(self, url_or_id: str) -> ExtractedChat:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        fetch_chat(UUID, db_path=db, session=session_for(Boom([])))

    conn = store.connect(db)
    assert store.counts(conn).chats == 0
    entry = store.last_fetch(conn)
    assert entry is not None
    assert (entry.chat_id, entry.status, entry.detail) == (UUID, "failed", "RuntimeError")
    conn.close()


def test_refresh_of_cached_chat_without_session_says_so(db: Path) -> None:
    fetch_chat(UUID, db_path=db, session=session_for(FakeSource([])))
    with pytest.raises(TargetNotFound, match="cannot be refreshed"):
        fetch_chat(UUID, refresh=True, db_path=db, session=None)


# ---------------------------------------------------------------- projects


class ProjectSource(FakeSource):
    """A source that also lists project chats and reports a project id."""

    def __init__(self, refs: list[ChatRef], body: str = "hi", title: str = "Chat") -> None:
        super().__init__(refs, body)
        self.project_calls: list[str] = []
        self.title = title

    def list_project_chats(self, project: str, limit: int = 50) -> list[ChatRef]:
        self.project_calls.append(project)
        return self.refs[:limit]

    def fetch_chat(self, url_or_id: str) -> ExtractedChat:
        self.fetched.append(url_or_id)
        chat = extracted(chat_id_from_url(url_or_id), self.body, self.title)
        return ExtractedChat(
            id=chat.id,
            title=chat.title,
            url=chat.url,
            turns=chat.turns,
            project_id="g-p-abc",
        )


def test_fetch_with_project_records_membership(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = ProjectSource([ChatRef(id=UUID, title="Plan", url="u")], title="Plan")
    result = fetch_chat("Plan", db_path=db, session=session_for(source), project="Test project")
    assert result.chat.project == "Test project"
    assert result.chat.project_id == "g-p-abc"
    assert source.project_calls == ["Test project"]
    assert source.list_calls == 0


def test_fetch_with_project_uses_the_project_listing(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = ProjectSource([ChatRef(id=UUID, title="Plan", url="u")], title="Plan")
    fetch_chat("Plan", db_path=db, session=session_for(source), project="Test project")
    assert source.project_calls == ["Test project"]
    assert source.list_calls == 0


def test_project_cache_hit_needs_no_browser(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = ProjectSource([ChatRef(id=UUID, title="Plan", url="u")], title="Plan")
    fetch_chat("Plan", db_path=db, session=session_for(source), project="Test project")
    again = fetch_chat("Plan", db_path=db, project="Test project")
    assert again.outcome == "cached"
    assert again.chat.project == "Test project"


def test_project_scope_excludes_chats_from_other_projects(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = ProjectSource([ChatRef(id=UUID, title="Plan", url="u")], title="Plan")
    fetch_chat("Plan", db_path=db, session=session_for(source), project="Test project")
    with pytest.raises(TargetNotFound):
        fetch_chat("Plan", db_path=db, project="Another project")
