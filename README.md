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
- **Reads by default.** The only writes are one confirmed message to an
  existing chat (`send`) or one new chat with one message (`new`). Both prompt
  in the CLI; MCP requires `confirm=true`. See
  [`docs/SEND-MESSAGE-DESIGN.md`](docs/SEND-MESSAGE-DESIGN.md).
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

**Not on PyPI.** Install from source or straight from git.

### From git (recommended)

One command, no clone needed:

```bash
uv tool install git+https://github.com/eydamson-dev/chatgpt-bridge
```

### From a local clone

```bash
git clone https://github.com/eydamson-dev/chatgpt-bridge
cd chatgpt-bridge
uv tool install .
```

### For development

Editable install: source edits take effect immediately, no reinstall.

```bash
git clone https://github.com/eydamson-dev/chatgpt-bridge
cd chatgpt-bridge
uv sync
uv tool install --editable .
```

`uv sync` creates the project virtualenv; `--editable` puts the console scripts
on your `PATH` while pointing them at that venv.

### Run without installing

Inside the clone, prefix commands with `uv run`:

```bash
uv run chatgpt-bridge doctor
```

`uv run` needs a `pyproject.toml` in the current directory, so this only works
from inside the repository.

### Both entry points

Any install provides two commands:

| Command | Purpose |
|---|---|
| `chatgpt-bridge` | the CLI |
| `chatgpt-bridge-mcp` | the stdio MCP server |

### Verify

```bash
chatgpt-bridge --version
chatgpt-bridge doctor
```

`doctor` needs the browser running (see below); `--version` does not.

### Upgrade and remove

```bash
uv tool upgrade chatgpt-bridge      # git installs
uv tool uninstall chatgpt-bridge
```

### Register the MCP server

Point your MCP client at the absolute path of the installed server:

```bash
command -v chatgpt-bridge-mcp
```

OpenCode, in `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "servers": {
      "chatgpt-bridge": {
        "type": "local",
        "command": ["/home/you/.local/bin/chatgpt-bridge-mcp"]
      }
    }
  }
}
```

Restart the client, or it may pick the config up live. The seven read tools are
safe to leave enabled; `send_message` and `create_chat` still require
`confirm=true`, so enable the server per session if you prefer.

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
chatgpt-bridge status                    # store size, last fetch, last send, log path

chatgpt-bridge send "<title>" "Make a plan"     # prompts, then waits for the reply
chatgpt-bridge send "<title>" "Continue" --yes  # no prompt
chatgpt-bridge send "<title>" "Continue" --dry-run
chatgpt-bridge new "Start a new plan"           # prompts, creates a chat
chatgpt-bridge new "Start a new plan" --yes
```

`--json` works before or after the subcommand on every command.

Exit codes: `0` ok, `1` error, `2` usage, `3` browser unreachable, `4` parse
drift, `5` not found, `6` login required, `7` ambiguous target, `8` store error,
`9` a fetch is already running, `10` message sent but no complete reply
observed.

### Naming a chat

1. A URL: `https://chatgpt.com/c/<id>`.
2. A full ID or a unique ID prefix, as printed by `chats`.
3. A title: exact match first, then a unique case-insensitive substring.

If several chats match, nothing is fetched; the candidates are listed and you
pick one. If nothing matches, `chats` shows what is available.

### Searching

`search` reads stored message text only. It never opens a page, and it does not
index chat titles — so searching for a chat's name finds nothing until you have
fetched that chat.

When a search finds nothing, the hint says which case you are in:

```
$ chatgpt-bridge search "kubernetes"
no stored message contains 'kubernetes'. Only 3 chats are stored, and chat
titles are not searched. If you meant a chat from the sidebar, fetch it first:
chatgpt-bridge fetch "<title>"
```

```
$ chatgpt-bridge search "Fedora notes"
no stored message contains 'Fedora notes', but a stored chat is titled
'Fedora notes'. Read it with: chatgpt-bridge show 'Fedora notes'
```

With `--json`, stdout stays a valid JSON array and the hint goes to stderr, so
scripts are unaffected. The MCP `search_chats` tool returns the same text in a
`hint` field when `hits` is empty.

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

### Sending messages

`send` appends one message to one existing chat; `new` creates a chat with one
message. Both use a background tab, close it afterwards, and refresh the local
copy when the reply arrives.

- The CLI shows the chat title, ID, and message, then asks before sending.
  `--yes` skips the prompt; `--dry-run` resolves the target without a page or a
  send.
- A draft already in the composer is refused rather than overwritten.
- If the reply does not finish within the timeout, the command exits `10` and
  reports `unknown`. The message may have been sent; check the chat before
  retrying. Nothing retries automatically.
- `status` shows the last send time. The `send_log` table stores chat ID, time,
  and status only — never message text.

## OpenCode integration

Add to `~/.config/opencode/opencode.json`, using the absolute path printed by
`command -v chatgpt-bridge-mcp` (a bare name fails when OpenCode's `PATH`
differs from your shell's):

```json
"chatgpt-bridge": {
  "type": "local",
  "command": ["/home/you/.local/bin/chatgpt-bridge-mcp"]
}
```

If you have not installed the package, point at the clone's virtualenv instead:
`/path/to/chatgpt-bridge/.venv/bin/chatgpt-bridge-mcp`.

Tools: `status`, `chats`, `fetch_chat`, `get_chat`, `search_chats`,
`forget_chat`, `doctor`, `send_message`, `create_chat`.

`fetch_chat` retrieves a chat. `get_chat` and `search_chats` read the local
store only and never open a page. Chat text is returned inside an
untrusted-content envelope; it is data, not instructions.

`send_message` and `create_chat` are the only tools that change ChatGPT.
`send_message` is a dry run by default; sending requires `confirm=true` **and**
the `expected_chat_id` returned by the dry run, so a fuzzy title cannot send to
the wrong chat. `create_chat` requires `confirm=true`. Enable this server per
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
- **Writes are deliberately narrow.** One message per call to an existing chat,
  or one new chat with one message. No delete, rename, branch switching, model
  selection, attachments, or bulk sending.
- **Unknown send state.** If the reply does not arrive in time, the message may
  still have been accepted; the tool refuses to retry automatically.
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
CHATGPT_BRIDGE_ALLOW_SEND=1 uv run pytest -m live_send   # creates and sends
```

The latest sanitized end-to-end results are in
[`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md).

Start a new session with `docs/HANDOFF.md`; it carries the current state and the
invariants.
