from __future__ import annotations

import contextlib
import json
from pathlib import Path

from chatgpt_bridge import paths, query, store
from chatgpt_bridge.logging_setup import get_logger

log = get_logger("maintenance")


def export_raw(out: Path | None = None) -> Path:
    """Dump every cached chat as JSON Lines, one chat per line."""
    target = out or paths.data_dir() / "export.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = store.connect()
    try:
        with target.open("w", encoding="utf-8") as handle:
            for summary in store.chat_summaries(conn):
                chat = store.get_chat(conn, summary.id)
                if chat is None:
                    continue
                handle.write(json.dumps(query.envelope(chat).as_dict(), ensure_ascii=False))
                handle.write("\n")
    finally:
        conn.close()
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    log.info("exported raw cache to %s", target)
    return target


def reset() -> None:
    """Delete all cached chats. Leaves the schema in place and never touches the browser."""
    conn = store.connect()
    try:
        store.delete_all(conn)
        conn.execute("VACUUM")
    finally:
        conn.close()
    log.info("cache reset")
