from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chatgpt_bridge import __version__, config
from chatgpt_bridge.logging_setup import log_file, setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatgpt-bridge",
        description="Read and search your ChatGPT web conversations.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, metavar="FILE", help="config file to use")
    parser.add_argument("--cdp-url", metavar="URL", help="browser DevTools endpoint")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.add_parser("version", help="show app version")

    cfg = subparsers.add_parser("config", help="show effective settings")
    cfg.add_argument("--show", action="store_true", help="print each setting and its source")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        resolved = config.load(
            flags={"cdp_url": args.cdp_url},
            config_path=args.config,
        )
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    setup_logging(resolved.settings)

    if args.command == "version":
        print(f"chatgpt-bridge {__version__}")
        return EXIT_OK

    if args.command == "config":
        if not args.show:
            print("usage: chatgpt-bridge config --show", file=sys.stderr)
            return EXIT_USAGE
        _print_config(resolved)
        return EXIT_OK

    parser.print_help()
    return EXIT_USAGE


def _print_config(resolved: config.Resolved) -> None:
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
        source = resolved.sources.get(name, "default")
        print(f"{name:<{width}}  {value}  [{source}]")
    print(f"{'log_file':<{width}}  {log_file()}  [default]")


if __name__ == "__main__":
    raise SystemExit(main())
