from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from chatgpt_bridge import send as send_service
from chatgpt_bridge import store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat, ReplyTimeout
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
        self.failure: Exception | None = None
        self.reply = "reply"

    def list_chats(self, scroll: bool = False, limit: int | None = None) -> list[ChatRef]:
        self.list_calls += 1
        return self.refs[:limit] if limit else self.refs

    def send_message(self, chat_id: str, message: str) -> ExtractedChat:
        self.sent.append((chat_id, message))
        if self.failure is not None:
            raise self.failure
        return extracted(chat_id, self.reply)

    def create_chat(self, message: str) -> ExtractedChat:
        self.created.append(message)
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
