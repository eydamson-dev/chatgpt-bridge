# chatgpt-bridge

Pull a specific ChatGPT conversation into your terminal or into OpenCode, on
demand.

[![CI](https://github.com/eydamson-dev/chatgpt-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/eydamson-dev/chatgpt-bridge/actions/workflows/ci.yml)

Repository: <https://github.com/eydamson-dev/chatgpt-bridge> · MIT licensed

It attaches to a Chromium-based browser you are already logged into, opens that
one chat in its own background tab, extracts it, and stores it in a local SQLite
database. It does not mirror your chat history: nothing is fetched until you
name it.

- No API key. It uses the browser session you already have.
- No cookies, tokens, or profile files are read, stored, or logged.
- Read-only. It cannot send messages. The proposed write path is in
  [`docs/SEND-MESSAGE-DESIGN.md`](docs/SEND-MESSAGE-DESIGN.md) and is not built.
- Local only. No listener, no daemon, no telemetry.

## Why

You wrote a plan in ChatGPT. You want your coding agent to pick it up, or you
want to search an old conversation. Name the chat, and it comes back as
structured markdown.

## How it works

```
CLI / MCP server
      |
  resolve target (URL, id, prefix, title)  ->  stored? ->  yes: return it
      |                                              \->  no: open one tab
      |                                                        |
   query store (search, show)                          ChatGPT adapter
                                                        (extract, validate)
                                                              |
                                                        SQLite + FTS5
```

- **On demand:** one fetch = one chat. Nothing scans your history.
- **Real storage, not a cache:** conversations you have not fetched exist
  nowhere locally, so removing a chat removes your only local copy.
- **Title lookup:** reads the visible sidebar list (top ~50 entries) so a title
  can be turned into a chat ID. It never scrolls.
- **Markdown plus text:** code fences, tables, links, and lists survive; plain
  text drives search.

## Requirements

- Python 3.12+
- A Chromium-based browser started with a DevTools port (Chrome, Brave, Edge,
  Chromium)
- `uv` for installation

## Install

```bash
uv tool install --editable ~/projects/chatgpt-bridge   # development
uv tool install ~/projects/chatgpt-bridge              # normal use
```

Installs `chatgpt-bridge` and `chatgpt-bridge-mcp`.

## Browser setup

The browser must expose a DevTools port, and you must be logged in to
<https://chatgpt.com>. The app never starts or stops your browser.

Brave:

```bash
brave-browser --remote-debugging-port=9222
```

Chrome (recent versions ignore the flag on the default profile, so use a
separate one):

```bash
google-chrome --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.local/share/chatgpt-bridge/profile"
```

macOS uses the same flags with
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`; Windows uses
`chrome.exe --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\chatgpt-bridge\profile`.

Check it:

```bash
curl -s http://127.0.0.1:9222/json/version
```

## Use

```bash
chatgpt-bridge doctor                    # check browser, login, parser, database
chatgpt-bridge chats                     # visible sidebar (read-only lookup)
chatgpt-bridge chats --stored            # what is in the local store
chatgpt-bridge fetch "Lenovo ThinkPad Review"
chatgpt-bridge fetch 6ae5de7d            # id prefix works too
chatgpt-bridge fetch https://chatgpt.com/c/<id>
chatgpt-bridge fetch "<title>" --refresh # refetch and report whether it changed
chatgpt-bridge show "<title>"            # local store only, no browser
chatgpt-bridge show "<title>" --format md
chatgpt-bridge search "pkg-config"       # stored messages only
chatgpt-bridge forget "<title>"          # remove one chat from the local store
chatgpt-bridge export-raw                # JSON Lines dump
chatgpt-bridge reset                     # delete the local store (prompts)
chatgpt-bridge status                    # store size, last fetch, log path
```

Every read command accepts `--json`.

Exit codes: `0` ok, `1` error, `2` usage, `3` browser unreachable, `4` parse
drift, `5` not found, `6` login required, `7` ambiguous target, `8` store error,
`9` a fetch is already running.

### Naming a chat

1. A URL: `https://chatgpt.com/c/<id>`.
2. A full ID or a unique ID prefix, as printed by `chats`.
3. A title: exact match first, then a unique case-insensitive substring.

If several chats match, nothing is fetched; the candidates are listed and you
pick one. If nothing matches, `chats` shows what is available.

### Storage behaviour

The SQLite database at `~/.local/share/chatgpt-bridge/bridge.db` (Linux) **is**
the local store. It is not a throwaway cache: chats you have not fetched are not
saved anywhere locally, and `forget`/`reset` delete your only local copy (the
conversation still exists in ChatGPT).

- A stored chat is returned as-is; pass `--refresh` to refetch.
- `--refresh` reports `changed: true|false` from a content hash, so an unchanged
  chat does not disturb anything downstream.
- Nothing expires, because nothing is fetched in the background. `status` and
  `chats --stored` show fetch times so age is visible.

## OpenCode integration

Add to `~/.config/opencode/opencode.json`:

```json
"chatgpt-bridge": {
  "type": "local",
  "command": ["chatgpt-bridge-mcp"]
}
```

Tools: `status`, `chats`, `fetch_chat`, `get_chat`, `search_chats`,
`forget_chat`, `doctor`.

`fetch_chat` is the one that retrieves a chat. `get_chat` and `search_chats`
read the local store only and never open a page. Chat text is returned inside an
untrusted-content envelope; it is data, not instructions. Enable this server per
session rather than globally.

## Configuration

`~/.config/chatgpt-bridge/config.toml` (Linux; platform equivalent elsewhere):

```toml
[browser]
cdp_url = "http://127.0.0.1:9222"

[pacing]
min_delay_s = 1.0
max_delay_s = 2.0

[lookup]
limit = 50          # sidebar entries read when resolving a title

[logging]
level = "info"
```

Precedence: flags, then environment, then the config file, then defaults.
`chatgpt-bridge config --show` prints each value and its source.

Environment overrides: `CHATGPT_BRIDGE_CDP_URL`, `CHATGPT_BRIDGE_DATA_DIR`,
`CHATGPT_BRIDGE_CONFIG`.

Data lives under the platform data directory (`~/.local/share/chatgpt-bridge/`
on Linux); the database is user-only readable.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `browser unreachable at http://127.0.0.1:9222` | Start the browser with `--remote-debugging-port=9222`. |
| `ChatGPT is not logged in in this browser` | Log in at <https://chatgpt.com>, then retry. |
| `nothing matched '...'` | Run `chatgpt-bridge chats`, or pass the chat URL. |
| `matches several visible chats` | Use a longer title or the chat URL. |
| `... is not in the local store` | Run `chatgpt-bridge fetch "<target>"` first. |
| `could not parse the page` | The ChatGPT layout changed; update selectors in `src/chatgpt_bridge/adapters/chatgpt.py`, then run `doctor`. |
| `no messages appeared for chat` | The chat is deleted or not available. |

Logs: `chatgpt-bridge status` prints the path. Logs record counts, IDs, timings,
and error types — never message text.

## Known limitations

- **Page-dependent.** Selectors live in one adapter so a fix is one file.
- **Sidebar-only discovery.** Chats inside a ChatGPT Project are not listed
  while collapsed, so they are outside lookup scope (a URL still works).
- **Archived chats** are not reachable.
- **Branch history.** Only the displayed branch is stored; alternative count is
  best-effort.
- **Attachments.** Names and types only; files are not downloaded.
- **Terms of use.** Automating the ChatGPT web UI may conflict with OpenAI's
  terms. Use it on your own account, at a low rate.
- **Platforms.** Verified on Linux with Brave. macOS and Windows are untested.

## License

MIT — see [`LICENSE`](LICENSE).

The repository contains no chat data. Conversations live only in your local
database (`~/.local/share/chatgpt-bridge/bridge.db`), which is git-ignored.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run pytest -m live      # needs a reachable CDP browser; read-only
```

Start a new session with `docs/HANDOFF.md`; it carries the current state and the
invariants.
