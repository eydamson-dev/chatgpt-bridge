from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chatgpt_bridge import __version__, config, paths, query, store
from chatgpt_bridge.adapters.chatgpt import ChatGPTAdapter, LoginRequired, chat_id_from_url
from chatgpt_bridge.browser import Browser, BrowserError
from chatgpt_bridge.lock import LockBusy, is_locked, sync_lock
from chatgpt_bridge.logging_setup import get_logger, log_file, setup_logging
from chatgpt_bridge.parse import ParseDriftError
from chatgpt_bridge.sync import SyncOptions, run_sync

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_BROWSER = 3
EXIT_DRIFT = 4
EXIT_NOT_FOUND = 5
EXIT_LOGIN = 6
EXIT_LOCKED = 7
EXIT_CACHE = 8

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatgpt-bridge",
        description="Read and search your ChatGPT web conversations.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, metavar="FILE", help="config file to use")
    parser.add_argument("--cdp-url", metavar="URL", help="browser DevTools endpoint")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="show app version")
    sub.add_parser("status", help="browser, cache, and last sync")
    sub.add_parser("doctor", help="check browser, login, parser, and database")

    sync = sub.add_parser("sync", help="fetch chats into the local cache")
    sync.add_argument("--full", action="store_true", help="refetch every chat")
    sync.add_argument("--limit", type=int, metavar="N", help="fetch at most N chats")

    listing = sub.add_parser("list", help="list cached chats")
    listing.add_argument("--section", choices=["pinned", "projects", "recents"])
    listing.add_argument("--stale", action="store_true", help="only chats missing from the sidebar")
    listing.add_argument("--fresh", action="store_true", help="only chats still in the sidebar")
    listing.add_argument("--unfetched", action="store_true", help="only chats without messages")
    listing.add_argument("--limit", type=int)

    show = sub.add_parser("show", help="print one cached chat")
    show.add_argument("chat", help="chat id or URL")
    show.add_argument("--format", choices=["text", "md", "json"], default="text")

    find = sub.add_parser("search", help="full-text search over cached messages")
    find.add_argument("query")
    find.add_argument("--chat", help="restrict to one chat")
    find.add_argument("--section", choices=["pinned", "projects", "recents"])
    find.add_argument("--since", metavar="DATE", help="only chats fetched on or after DATE")
    find.add_argument("--limit", type=int)

    cfg = sub.add_parser("config", help="show effective settings")
    cfg.add_argument("--show", action="store_true")

    export = sub.add_parser("export-raw", help="dump the cache as JSON Lines")
    export.add_argument("--out", type=Path, metavar="FILE")

    reset = sub.add_parser("reset", help="delete the local cache")
    reset.add_argument("--yes", action="store_true", help="do not prompt")

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
    settings = resolved.settings
    setup_logging(settings)

    handlers: dict[str, Any] = {
        "version": _cmd_version,
        "config": _cmd_config,
        "status": _cmd_status,
        "doctor": _cmd_doctor,
        "sync": _cmd_sync,
        "list": _cmd_list,
        "show": _cmd_show,
        "search": _cmd_search,
        "export-raw": _cmd_export_raw,
        "reset": _cmd_reset,
    }
    return _run(handlers[args.command], args, resolved)


def _run(handler: Any, args: argparse.Namespace, resolved: config.Resolved) -> int:
    try:
        return int(handler(args, resolved))
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_OK
    except config.ConfigError as exc:
        _err(str(exc))
        return EXIT_ERROR
    except LockBusy as exc:
        _err(str(exc))
        return EXIT_LOCKED
    except LoginRequired as exc:
        _err(str(exc))
        return EXIT_LOGIN
    except ParseDriftError as exc:
        _err(f"could not parse the page: {exc}")
        return EXIT_DRIFT
    except query.NotFound as exc:
        _err(str(exc))
        return EXIT_NOT_FOUND
    except BrowserError as exc:
        _err(str(exc))
        return EXIT_BROWSER
    except store.NewerSchemaError as exc:
        _err(str(exc))
        return EXIT_CACHE
    except store.StoreError as exc:
        _err(str(exc))
        return EXIT_CACHE
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _err("interrupted")
        return EXIT_ERROR


def _err(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _out(args: argparse.Namespace, payload: Any, text: str) -> None:
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
        ("recent_window", settings.recent_window),
        ("lock_timeout_s", settings.lock_timeout_s),
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
    last = query.last_run()
    payload = {
        "data_dir": str(resolved.settings.data_dir),
        "db_path": str(paths.db_path()),
        "sync_running": is_locked(paths.lock_path()),
        "chats": counts.chats,
        "fetched": counts.fetched,
        "stale": counts.stale,
        "turns": counts.turns,
        "last_full_sync": counts.last_full_sync,
        "last_incremental_sync": counts.last_incremental_sync,
        "last_run": last,
        "log_file": str(log_file()),
    }
    text = "\n".join(
        [
            f"database      {payload['db_path']}",
            f"chats         {counts.chats} ({counts.fetched} fetched, {counts.stale} stale)",
            f"messages      {counts.turns}",
            f"sync running  {'yes' if payload['sync_running'] else 'no'}",
            f"last full     {counts.last_full_sync or 'never'}",
            f"last update   {counts.last_incremental_sync or 'never'}",
            f"log file      {payload['log_file']}",
        ]
    )
    _out(args, payload, text)
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace, resolved: config.Resolved) -> int:
    from chatgpt_bridge import doctor

    report = doctor.run_checks(resolved.settings)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return EXIT_OK if report.ok else EXIT_ERROR


def _cmd_sync(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    options = SyncOptions(
        full=args.full,
        limit=args.limit,
        recent_window=settings.recent_window,
        min_delay_s=settings.min_delay_s,
        max_delay_s=settings.max_delay_s,
    )
    progress = None if args.quiet or args.json else (lambda message: print(message, flush=True))
    with (
        sync_lock(paths.lock_path(), timeout_s=settings.lock_timeout_s),
        Browser(settings.cdp_url) as browser,
    ):
        adapter = ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        result = run_sync(adapter, options, progress=progress)
    payload = {"status": result.status, **result.summary()}
    _out(
        args,
        payload,
        f"{result.status}: listed {result.listed}, fetched {result.fetched} "
        f"(new {result.new}, updated {result.updated}, unchanged {result.unchanged}), "
        f"stale {result.stale}, errors {len(result.errors)}",
    )
    if result.errors and not args.json:
        for message in result.errors[:10]:
            print(f"  {message}", file=sys.stderr)
    return EXIT_OK if not result.errors else EXIT_ERROR


def _cmd_list(args: argparse.Namespace, resolved: config.Resolved) -> int:
    stale = True if args.stale else (False if args.fresh else None)
    rows = query.list_chats(
        section=args.section, stale=stale, unfetched=args.unfetched, limit=args.limit
    )
    payload = [
        {
            "id": row.id,
            "title": row.title,
            "url": row.url,
            "section": row.section,
            "turns": row.turn_count,
            "fetched_at": row.fetched_at,
            "stale": row.stale,
        }
        for row in rows
    ]
    text = "\n".join(
        f"{row.id[:8]}  {row.section or '-':<8}  {_turn_count(row.turn_count):>4}  "
        f"{row.title[:60]}{'  [stale]' if row.stale else ''}"
        for row in rows
    )
    _out(args, payload, text or "no chats cached; run: chatgpt-bridge sync")
    return EXIT_OK


def _turn_count(value: int | None) -> str:
    return "?" if value is None else str(value)


def _cmd_show(args: argparse.Namespace, resolved: config.Resolved) -> int:
    chat_id = chat_id_from_url(args.chat)
    chat = query.get_chat(chat_id)
    if args.format == "json" or args.json:
        print(json.dumps(query.envelope(chat).as_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.format == "md":
        print(query.render_markdown(chat), end="")
        return EXIT_OK
    print(query.render_text(chat), end="")
    return EXIT_OK


def _cmd_search(args: argparse.Namespace, resolved: config.Resolved) -> int:
    hits = query.search(
        args.query,
        chat_id=chat_id_from_url(args.chat) if args.chat else None,
        section=args.section,
        since=args.since,
        limit=args.limit,
    )
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
    _out(args, payload, text or "no matches")
    return EXIT_OK


def _cmd_export_raw(args: argparse.Namespace, resolved: config.Resolved) -> int:
    from chatgpt_bridge import maintenance

    target = maintenance.export_raw(args.out)
    counts = query.counts()
    if args.json:
        _out(args, {"path": str(target), "chats": counts.chats}, "")
    else:
        print(f"wrote {target} ({counts.chats} chats, {counts.turns} messages)")
    return EXIT_OK


def _cmd_reset(args: argparse.Namespace, resolved: config.Resolved) -> int:
    from chatgpt_bridge import maintenance

    if not args.yes:
        if not sys.stdin.isatty():
            _err("refusing to delete the cache without --yes when stdin is not a terminal")
            return EXIT_USAGE
        answer = input("Delete the local chat cache? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("cancelled")
            return EXIT_OK
    maintenance.reset()
    print("cache deleted")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
