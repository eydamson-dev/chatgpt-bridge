from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from chatgpt_bridge import paths, store
from chatgpt_bridge.adapters.chatgpt import ExtractedChat, LoginRequired
from chatgpt_bridge.logging_setup import get_logger
from chatgpt_bridge.parse import ParseDriftError
from chatgpt_bridge.store import ChatRef, Turn

log = get_logger("sync")


class ChatSource(Protocol):
    """What sync needs from a chat source. The adapter implements this."""

    def list_chats(self, scroll: bool = True) -> list[ChatRef]: ...

    def fetch_chat(self, url_or_id: str) -> ExtractedChat: ...


@dataclass(frozen=True)
class SyncOptions:
    full: bool = False
    limit: int | None = None
    recent_window: int = 10
    min_delay_s: float = 2.0
    max_delay_s: float = 4.0
    dry_run: bool = False


@dataclass
class SyncResult:
    kind: str
    status: str
    listed: int = 0
    fetched: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    stale: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str | None = None

    def summary(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "listed": self.listed,
            "fetched": self.fetched,
            "new": self.new,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "stale": self.stale,
            "errors": self.errors,
        }


class SyncError(RuntimeError):
    """Sync could not continue."""


class SyncAborted(SyncError):
    """Repeated failures suggest the page changed or the account is throttled."""


MAX_CONSECUTIVE_FAILURES = 3


def run_sync(
    source: ChatSource,
    options: SyncOptions,
    *,
    db_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> SyncResult:
    """Discover chats, then fetch the ones that need it."""
    rng = rng or random.Random()
    say = progress or (lambda _message: None)
    conn = store.connect(db_path)
    resolved_db = db_path or paths.db_path()

    started = store.utc_now()
    result = SyncResult(
        kind="full" if options.full else "incremental",
        status="running",
        started_at=started,
    )
    run_id = store.save_sync_run(conn, result.kind, "running", {}, started)  # type: ignore[arg-type]

    try:
        refs = source.list_chats(scroll=True)
        result.listed = len(refs)
        say(f"listed {len(refs)} chats")

        # Read cached state before the listing upsert, so title changes are visible.
        cached = {summary.id: summary for summary in store.chat_summaries(conn)}

        seen_at = store.utc_now()
        store.upsert_listed_chats(conn, refs, seen_at=seen_at)

        targets = _select_targets(refs, cached, options, full=options.full)
        result.stale = len(store.mark_missing_stale(conn, {ref.id for ref in refs}))

        failed_in_a_row = 0
        for index, ref in enumerate(targets, start=1):
            if options.limit is not None and result.fetched >= options.limit:
                break
            say(f"[{index}/{len(targets)}] {ref.title[:60]}")
            try:
                chat = source.fetch_chat(ref.id)
                before = cached.get(ref.id)
                changed = _save_if_changed(conn, chat, options.dry_run)
                result.fetched += 1
                if before is None or before.fetched_at is None:
                    result.new += 1
                elif changed:
                    result.updated += 1
                else:
                    result.unchanged += 1
                failed_in_a_row = 0
            except ParseDriftError as exc:
                failed_in_a_row += 1
                message = f"{ref.id}: parse drift: {exc}"
                result.errors.append(message)
                log.warning("sync: %s", message)
                if failed_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                    raise SyncAborted(
                        f"stopped after {failed_in_a_row} consecutive parse failures; "
                        "the ChatGPT page layout probably changed"
                    ) from exc
            except Exception as exc:
                failed_in_a_row += 1
                message = f"{ref.id}: {type(exc).__name__}: {exc}"
                result.errors.append(message)
                log.warning("sync: %s", message)
                if failed_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                    raise SyncAborted(
                        f"stopped after {failed_in_a_row} consecutive failures: {exc}"
                    ) from exc
            delay = rng.uniform(options.min_delay_s, options.max_delay_s)
            if delay > 0:
                sleep(delay)

        result.status = "partial" if result.errors else "ok"
        result.finished_at = store.utc_now()
        summary = result.summary()
        store.update_sync_run(conn, run_id, result.status, summary, result.finished_at)  # type: ignore[arg-type]
        key = "last_full_sync" if options.full else "last_incremental_sync"
        store.set_meta(conn, key, result.finished_at)
        if options.full or not result.errors:
            store.drop_migration_backup(resolved_db)
        say(f"done: {result.status} ({result.fetched} fetched, {len(result.errors)} errors)")
        return result
    except LoginRequired:
        _finish_failed(conn, run_id, result, "login required")
        raise
    except SyncAborted as exc:
        _finish_failed(conn, run_id, result, str(exc))
        result.status = "failed"
        raise
    except BaseException:
        _finish_failed(conn, run_id, result, "interrupted")
        raise
    finally:
        conn.close()


def _finish_failed(conn: object, run_id: int, result: SyncResult, note: str) -> None:
    result.status = "failed"
    result.errors.append(note)
    result.finished_at = store.utc_now()
    try:
        store.update_sync_run(conn, run_id, "failed", result.summary(), result.finished_at)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover - run row is best-effort
        log.debug("could not record failed sync run")


def _select_targets(
    refs: list[ChatRef],
    cached: dict[str, store.ChatSummary],
    options: SyncOptions,
    *,
    full: bool,
) -> list[ChatRef]:
    if full:
        return [ref for ref in refs if _needs_fetch(cached.get(ref.id))]

    targets: list[ChatRef] = []
    walked_known = 0
    for ref in sorted(
        refs, key=lambda r: r.sidebar_rank if r.sidebar_rank is not None else 1 << 30
    ):
        summary = cached.get(ref.id)
        if summary is None or _needs_fetch(summary):
            targets.append(ref)
            continue
        if summary.title != ref.title:
            targets.append(ref)
            continue
        if walked_known < options.recent_window:
            targets.append(ref)
            walked_known += 1
    return targets


def _needs_fetch(summary: store.ChatSummary | None) -> bool:
    if summary is None or summary.fetched_at is None:
        return True
    return summary.parse_version < store.DEFAULT_PARSE_VERSION


def _save_if_changed(conn: object, chat: ExtractedChat, dry_run: bool) -> bool:
    turns = [
        Turn(
            role=turn.role,
            markdown=turn.markdown,
            text=turn.text,
            alternatives=turn.alternatives,
        )
        for turn in chat.turns
    ]
    new_hash = store.content_hash(turns)
    existing = store.get_chat(conn, chat.id)  # type: ignore[arg-type]
    if existing is not None and existing.content_hash == new_hash:
        return False
    if dry_run:
        return True
    store.save_chat(conn, chat.id, turns, new_hash)  # type: ignore[arg-type]
    log.info("saved chat %s (%d turns)", chat.id, len(turns))
    return True


def iter_chunk(items: Iterable[ChatRef], size: int) -> Iterable[list[ChatRef]]:
    chunk: list[ChatRef] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
