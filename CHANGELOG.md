# Changelog

User-visible changes only. Implementation detail lives in git history,
`PLAN.md`, and `docs/HANDOFF.md`.

## 0.3.0 — explicit write path

Adds the only two write operations, both confirmed and one message per call:
send to an existing chat, or create a new chat with one message. Everything
else remains read-only.

### Added

- `chatgpt-bridge send <target> "<message>"` — resolve a chat, show a
  confirmation prompt, send, wait for the reply, and store the refreshed
  conversation. `--dry-run` resolves without sending; `--yes` skips the prompt.
- `chatgpt-bridge new "<message>"` (alias `create-chat`) — create a chat with
  one message and store it.
- MCP `send_message(target, message, confirm, expected_chat_id)` — dry run by
  default; sending requires `confirm=true` and the `expected_chat_id` returned
  by the dry run.
- MCP `create_chat(message, confirm)` — dry run by default; sending requires
  `confirm=true`.
- Schema v3 `send_log`: chat ID, time, and `sent`/`unknown` status. No message
  text is stored there.
- `status` reports the last send alongside the last fetch.

### Safety behaviour

- A new tab is used and closed in `finally`; the user's tabs are never touched.
- The loaded chat URL must match the resolved ID before typing.
- An existing composer draft is refused, never overwritten.
- The typed text must match the requested message exactly before Send is
  clicked.
- A reply that does not finish in time returns `unknown` (exit code 10) and is
  never retried automatically.
- `send_message` refuses if `expected_chat_id` does not match the resolved chat.
- A successful send refreshes the local store, so `show`, `search`, and
  `get_chat` work immediately.

### Testing

- `CHATGPT_BRIDGE_ALLOW_SEND=1 uv run pytest -m live_send` runs one real
  create + send + read-back test. It is disabled by default and never runs in
  CI. The normal `live` tests remain read-only.

## 0.2.1 — hardening

Fixes found while verifying the 0.2.0 release end to end. No behaviour changes
to fetching or reading.

### Fixed

- Logs and `export-raw` files are created user-only (`0600`), including after a
  log rotation. An existing log file is tightened the next time the app starts.
- A title made only of hex characters, such as `decade`, was mistaken for an ID
  prefix and could not be resolved. It now falls back to title matching.
- Search terms shorter than three characters were ignored unless the whole
  query was short. Mixed queries such as `go store` now fall back to a literal
  scan.
- `find_by_title` treated `%` and `_` as SQL wildcards instead of literal
  characters.
- A failed fetch now records a `failed` row in the fetch log, with the exception
  type only — never the message, URL, or title.
- The default pacing values are now `1.0`–`2.0` s, matching the documented
  configuration.
- The lock error says a fetch is running, not a removed `sync`.
- `reset --json` returns a JSON result.
- An unusable database path now reports a store error instead of a traceback.
- Repeated logging setup closes the previous file handler instead of leaking it.
- User-facing messages say "stored chats", not "cached chats".

### Changed

- CI runs lint, format, type checks, tests on Python 3.12–3.14, and a wheel
  install smoke test.
- `docs/SEND-MESSAGE-DESIGN.md` proposes the write path. It is **not
  implemented** and needs approval.

## 0.2.0 — on-demand retrieval

Reworked from a mirror model to on-demand fetching. **`sync` is removed.** The
cache now holds only chats you asked for.

### Added

- Public repository under the MIT license.
- `fetch <url|id|prefix|title>` — fetch one conversation and store it.
  `--refresh` refetches and reports whether the content changed.
- `chats` — visible sidebar list for title lookup (read-only, no scrolling);
  `--stored` lists what has been fetched.
- `forget <target>` — remove one chat from the cache (prompts; `--yes`).
- Target resolution by URL, full ID, unique ID prefix, exact title, or unique
  title substring. Ambiguous matches list candidates and fetch nothing.
- MCP `fetch_chat`, `get_chat`, `chats`, `search_chats`, `forget_chat`, `status`,
  `doctor`. Reads never touch the browser; only `fetch_chat` does.
- Untrusted-content envelope on every MCP tool returning chat text.

### Changed

- `show` and `search` read the cache only, and their errors tell you to run
  `fetch` when a chat is missing.
- Search reports how many chats are cached, so an empty result is not mistaken
  for "not in my history".
- `doctor` no longer needs a fetch: it checks config, database, CDP, login, and
  sidebar lookup.

### Removed

- `sync`, `sync --full`, incremental updates, staleness tracking, and the
  sidebar backfill. Schema migration 002 discards previously backfilled data.

## 0.1.0

Scaffold: CLI and MCP entry points, layered configuration, logging, database
schema, parser, adapter, browser layer. Never released.
