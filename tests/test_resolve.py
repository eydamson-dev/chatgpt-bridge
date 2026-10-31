from __future__ import annotations

import pytest

from chatgpt_bridge.resolve import (
    AmbiguousTarget,
    TargetNotFound,
    resolve_from_cache,
    resolve_with_listing,
    target_id,
)
from chatgpt_bridge.store import ChatRef, ChatSummary

UUID = "6ae5de7d-e0f8-83ec-9a71-cd2780e3bf72"


def summary(chat_id: str, title: str) -> ChatSummary:
    return ChatSummary(
        id=chat_id,
        title=title,
        url=f"https://chatgpt.com/c/{chat_id}",
        section="recents",
        turn_count=2,
        fetched_at="2026-01-01T00:00:00+00:00",
        parse_version=1,
    )


def ref(chat_id: str, title: str) -> ChatRef:
    return ChatRef(
        id=chat_id, title=title, url=f"https://chatgpt.com/c/{chat_id}", section="recents"
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        (UUID, UUID),
        (f"https://chatgpt.com/c/{UUID}", UUID),
        (f"https://chatgpt.com/c/{UUID}?model=x", UUID),
        (f"https://www.chatgpt.com/c/{UUID}", UUID),
        (f"/c/{UUID}", UUID),
        ("6ae5de7d", "6ae5de7d"),
    ],
)
def test_target_id_recognises_ids_and_urls(value: str, expected: str) -> None:
    assert target_id(value) == expected


def test_target_id_returns_none_for_titles() -> None:
    assert target_id("Lenovo ThinkPad Review") is None
    assert target_id("short") is None


def test_target_id_rejects_other_hosts() -> None:
    with pytest.raises(TargetNotFound, match="not a ChatGPT chat URL"):
        target_id("https://example.com/c/abc")


def test_cache_resolution_by_full_id() -> None:
    resolved = resolve_from_cache(UUID, [summary(UUID, "Lenovo")])
    assert resolved is not None
    assert resolved.id == UUID
    assert resolved.from_cache is True


def test_cache_resolution_by_prefix() -> None:
    resolved = resolve_from_cache("6ae5de7d", [summary(UUID, "Lenovo")])
    assert resolved is not None
    assert resolved.id == UUID


def test_cache_resolution_by_exact_title() -> None:
    resolved = resolve_from_cache("lenovo", [summary("c1", "Lenovo"), summary("c2", "Other")])
    assert resolved is not None
    assert resolved.id == "c1"


def test_cache_resolution_by_unique_substring() -> None:
    resolved = resolve_from_cache(
        "ThinkPad", [summary("c1", "Lenovo ThinkPad Review"), summary("c2", "Other")]
    )
    assert resolved is not None
    assert resolved.id == "c1"


def test_cache_resolution_ambiguous_title() -> None:
    with pytest.raises(AmbiguousTarget) as excinfo:
        resolve_from_cache("lenovo", [summary("c1", "Lenovo T14"), summary("c2", "Lenovo T14s")])
    assert len(excinfo.value.candidates) == 2


def test_cache_resolution_miss_returns_none() -> None:
    assert resolve_from_cache("nothing here", [summary("c1", "Other")]) is None


def test_listing_resolution_by_title() -> None:
    resolved = resolve_with_listing("Lenovo ThinkPad Review", [ref("c1", "Lenovo ThinkPad Review")])
    assert resolved.id == "c1"
    assert resolved.from_cache is False


def test_listing_resolution_by_url_not_in_list() -> None:
    resolved = resolve_with_listing(f"https://chatgpt.com/c/{UUID}", [ref("other", "Other")])
    assert resolved.id == UUID


def test_listing_resolution_ambiguous() -> None:
    with pytest.raises(AmbiguousTarget, match="several visible"):
        resolve_with_listing("lenovo", [ref("c1", "Lenovo T14"), ref("c2", "Lenovo T14s")])


def test_listing_resolution_missing_suggests_chats() -> None:
    with pytest.raises(TargetNotFound, match="chatgpt-bridge chats"):
        resolve_with_listing("nope", [ref("c1", "Other")])


def test_listing_resolution_short_id_prefix_missing() -> None:
    with pytest.raises(TargetNotFound, match="visible chat list"):
        resolve_with_listing("abc123", [ref("zzz999", "Other")])
