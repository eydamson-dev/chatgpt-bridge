from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chatgpt_bridge import __version__, config, paths, query, store
from chatgpt_bridge import project as project_service
from chatgpt_bridge import send as send_service
from chatgpt_bridge.adapters.chatgpt import (
    ChatGPTAdapter,
    ChatNotFound,
    LoginRequired,
    ProjectCreateFailed,
    SendFailed,
)
from chatgpt_bridge.browser import Browser, BrowserError
from chatgpt_bridge.fetch import fetch_chat
from chatgpt_bridge.lock import LockBusy, is_locked, sync_lock
from chatgpt_bridge.logging_setup import get_logger, log_file, setup_logging
from chatgpt_bridge.parse import ParseDriftError
from chatgpt_bridge.resolve import (
    AmbiguousTarget,
    Resolved,
    TargetNotFound,
    resolve_with_listing,
)

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
EXIT_UNKNOWN = 10

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

    # Let global flags also follow the subcommand, e.g. `fetch x --json`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, metavar="FILE", default=argparse.SUPPRESS)
    common.add_argument("--cdp-url", metavar="URL", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kwargs)

    add("version", help="show app version")
    add("status", help="local store size and last fetch")
    add("doctor", help="check browser, login, parser, and database")

    add("projects", help="list ChatGPT projects")

    chats = add("chats", help="list chats")
    chats.add_argument("--stored", action="store_true", help="only chats already fetched")
    chats.add_argument("--limit", type=int)
    chats.add_argument("--project", metavar="NAME", help="only chats in this project")

    fetch = add("fetch", help="fetch one chat into the local store")
    fetch.add_argument("target", help="chat URL, id, id prefix, or title")
    fetch.add_argument("--refresh", action="store_true", help="refetch even if already stored")
    fetch.add_argument("--format", choices=["text", "md", "json"], default="md")
    fetch.add_argument("--project", metavar="NAME", help="resolve inside this project")

    send = add("send", help="send one message to an existing chat")
    send.add_argument("target", help="chat URL, id, id prefix, or title")
    send.add_argument("message", help="message to send")
    send.add_argument("--yes", action="store_true", help="do not prompt")
    send.add_argument("--dry-run", action="store_true", help="show the target without sending")
    send.add_argument("--project", metavar="NAME", help="resolve inside this project")

    new = add(
        "new",
        aliases=["create-chat"],
        help="create a new chat with one message",
    )
    new.add_argument("message", help="first message")
    new.add_argument("--yes", action="store_true", help="do not prompt")
    new.add_argument("--dry-run", action="store_true", help="show the message without sending")
    new.add_argument("--project", metavar="NAME", help="create the chat inside this project")

    new_project = add(
        "new-project",
        aliases=["create-project"],
        help="create a new ChatGPT project",
    )
    new_project.add_argument("name", help="project name")
    new_project.add_argument("--yes", action="store_true", help="do not prompt")
    new_project.add_argument(
        "--dry-run", action="store_true", help="show the name without creating"
    )

    show = add("show", help="print a stored chat")
    show.add_argument("target")
    show.add_argument("--format", choices=["text", "md", "json"], default="text")

    find = add("search", help="search stored messages")
    find.add_argument("query")
    find.add_argument("--chat", help="restrict to one stored chat")
    find.add_argument("--limit", type=int)
    find.add_argument("--project", metavar="NAME", help="restrict to one project")

    forget = add("forget", help="remove one chat from the local store")
    forget.add_argument("target")
    forget.add_argument("--yes", action="store_true", help="do not prompt")

    cfg = add("config", help="show effective settings")
    cfg.add_argument("--show", action="store_true")

    export = add("export-raw", help="dump the local store as JSON Lines")
    export.add_argument("--out", type=Path, metavar="FILE")

    reset = add("reset", help="delete every stored chat")
    reset.add_argument("--yes", action="store_true", help="do not prompt")

    sync = add("sync", help=argparse.SUPPRESS)
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
        "projects": _cmd_projects,
        "chats": _cmd_chats,
        "fetch": _cmd_fetch,
        "send": _cmd_send,
        "new": _cmd_new,
        "create-chat": _cmd_new,
        "new-project": _cmd_new_project,
        "create-project": _cmd_new_project,
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
    except send_service.SendError as exc:
        _err(str(exc))
        return EXIT_USAGE
    except project_service.ProjectError as exc:
        _err(str(exc))
        return EXIT_USAGE
    except ProjectCreateFailed as exc:
        _err(str(exc))
        return EXIT_ERROR
    except SendFailed as exc:
        _err(str(exc))
        return EXIT_ERROR
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
        "last_send": counts.last_send,
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
            f"last send    {counts.last_send or 'never'}",
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


def _cmd_projects(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    with Browser(settings.cdp_url) as browser:
        adapter = ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        projects = adapter.list_projects()
    payload = [{"name": project.name, "has_chats": project.has_chats} for project in projects]
    text = "\n".join(
        f"{project.name[:60]}" + ("" if project.has_chats else "  (no chats)")
        for project in projects
    )
    _emit(args, payload, text or "no projects on the projects page")
    return EXIT_OK


def _cmd_chats(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    if args.stored:
        rows = query.list_cached(limit=args.limit, project=args.project)
        payload = [
            {
                "id": row.id,
                "title": row.title,
                "url": row.url,
                "turns": row.turn_count,
                "fetched_at": row.fetched_at,
                "project": row.project,
            }
            for row in rows
        ]
        text = "\n".join(
            f"{row.id[:8]}  {_turn_count(row.turn_count):>4}  {row.fetched_at[:10]}  "
            f"{_project_prefix(row.project)}{row.title[:60]}"
            for row in rows
        )
        empty = "no stored chats yet; use: chatgpt-bridge fetch <target>"
        if args.project:
            empty = f"no stored chats in project {args.project!r}"
        _emit(args, payload, text or empty)
        return EXIT_OK

    limit = args.limit or settings.lookup_limit
    if args.project:
        with Browser(settings.cdp_url) as browser:
            adapter = ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
            refs = adapter.list_project_chats(args.project, limit=limit)
        payload = [
            {"id": ref.id, "title": ref.title, "url": ref.url, "project": ref.project}
            for ref in refs
        ]
        text = "\n".join(f"{ref.id[:8]}  {ref.title[:60]}" for ref in refs)
        _emit(args, payload, text or f"no chats in project {args.project!r}")
        return EXIT_OK

    with Browser(settings.cdp_url) as browser:
        adapter = ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        refs = adapter.list_chats(scroll=False, limit=limit)
    payload = [
        {"id": ref.id, "title": ref.title, "url": ref.url, "section": ref.section} for ref in refs
    ]
    text = "\n".join(f"{ref.id[:8]}  {ref.section or '-':<8}  {ref.title[:60]}" for ref in refs)
    _emit(args, payload, text)
    return EXIT_OK


def _project_prefix(project: str | None) -> str:
    return f"[{project[:20]}] " if project else ""


def _project_name(project: str | None, title: str) -> str:
    return f"{project} / {title}" if project else title


def _make_session(settings: config.Settings) -> Any:
    def session() -> Any:
        browser = Browser(settings.cdp_url)
        browser.connect()
        return _Session(
            browser, ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        )

    return session


def _cmd_fetch(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings

    with sync_lock(paths.lock_path(), timeout_s=0):
        result = fetch_chat(
            args.target,
            refresh=args.refresh,
            session=_make_session(settings),
            lookup_limit=settings.lookup_limit,
            project=args.project,
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


def _cmd_send(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    target = send_service.preview(args.target, project=args.project)
    label = (
        f'"{_project_name(target.project or args.project, target.title)}" ({target.id[:8]})'
        if target is not None
        else repr(args.target)
    )

    if args.dry_run:
        payload = {
            "dry_run": True,
            "target": args.target,
            "project": args.project,
            "resolved": None
            if target is None
            else {
                "id": target.id,
                "title": target.title,
                "url": target.url,
                "project": target.project or args.project,
            },
            "message": args.message,
        }
        text = f"would send to {label}: {_message_preview(args.message)}"
        _emit(args, payload, text)
        return EXIT_OK

    expected_chat_id: str | None = None
    if not args.yes:
        if not sys.stdin.isatty():
            _err("refusing to send without --yes when stdin is not a terminal")
            return EXIT_USAGE
        # Show the real chat title and ID, even for an uncached title.
        if target is None:
            target = _resolve_live(settings, args.target, args.project)
            label = (
                f'"{_project_name(target.project or args.project, target.title)}" ({target.id[:8]})'
            )
        if not _confirm("Send to", label, args.message):
            print("cancelled")
            return EXIT_OK
        expected_chat_id = target.id

    with sync_lock(paths.lock_path(), timeout_s=0):
        result = send_service.send_message(
            args.target,
            args.message,
            expected_chat_id=expected_chat_id,
            session=_make_session(settings),
            lookup_limit=settings.lookup_limit,
            project=args.project,
        )
    return _report_send(args, result)


def _resolve_live(settings: config.Settings, target: str, project: str | None = None) -> Resolved:
    with Browser(settings.cdp_url) as browser:
        adapter = ChatGPTAdapter(browser, page_delay_s=min(settings.min_delay_s, 1.0))
        refs = (
            adapter.list_project_chats(project, limit=settings.lookup_limit)
            if project
            else adapter.list_chats(scroll=False, limit=settings.lookup_limit)
        )
    return resolve_with_listing(target, refs)


def _cmd_new(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    where = f"in project {args.project!r}" if args.project else "a new chat"
    if args.dry_run:
        payload = {"dry_run": True, "message": args.message, "project": args.project}
        _emit(
            args,
            payload,
            f"would create {where} with: {_message_preview(args.message)}",
        )
        return EXIT_OK

    if not args.yes:
        if not sys.stdin.isatty():
            _err("refusing to create a chat without --yes when stdin is not a terminal")
            return EXIT_USAGE
        if not _confirm("Create a chat and send", where, args.message):
            print("cancelled")
            return EXIT_OK

    with sync_lock(paths.lock_path(), timeout_s=0):
        result = send_service.create_chat(
            args.message, session=_make_session(settings), project=args.project
        )
    return _report_send(args, result)


def _cmd_new_project(args: argparse.Namespace, resolved: config.Resolved) -> int:
    settings = resolved.settings
    name = project_service.validate_name(args.name)

    if args.dry_run:
        payload = {"dry_run": True, "name": name}
        _emit(args, payload, f"would create project {name!r}")
        return EXIT_OK

    if not args.yes:
        if not sys.stdin.isatty():
            _err("refusing to create a project without --yes when stdin is not a terminal")
            return EXIT_USAGE
        if not _confirm_project(name):
            print("cancelled")
            return EXIT_OK

    with sync_lock(paths.lock_path(), timeout_s=0):
        project = project_service.create_project(name, session=_make_session(settings))
    payload = {"name": project.name, "project_id": project.project_id}
    text = project.name + (f"  ({project.project_id})" if project.project_id else "")
    if not args.quiet:
        print(f"created: {project.name}", file=sys.stderr)
    _emit(args, payload, text)
    return EXIT_OK


def _confirm_project(name: str) -> bool:
    print(f"Create project {name!r} in ChatGPT", file=sys.stderr)
    return input("Proceed? [y/N] ").strip().lower() in {"y", "yes"}


def _confirm(action: str, label: str, message: str) -> bool:
    print(f"{action} {label}", file=sys.stderr)
    print(f"message: {_message_preview(message)}", file=sys.stderr)
    return input("Proceed? [y/N] ").strip().lower() in {"y", "yes"}


def _message_preview(message: str, width: int = 200) -> str:
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= width else f"{one_line[:width]}…"


def _report_send(args: argparse.Namespace, result: send_service.SendResult) -> int:
    if args.json:
        payload: dict[str, Any] = {
            "outcome": result.outcome,
            "chat_id": result.chat_id,
            "url": result.url,
            "reply": result.reply,
        }
        if result.chat is not None:
            payload["chat"] = query.envelope(result.chat).as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif result.outcome == "unknown":
        _err("the message may have been sent, but no complete reply arrived")
        print(result.url)
    else:
        print(result.reply or "")
    if not args.quiet:
        short_id = result.chat_id[:8] or "unknown"
        print(f"{result.outcome}: {short_id}", file=sys.stderr)
    return EXIT_OK if result.outcome == "sent" else EXIT_UNKNOWN


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
    hits = query.search(args.query, chat=args.chat, limit=args.limit, project=args.project)
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
        hint = query.search_hint(args.query, project=args.project)
        # Keep stdout valid JSON for scripts, but still explain the empty result.
        if args.json and not args.quiet:
            print(hint, file=sys.stderr)
        text = hint
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
