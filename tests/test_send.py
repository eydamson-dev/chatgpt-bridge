from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from chatgpt_bridge import send as send_service
from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import (
    ExtractedChat,
    ReplyTimeout,
    chat_id_from_url,
)
from chatgpt_bridge.parse import ParsedTurn
from chatgpt_bridge.store import ChatRef

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


def extracted(chat_id: str, reply: str = "reply") -> ExtractedChat:
    return ExtractedChat(
        id=chat_id,
        title="Chat",
        url=f"https://chatgpt.com/c/{chat_id}",
        turns=(
            ParsedTurn(role="user", markdown="question", text="question"),
            ParsedTurn(role="assistant", markdown=reply, text=reply),
        ),
    )


class FakeSource:
    def __init__(self, refs: list[ChatRef] | None = None) -> None:
        self.refs = refs or []
        self.sent: list[tuple[str, str]] = []
        self.created: list[str] = []
        self.list_calls = 0
        self.project_calls: list[str] = []
        self.created_in_project: list[tuple[str, str]] = []
        self.failure: Exception | None = None
        self.reply = "reply"

    def list_chats(self, scroll: bool = False, limit: int | None = None) -> list[ChatRef]:
        self.list_calls += 1
        return self.refs[:limit] if limit else self.refs

    def send_message(self, url_or_id: str, message: str) -> ExtractedChat:
        chat_id = chat_id_from_url(url_or_id)
        self.sent.append((chat_id, message))
        if self.failure is not None:
            raise self.failure
        return extracted(chat_id, self.reply)

    def list_project_chats(self, project: str, limit: int = 50) -> list[ChatRef]:
        self.project_calls.append(project)
        return self.refs[:limit] if self.refs else []

    def create_chat(self, message: str) -> ExtractedChat:
        self.created.append(message)
        if self.failure is not None:
            raise self.failure
        return extracted(UUID, self.reply)

    def create_chat_in_project(self, project: str, message: str) -> ExtractedChat:
        self.created_in_project.append((project, message))
        if self.failure is not None:
            raise self.failure
        return extracted(UUID, self.reply)


def session_for(source: FakeSource):
    @contextmanager
    def _session():
        yield source

    return _session


def seed(db: Path, chat_id: str = UUID, title: str = "Fedora notes") -> None:
    conn = store.connect(db)
    turns = [store.Turn(role="user", markdown="old", text="old")]
    store.save_chat(
        conn,
        chat_id=chat_id,
        title=title,
        url=f"https://chatgpt.com/c/{chat_id}",
        section="recents",
        turns=turns,
        hash_value=store.content_hash(turns),
    )
    conn.close()


def test_send_to_stored_title_uses_cache_then_stores_reply(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    seed(db)
    source = FakeSource()

    result = send_service.send_message("fedora", "hello", db_path=db, session=session_for(source))

    assert result.outcome == "sent"
    assert result.reply == "reply"
    assert source.sent == [(UUID, "hello")]
    assert source.list_calls == 0

    conn = store.connect(db)
    chat = store.get_chat(conn, UUID)
    assert chat is not None
    assert [turn.role for turn in chat.turns] == ["user", "assistant"]
    assert store.last_fetch(conn) is not None
    sent = store.last_send(conn)
    assert sent is not None
    assert (sent.chat_id, sent.status) == (UUID, "sent")
    conn.close()


def test_send_by_title_resolves_from_sidebar(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource([ChatRef(id=UUID, title="Fedora notes", url="u")])

    result = send_service.send_message(
        "Fedora notes", "hello", db_path=db, session=session_for(source)
    )

    assert result.outcome == "sent"
    assert source.list_calls == 1
    assert source.sent == [(UUID, "hello")]


def test_expected_chat_id_mismatch_refuses_before_sending(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource()

    with pytest.raises(send_service.SendRefused, match="refusing to send"):
        send_service.send_message(
            f"https://chatgpt.com/c/{UUID}",
            "hello",
            expected_chat_id="someone-else",
            db_path=db,
            session=session_for(source),
        )
    assert source.sent == []


def test_reply_timeout_reports_unknown_and_stores_nothing(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource()
    source.failure = ReplyTimeout(UUID, f"https://chatgpt.com/c/{UUID}")

    result = send_service.send_message(
        f"https://chatgpt.com/c/{UUID}", "hello", db_path=db, session=session_for(source)
    )

    assert result.outcome == "unknown"
    assert result.chat is None
    conn = store.connect(db)
    assert store.counts(conn).chats == 0
    sent = store.last_send(conn)
    assert sent is not None
    assert (sent.chat_id, sent.status) == (UUID, "unknown")
    conn.close()


def test_create_chat_stores_the_new_conversation(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource()

    result = send_service.create_chat("hello", db_path=db, session=session_for(source))

    assert result.outcome == "sent"
    assert result.reply == "reply"
    assert source.created == ["hello"]
    conn = store.connect(db)
    chat = store.get_chat(conn, UUID)
    assert chat is not None
    assert chat.turns[-1].text == "reply"
    assert store.last_send(conn) is not None
    conn.close()


def test_empty_message_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    with pytest.raises(send_service.SendError, match="empty"):
        send_service.send_message("anything", "   ", db_path=db)


def test_empty_target_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    with pytest.raises(send_service.SendError, match="target is empty"):
        send_service.send_message("   ", "hello", db_path=db)


def test_preview_resolves_direct_and_stored_targets(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    seed(db)

    direct = send_service.preview(f"https://chatgpt.com/c/{UUID}", db_path=db)
    assert direct is not None
    assert direct.id == UUID

    stored = send_service.preview("fedora", db_path=db)
    assert stored is not None
    assert stored.id == UUID

    assert send_service.preview("not stored anywhere", db_path=db) is None


def test_send_requires_a_session_for_an_uncached_target(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    with pytest.raises(Exception, match="no browser session"):
        send_service.send_message(f"https://chatgpt.com/c/{UUID}", "hello", db_path=db)


# ---------------------------------------------------------------- projects


def test_send_scoped_to_a_project_uses_the_project_listing(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource([ChatRef(id=UUID, title="Plan", url="u", project="Test project")])
    result = send_service.send_message(
        "Plan", "hello", db_path=db, session=session_for(source), project="Test project"
    )
    assert result.outcome == "sent"
    assert source.project_calls == ["Test project"]
    assert source.list_calls == 0


def test_send_records_the_project_on_the_stored_chat(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource(
        [
            ChatRef(
                id=UUID,
                title="Plan",
                url=f"https://chatgpt.com/g/g-p-abc/c/{UUID}",
                project="Test project",
                project_id="g-p-abc",
            )
        ]
    )
    send_service.send_message(
        "Plan", "hello", db_path=db, session=session_for(source), project="Test project"
    )
    conn = store.connect(db)
    chat = store.get_chat(conn, UUID)
    assert chat is not None
    assert chat.project == "Test project"
    assert chat.project_id == "g-p-abc"
    conn.close()


def test_create_chat_in_project_routes_to_the_project_entry_point(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource()
    result = send_service.create_chat(
        "hello", db_path=db, session=session_for(source), project="Test project"
    )
    assert result.outcome == "sent"
    assert source.created_in_project == [("Test project", "hello")]
    assert source.created == []
    assert result.chat is not None
    assert result.chat.project == "Test project"


def test_create_chat_without_project_uses_the_plain_entry_point(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    source = FakeSource()
    send_service.create_chat("hello", db_path=db, session=session_for(source))
    assert source.created == ["hello"]
    assert source.created_in_project == []


def test_create_chat_rejects_a_blank_project(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    with pytest.raises(send_service.SendError, match="project is empty"):
        send_service.create_chat("hello", db_path=db, project="   ")


def test_preview_is_scoped_to_the_project(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    seed(db)
    conn = store.connect(db)
    conn.execute("UPDATE chats SET project = 'Test project' WHERE id = ?", (UUID,))
    conn.close()
    assert send_service.preview("fedora", db_path=db, project="Test project") is not None
    assert send_service.preview("fedora", db_path=db, project="Other") is None
