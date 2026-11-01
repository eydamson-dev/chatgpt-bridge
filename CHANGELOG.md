# Changelog

User-visible changes only. Implementation detail lives in git history,
`PLAN.md`, and `docs/HANDOFF.md`.

## 0.6.0 — background browser

ChatGPT can now run in a managed container instead of a tab in your everyday
browser.

### Added

- `chatgpt-bridge browser start | stop | restart | status | login`.
- `[browser] mode = "external" | "managed"` and `managed_*` settings. Default
  stays `external`, so nothing changes until you opt in.
- MCP and CLI share the setting; no new MCP tool.

### Behaviour

- The managed browser is a Chromium container (`jlesage/chromium`) with CDP on
  `127.0.0.1:9223` and a web GUI on `127.0.0.1:5801` for the one-time login.
  Both ports are bound to loopback only.
- The profile is `~/.local/share/chatgpt-bridge/browser` (mode `0700`), mounted
  into the container. The ChatGPT session survives restarts; the tool never
  reads the profile.
- `mode = "managed"` never falls back to another browser. If the container is
  down, commands fail with exit `3`.
- Docker is only required for `managed` mode.

### Fixed

- A logged-out browser now reports "ChatGPT is not logged in in this browser"
  instead of hanging for 30 seconds and printing a traceback. Logged out,
  ChatGPT leaves the chat-history sidebar in the DOM but hidden.

## 0.5.2 — long messages

### Fixed

- Sending a long multi-line message could duplicate and reorder its text: the
  composer re-renders while `keyboard.insert_text` is still typing, and the
  copied text landed twice. The message is now set with `fill()` in one step.
- The exact-match check now compares words with whitespace collapsed, because
  the editor reflows paragraph breaks. A duplicated or altered message is still
  refused before Send.

## 0.5.1 — creation wait

Fixes `new-project` failing on a fresh page.

### Fixed

- The `/projects` main list renders before the sidebar, and `Add new project`
  lives only in the sidebar. Creation now waits for that control instead of
  reading the page immediately, which failed with "the 'Add new project' control
  is not on the projects page" on a cold load.

## 0.5.0 — create a project

Projects can now be created from the tool, not only used.

### Added

- `chatgpt-bridge new-project "Name"` (alias `create-project`) creates one empty
  ChatGPT project. It prompts, supports `--dry-run`, and needs `--yes` when
  stdin is not a terminal.
- MCP `create_project(name, confirm=False)`: a dry run unless `confirm=true`.

### Behaviour

- Only the name is set. The dialog's memory, icon, and colour controls are left
  untouched, and no instructions or files are added.
- The name is read back from the field before Create is clicked, and the project
  must appear in the list (or the page must move to its URL) before success is
  reported. Otherwise the command says to check the page first, so a retry does
  not create a duplicate.
- Project names are never written to logs.

### Not supported

- Renaming, deleting, or pinning a project, and changing its memory, icon,
  files, or instructions.

## 0.4.0 — ChatGPT Projects

Reads and writes now understand ChatGPT Projects. Chats fetched from a project
record which project they belong to, so you can list and search them by project
without opening the browser.

### Added

- `chatgpt-bridge projects` — list projects by name.
- `--project NAME` on `chats`, `fetch`, `search`, `send`, and `new`.
- MCP `projects()` and a `project` parameter on `chats`, `fetch_chat`,
  `search_chats`, `send_message`, and `create_chat`.
- Schema v4: `chats.project` (display name) and `chats.project_id`
  (`g-p-<hex>`, authoritative) plus an index. A re-fetch without `--project`
  keeps the membership.
- `new --project` creates the chat inside the project, which therefore uses the
  project's instructions and files.

### Behaviour

- `chats --stored --project NAME` filters locally, with no browser.
- `search --project NAME` is scoped, and its hint names the project.
- `fetch --project NAME` resolves inside the project and stores the membership.
- `send --project NAME` shows `project / title` in the confirmation prompt.
- `projects` reads names only; a project's chats are listed with
  `chats --project NAME`.

### Fixed

- A project chat does not load at `/c/<id>`; it needs
  `/g/<project>/c/<chat>`. `fetch` and `send` now open that URL, which is what
  made project chats fail with "could not parse the page".
- The projects list renders incrementally, so reading it as soon as the first row
  appeared dropped the rest.

## 0.3.1 — clearer empty search results

A search that finds nothing now explains why instead of printing a bare
"no matches". Titles are not indexed, so searching for a chat's name used to
look like a broken search rather than a missing fetch.

### Changed

- `search` with no hits reports the term, how many chats are stored, that chat
  titles are not searched, and how to fetch a chat from the sidebar.
- If the term matches a stored chat's **title**, the hint points at that chat
  and suggests `show` instead.
- `search --json` keeps stdout a valid JSON array and writes the hint to
  stderr, so scripts are unaffected.
- MCP `search_chats` returns the same text in a new `hint` field when `hits` is
  empty, and `null` when it finds something.

### Documentation

- `README.md` now has full install instructions: from git, from a clone, an
  editable development install, running without installing, verifying,
  upgrading, removing, and registering the MCP server. The project is
  deliberately **not published to PyPI**.

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
