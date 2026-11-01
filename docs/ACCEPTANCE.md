# Acceptance report — 0.3.0

**Date:** 2026-11-01
**Scope:** every CLI command and all nine MCP tools, against the real logged-in
browser. This file is sanitized: it records counts, exit codes, and statuses,
never chat text, titles, URLs, or chat IDs.

## Environment

| Item | Value |
|---|---|
| App | `chatgpt-bridge 0.3.0` |
| OS | Linux (Fedora, x86_64) |
| Browser | Chrome/Brave 156 over CDP at `127.0.0.1:9222` |
| Python | 3.14.8 and 3.12.15 |
| Data | throwaway data directories under `/tmp`, never the real store |
| Chat volume | one message per call, all test messages clearly labelled |

## Automated gates

| Gate | Result |
|---|---|
| `ruff check` / `ruff format --check` | clean |
| `mypy --strict` (whole package) | clean |
| `pytest` (offline: unit, fake source, CLI, MCP) | **232 passed** |
| `pytest -m live` (real browser, read-only) | **2 passed** |
| `CHATGPT_BRIDGE_ALLOW_SEND=1 pytest -m live_send` | **1 passed** |
| Python 3.12 test run | **232 passed**, same gates |
| Wheel build and clean-venv install | **0.3.0**, all 3 migrations present |
| Wheel CLI smoke test | `version`, `--help`, `status` work |

## CLI acceptance

Each command below ran against the live browser; the data directory was
throwaway.

| Feature | Result | Evidence |
|---|---|---|
| `doctor` | pass | 4 checks: config, database, browser, sidebar; "all checks passed" |
| `chats` (sidebar) | pass | returned the visible sidebar list, no scroll |
| `chats --stored` | pass | listed the stored test chats |
| `fetch` (first time) | pass | outcome `new`, conversation converted and stored |
| `fetch` (second time) | pass | outcome `cached`, no browser page opened |
| `fetch --refresh` | pass | outcome `refreshed`/`unchanged`, content hash compared |
| `show --format text` | pass | transcript rendered |
| `show --format md` | pass | markdown document rendered |
| `show --format json` | pass | valid envelope, turns and roles present |
| `search <term>` | pass | hits with snippets over stored messages |
| `search --chat <target>` | pass | scope filter works |
| `search` (no match) | pass | states how many chats are stored |
| `export-raw --out FILE` | pass | JSON Lines written, file mode `0600` |
| `forget --yes` | pass | removed one chat; `show` then exited `5` |
| `fetch` after `forget` | pass | re-fetched and stored the chat |
| `reset --yes` | pass | store emptied; `status` reported 0 chats |
| `status` / `status --json` | pass | store counts, last fetch, last send, log path |
| `config --show` | pass | every setting with its source |
| `send --dry-run` | pass | resolved target, no page, no send |
| `send` without a terminal | pass | refused with exit `2`, nothing sent |
| `send` interactive prompt, answer `n` | pass | prompt shown, "cancelled", exit `0`, nothing sent |
| `send` prompt for an uncached title | pass | resolved the sidebar first, showed the real title and ID, then cancelled |
| `send --yes --json` (by ID) | pass | outcome `sent`, reply returned, store refreshed |
| `send --dry-run` (by title) | pass | resolved the stored title without sending |
| `send --yes --json` (by title) | pass | outcome `sent`; turns grew from 6 to 8 |
| `new --dry-run` | pass | preview only, nothing sent |
| `new --yes --json` | pass | outcome `sent`, new chat created with 2 turns |
| `--json` after the subcommand | pass | parsed as JSON |
| Reply timeout → exit `10` | covered by unit test | real timeout not forced against the account |

## MCP acceptance (stdio)

| Tool | Result | Evidence |
|---|---|---|
| `status` | pass | stored chats/messages, last fetch and last send |
| `chats` | pass | local-store list, no browser |
| `fetch_chat` | pass | fetched and refreshed; outcome reported |
| `get_chat` | pass | untrusted envelope, turns returned |
| `search_chats` | pass | hits plus stored-chat count |
| `forget_chat` | pass | `confirm=true` removed one chat |
| `doctor` | pass | 4 checks, `ok: true` |
| `send_message` dry run | pass | returned `chat_id`, title, `requires` text; no send |
| `send_message` confirm | pass | sent with matching `expected_chat_id`; reply returned |
| `create_chat` dry run | pass | preview only |
| `create_chat` confirm | pass | new chat created, stored, 2 turns |
| `send_message` without `expected_chat_id` | pass | refused (unit test) |
| Wrong `expected_chat_id` | pass | refused before typing (unit test) |

## Write-path safety checks

| Check | Result |
|---|---|
| Own tab only; tab closed in `finally` | verified in code and tests |
| Loaded URL must match resolved chat ID | unit test: wrong chat refuses before typing |
| Existing composer draft | unit test: refused, no Send click |
| Typed text must match exactly | unit test: mismatch refuses before Send |
| New-chat temporary `local-chatgpt:` URL | handled; waits for real UUID |
| Reply completion detection | stop control gone + text stable for 3 polls |
| Timeout behaviour | `unknown`, exit `10`, no retry (unit test) |
| `send_log` contents | chat ID, time, status, error type only |
| Message text in `send_log` | none; text lives in `turns` after refresh |
| Logs with chat text/titles/chat URLs | none found in the current log |
| Credentials accessed | none |

## Privacy and storage

| Check | Result |
|---|---|
| Database mode | `0600` |
| `export-raw` mode | `0600` |
| Log mode | `0600` |
| WAL/SHM modes | `0600` |
| Tracked `.db`/`.log`/`.jsonl` files in git | none |
| Chat content committed | none |

## Packaging and CI

- `uv build` produced `chatgpt_bridge-0.3.0.tar.gz` and a wheel containing
  `001_init.sql`, `002_on_demand.sql`, and `003_send_log.sql`.
- The wheel installed into a fresh Python 3.14 venv; `chatgpt-bridge 0.3.0`
  reports `version`, `status`, and the new `send` / `new` commands.
- CI (`.github/workflows/ci.yml`) runs lint, format, types, tests on Python
  3.12–3.14, and a wheel smoke test.

## Notes for the user

- The acceptance run created **six clearly labelled test conversations** in the
  account. They remain because deletion is deliberately not a feature; remove
  them in the ChatGPT UI if unwanted.
- No real conversation was read into the repository. The live read tests used a
  temporary database and only asserted structure.
- The write path has one unavoidable edge: after a reply timeout the message may
  have been accepted. That is reported as `unknown` and is never retried.

## 0.3.1 — search hint

Verified against the real store (3 chats, 12 messages):

| Case | Command | Result |
|---|---|---|
| No hit, no title match | `search "kubernetes"` | states the term, that 3 chats are stored, that titles are not searched, and how to fetch |
| `--json`, no hit | `search "kubernetes" --json` | stdout `[]`, hint on stderr |
| Hits found | `search "OK"` | 8 hits, no hint |
| MCP, no hit | `search_chats("kubernetes")` | `hits: []`, `hint` populated |
| MCP, hits | `search_chats("OK")` | `hits: 8`, `hint: null` |

Title-match hinting is covered by unit tests (`tests/test_query.py`,
`tests/test_cli.py`, `tests/test_mcp.py`) rather than a live case, because every
stored chat's messages already contain the words in its title.

### CLI verification by the user, 2026-11-01

`doctor`, `chats`, `chats --stored`, `fetch`, `show`, `search`, `send
--dry-run`, `send` with prompt answered `n`, `send --yes`, and `new --yes` all
behaved as documented. `send --yes` appended one exchange (2 → 4 messages) and
`new --yes` created and stored a chat; `status` reported the new `last_send`.
