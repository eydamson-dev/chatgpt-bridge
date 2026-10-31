from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chatgpt_bridge import __version__, config, paths, query, store
from chatgpt_bridge.adapters.chatgpt import ChatGPTAdapter, ChatNotFound, LoginRequired
from chatgpt_bridge.browser import Browser, BrowserError
from chatgpt_bridge.fetch import fetch_chat
from chatgpt_bridge.lock import LockBusy, is_locked, sync_lock
from chatgpt_bridge.logging_setup import get_logger, log_file, setup_logging
from chatgpt_bridge.parse import ParseDriftError
from chatgpt_bridge.resolve import AmbiguousTarget, TargetNotFound

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_BROWSER = 3
EXIT_DRIFT = 4
EXIT_NOT_FOUND = 5
EXIT_LOGIN = 6
EXIT_AMBIGUOUS = 7
EXIT_CACHE = 8
EXIT_LOCKED = 9

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatgpt-bridge",
        description="Read your ChatGPT web conversations on demand, through a logged-in browser.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, metavar="FILE", help="config file to use")
    parser.add_argument("--cdp-url", metavar="URL", help="browser DevTools endpoint")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="show app version")
    sub.add_parser("status", help="local store size and last fetch")
    sub.add_parser("doctor", help="check browser, login, parser, and database")

    chats = sub.add_parser("chats", help="list chats")
    chats.add_argument("--stored", action="store_true", help="only chats already fetched")
    chats.add_argument("--limit", type=int)

    fetch = sub.add_parser("fetch", help="fetch one chat into the local store")
    fetch.add_argument("target", help="chat URL, id, id prefix, or title")
    fetch.add_argument("--refresh", action="store_true", help="refetch even if already stored")
    fetch.add_argument("--format", choices=["text", "md", "json"], default="md")

    show = sub.add_parser("show", help="print a stored chat")
    show.add_argument("target")
    show.add_argument("--format", choices=["text", "md", "json"], default="text")

    find = sub.add_parser("search", help="search stored messages")
    find.add_argument("query")
    find.add_argument("--chat", help="restrict to one stored chat")
    find.add_argument("--limit", type=int)

    forget = sub.add_parser("forget", help="remove one chat from the local store")
    forget.add_argument("target")
    forget.add_argument("--yes", action="store_true", help="do not prompt")

    cfg = sub.add_parser("config", help="show effective settings")
    cfg.add_argument("--show", action="store_true")

    export = sub.add_parser("export-raw", help="dump the local store as JSON Lines")
    export.add_argument("--out", type=Path, metavar="FILE")

    reset = sub.add_parser("reset", help="delete every stored chat")
    reset.add_argument("--yes", action="store_true", help="do not prompt")

    sync = sub.add_parser("sync", help=argparse.SUPPRESS)
    sync.add_argument("--full", action="store_true", help=argparse.SUPPRESS)
    sync.add_argument("--limit", type=int, help=argparse.SUPPRESS)
    sync.add_argument("rest", nargs="*", help=argparse.SUPPRESS)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        resolved = config.load(flags={"cdp_url": args.cdp_url}, config_path=args.config)
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    setup_logging(resolved.settings)

    handlers: dict[str, Any] = {
        "version": _cmd_version,
        "config": _cmd_config,
        "status": _cmd_status,
        "doctor": _cmd_doctor,
        "chats": _cmd_chats,
        "fetch": _cmd_fetch,
        "show": _cmd_show,
        "search": _cmd_search,
        "forget": _cmd_forget,
        "export-raw": _cmd_export_raw,
        "reset": _cmd_reset,
        "sync": _cmd_sync_removed,
    }
    return _run(handlers[args.command], args, resolved)


def _run(handler: Any, args: argparse.Namespace, resolved: config.Resolved) -> int:
    try:
        return int(handler(args, resolved))
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK
    except (config.ConfigError, store.StoreError) as exc:
        _err(str(exc))
        return EXIT_CACHE if isinstance(exc, store.StoreError) else EXIT_ERROR
    except AmbiguousTarget as exc:
        _err(str(exc))
        for chat_id, title in exc.candidates:
            print(f"  {chat_id[:8]}  {title}", file=sys.stderr)
        return EXIT_AMBIGUOUS
    except TargetNotFound as exc:
        _err(str(exc))
        return EXIT_NOT_FOUND
    except query.NotFound as exc:
        _err(str(exc))
        return EXIT_NOT_FOUND
    except LoginRequired as exc:
        _err(str(exc))
        return EXIT_LOGIN
    except ParseDriftError as exc:
        _err(f"could not parse the page: {exc}")
        return EXIT_DRIFT
    except ChatNotFound as exc:
        _err(str(exc))
        return EXIT_NOT_FOUND
    except BrowserError as exc:
        _err(str(exc))
        return EXIT_BROWSER
    except LockBusy as exc:
        _err(str(exc))
        return EXIT_LOCKED
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _err("interrupted")
        return EXIT_ERROR


def _err(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _emit(args: argparse.Namespace, payload: Any, text: str) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)


# ------------------------------------------------------------- commands


def _cmd_version(args: argparse.Namespace, resolved: config.Resolved) -> int:
    print(f"chatgpt-bridge {__version__}")
    return EXIT_OK


def _cmd_config(args: argparse.Namespace, resolved: config.Resolved) -> int:
    if not args.show:
        _err("usage: chatgpt-bridge config --show")
        return EXIT_USAGE
    settings = resolved.settings
    rows: list[tuple[str, Any]] = [
        ("cdp_url", settings.cdp_url),
        ("min_delay_s", settings.min_delay_s),
        ("max_delay_s", settings.max_delay_s),
        ("lookup_limit", settings.lookup_limit),
        ("log_level", settings.log_level),
        ("data_dir", settings.data_dir),
        ("config_file", settings.config_file),
    ]
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name:<{width}}  {value}  [{resolved.sources.get(name, 'default')}]")
    print(f"{'log_file':<{width}}  {log_file()}  [default]")
    return EXIT_OK


def _cmd_status(args: argparse.Namespace, resolved: config.Resolved) -> int:
    counts = query.counts()
    last = query.last_fetch()
    payload = {
        "data_dir": str(resolved.settings.data_dir),
        "db_path": str(paths.db_path()),
        "chats": counts.chats,
        "turns": counts.turns,
        "last_fetch": counts.last_fetch,
        "fetch_running": is_locked(paths.lock_path()),
        "last_fetch_detail": None
        if last is None
        else {"chat_id": last.chat_id, "status": last.status, "fetched_at": last.fetched_at},
        "log_file": str(log_file()),
    }
    text = "\n".join(
        [
            f"database     {payload['db_path']}",
            f"stored chats {counts.chats} ({counts.turns} messages)",
            f"fetching     {'yes' if payload['fetch_running'] else 'no'}",
            f"last fetch   {counts.last_fetch or 'never'}",
            f"log file     {payload['log_file']}",
        ]
    )
    _emit(args, payload, text)
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace, resolved: config.Resolved) -> int:
    from chatgpt_bridge import doctor

    report = doctor.run_checks(resolved.settings)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return EXIT_OK if report.ok else EXIT_ERROR


def _cmd_chats(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    if args.stored:
        rows = query.list_cached(limit=args.limit)
        payload = [
            {
                "id": row.id,
                "title": row.title,
                "url": row.url,
                "turns": row.turn_count,
                "fetched_at": row.fetched_at,
            }
            for row in rows
        ]
        text = "\n".join(
            f"{row.id[:8]}  {_turn_count(row.turn_count):>4}  {row.fetched_at[:10]}  "
            f"{row.title[:60]}"
            for row in rows
        )
        _emit(args, payload, text or "no stored chats yet; use: chatgpt-bridge fetch <target>")
        return EXIT_OK

    with Browser(settings.cdp_url) as browser:
        adapter = ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        refs = adapter.list_chats(scroll=False, limit=args.limit or settings.lookup_limit)
    payload = [
        {"id": ref.id, "title": ref.title, "url": ref.url, "section": ref.section} for ref in refs
    ]
    text = "\n".join(f"{ref.id[:8]}  {ref.section or '-':<8}  {ref.title[:60]}" for ref in refs)
    _emit(args, payload, text)
    return EXIT_OK


def _cmd_fetch(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings

    def session() -> Any:
        browser = Browser(settings.cdp_url)
        browser.connect()
        return _Session(
            browser, ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        )

    with sync_lock(paths.lock_path(), timeout_s=0):
        result = fetch_chat(
            args.target,
            refresh=args.refresh,
            session=session,
            lookup_limit=settings.lookup_limit,
        )
    chat = result.chat
    payload = {
        "outcome": result.outcome,
        "changed": result.changed,
        **query.envelope(chat).as_dict(),
    }
    if args.format == "json" or args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        if not args.quiet:
            print(
                f"{result.outcome}: {chat.title} ({len(chat.turns)} messages, {chat.fetched_at})",
                file=sys.stderr,
            )
        print(
            query.render_markdown(chat) if args.format == "md" else query.render_text(chat), end=""
        )
    return EXIT_OK


class _Session:
    """Pairs the browser with the adapter so a fetch can close both."""

    def __init__(self, browser: Browser, adapter: ChatGPTAdapter) -> None:
        self.browser = browser
        self.adapter = adapter

    def __enter__(self) -> ChatGPTAdapter:
        return self.adapter

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.browser.close()


def _cmd_show(args: argparse.Namespace, resolved: config.Resolved) -> int:
    chat = query.get_chat(args.target)
    if args.format == "json" or args.json:
        print(json.dumps(query.envelope(chat).as_dict(), ensure_ascii=False, indent=2, default=str))
    elif args.format == "md":
        print(query.render_markdown(chat), end="")
    else:
        print(query.render_text(chat), end="")
    return EXIT_OK


def _cmd_search(args: argparse.Namespace, resolved: config.Resolved) -> int:
    hits = query.search(args.query, chat=args.chat, limit=args.limit)
    payload = [
        {
            "chat_id": hit.chat_id,
            "title": hit.title,
            "position": hit.position,
            "role": hit.role,
            "snippet": hit.snippet,
        }
        for hit in hits
    ]
    text = "\n\n".join(
        f"{hit.chat_id[:8]}  [{hit.position}] {hit.role}\n  {hit.snippet}" for hit in hits
    )
    if not hits:
        text = (
            "no matches in stored chats "
            f"({query.counts().chats} stored). Fetch more with: chatgpt-bridge fetch <target>"
        )
    _emit(args, payload, text)
    return EXIT_OK


def _cmd_forget(args: argparse.Namespace, resolved: config.Resolved) -> int:
    if not args.yes:
        if not sys.stdin.isatty():
            _err("refusing to remove a stored chat without --yes when stdin is not a terminal")
            return EXIT_USAGE
        if input(f"Remove {args.target!r} from the local store? [y/N] ").strip().lower() not in {
            "y",
            "yes",
        }:
            print("cancelled")
            return EXIT_OK
    chat_id = query.forget(args.target)
    if args.json:
        print(json.dumps({"forgotten": chat_id}))
    else:
        print(f"removed {chat_id} from the local store")
    return EXIT_OK


def _cmd_export_raw(args: argparse.Namespace, resolved: config.Resolved) -> int:
    from chatgpt_bridge import maintenance

    target = maintenance.export_raw(args.out)
    counts = query.counts()
    if args.json:
        print(json.dumps({"path": str(target), "chats": counts.chats}))
    else:
        print(f"wrote {target} ({counts.chats} chats, {counts.turns} messages)")
    return EXIT_OK


def _cmd_reset(args: argparse.Namespace, resolved: config.Resolved) -> int:
    from chatgpt_bridge import maintenance

    if not args.yes:
        if not sys.stdin.isatty():
            _err("refusing to delete the local store without --yes when stdin is not a terminal")
            return EXIT_USAGE
        answer = input("Delete the local chat store? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("cancelled")
            return EXIT_OK
    maintenance.reset()
    if args.json:
        print(json.dumps({"reset": True}))
    else:
        print("local store deleted")
    return EXIT_OK


def _cmd_sync_removed(args: argparse.Namespace, resolved: config.Resolved) -> int:
    _err(
        "`sync` was removed: chatgpt-bridge now fetches chats on demand.\n"
        'use: chatgpt-bridge fetch "<title, id, or url>"'
    )
    return EXIT_USAGE


def _turn_count(value: int | None) -> str:
    return "?" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
