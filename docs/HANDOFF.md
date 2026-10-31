# Handoff — chatgpt-bridge

**Read this first in a new session.** It reflects the state of the repo right now,
not history. Update it at the end of every milestone: rewrite the "Current state"
and "Next step" sections, and append to "Decisions" and "Gotchas" only when
something durable was learned.

Last updated: 2026-10-31 (public repo, MIT license, rewritten author)

## What this project is

A CLI and MCP server that pull one ChatGPT conversation at a time out of a
logged-in Chromium browser (CDP) and store it in a local SQLite database for
reading and search.

- **Repository:** <https://github.com/eydamson-dev/chatgpt-bridge> (public, MIT).
- Contains code and docs only. Chat data never enters the repository; the
  database is git-ignored.

- **On demand only.** Nothing scans or mirrors the user's chat history.
- **The local store is not a disposable cache.** Chats you have not fetched are
  not stored anywhere locally, so `forget`/`reset` removes them from this tool.
- **Read-only.** There is no code path that sends, edits, or deletes in ChatGPT.
- Full design: `../PLAN.md`. Hard rules: `../AGENTS.md`.

## Current state

Version **0.2.1**. Working, verified against a live browser (Brave on
`127.0.0.1:9222`):

- Target resolution by URL, full ID, ID prefix, exact title, unique title
  substring; ambiguity is reported with candidates and no page loads.
- `fetch` retrieves one chat, converts messages to markdown, stores it, and
  records the fetch. A second call is served from the local store without the
  browser.
- `--refresh` refetches and reports `changed: true|false` using a content hash.
- Search over stored messages (FTS5 trigram), with context snippets.
- `forget` removes one chat; `reset` clears the whole local store (both prompt).
- `doctor` checks config, database, CDP reachability, login, and sidebar lookup.
- MCP server exposes `status`, `chats`, `fetch_chat`, `get_chat`,
  `search_chats`, `forget_chat`, `doctor`; chat text is wrapped as untrusted
  content.
- 205 unit tests and 2 opt-in live tests pass on Python 3.12 and 3.14;
  `ruff` and `mypy --strict` are clean.
- The built wheel includes both migrations, installs into a clean virtual
  environment, and both entry points work. `.github/workflows/ci.yml` repeats
  lint, format, types, tests (3.12–3.14), and a wheel smoke test.
- A real stdio MCP handshake lists the seven tools and calls `status`; live
  `doctor` passes against Chrome/Brave with 16 sidebar chats visible.
- Logs, database, and `export-raw` output are user-only (`0600`). A failed
  fetch records metadata only.

Schema is **version 2**. Migration `002_on_demand.sql` rebuilds the tables and
discards v1 backfill data; a v1 database migrates automatically.

## Repo map

```
src/chatgpt_bridge/
  cli.py            command surface, exit codes, no business logic
  mcp_server.py     seven tools; imports the fetch service as fetch_service
  resolve.py        target -> chat id (URL/ID/prefix/title), pure
  fetch.py          fetch service: stored hit, refresh, save, fetch log
  query.py          local-store reads: list, get, search, render, envelope
  store.py          SQLite schema, migrations, FTS, fetch log
  adapters/chatgpt.py   all ChatGPT selectors and labels
  browser.py        CDP connection, own tabs, reachability
  parse.py          HTML -> markdown/text, turn validation (pure)
  config.py         layered settings with source tracking
  paths.py          platform dirs
  logging_setup.py  rotating file log, never stdout
  lock.py           exclusive lock so two fetches cannot overlap
  doctor.py         environment checks
  maintenance.py    export-raw, reset
tests/              unit, fake-source, CLI, MCP (no browser)
tests/test_live.py  opt-in live checks (`-m live`)
.github/workflows/ci.yml   lint, types, tests, wheel smoke test
docs/SEND-MESSAGE-DESIGN.md   proposed write path, not implemented
```

## Verify before changing anything

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run pytest -m live          # needs a reachable, logged-in CDP browser
uv build                       # wheel must contain migrations/*.sql
```

Manual check against the real browser (requires a logged-in CDP browser):

```bash
uv run chatgpt-bridge doctor
uv run chatgpt-bridge chats --stored   # what is in the local store
uv run chatgpt-bridge chats            # visible sidebar, read-only
uv run chatgpt-bridge fetch "<title>"  # one chat, stored locally
uv run chatgpt-bridge fetch "<title>"  # served from local store, no page load
```

## Invariants (do not break)

1. Read-only. No send/create/edit/delete path in ChatGPT.
2. No credentials. Never read cookies, tokens, headers, or profile files.
3. Own tabs only, closed in `finally`; never `browser.close()`.
4. Logs never contain chat text, titles, or URLs.
5. Chat text is untrusted data; MCP output wraps it in the envelope.
6. Reads never trigger a page load; only `fetch_chat` does.
7. The local SQLite database is the product's storage, not a throwaway cache:
   conversations the user has not fetched exist nowhere locally.
8. Title lookup reads the visible sidebar only, never scrolls.
9. A failed parse stores nothing.

## Decisions (durable)

- **2026-10-31 — on-demand, not mirror.** The product fetches a named chat when
  asked. Bulk sync, incremental updates, staleness tracking, and the sync lock
  were designed and then removed at the user's direction; the lock survives only
  to serialise concurrent fetches.
- **2026-10-31 — keep the code, rework it.** The first build implemented the
  mirror model; it was reworked rather than discarded.
- **2026-10-31 — title resolution is sidebar-only.** Discovery alternatives
  (Library, search) were checked and showed no extra chats; the sidebar is the
  lookup source.
- **2026-10-31 — schema v2 discards v1 data.** The v1 database held a backfill
  the user did not want. Migration drops it.
- **2026-10-31 — public repository under MIT.** The repository holds code, tests
  and docs only; no chat content, no fixtures from real conversations. Sample
  titles in tests were neutralized before publishing.
- **2026-10-31 — commit identity.** History was rewritten to
  `eydamson-dev <eydamson-dev@users.noreply.github.com>` and force-pushed, before
  the repository was shared.
- **2026-10-31 — terminology.** "Cache" is used only for internal identifiers.
  User-facing docs and output say "local store" / "stored", because the database
  is real storage, not a disposable copy.
- **2026-10-31 — markdown plus plain text per message.** Markdown keeps code
  fences and tables readable; plain text drives search.
- **2026-10-31 — 0.2.1 hardening.** Private files are created `0600` rather than
  fixed afterwards. All-hex words fall back to title matching when no ID
  matches. Mixed short search terms use a literal `LIKE` scan. Failed fetches
  record the exception type only.
- **2026-10-31 — no automatic expiry.** Stored chats never age out; `forget` and
  `reset` remain the only deletion paths.
- **2026-10-31 — `send_message` is still unbuilt.** The write path is proposed in
  `docs/SEND-MESSAGE-DESIGN.md` and needs the approval checklist before any code
  is added.

## Gotchas discovered

- **`mcp` 2.x**: `FastMCP` was renamed to `MCPServer`
  (`mcp.server.mcpserver`). Pin `mcp>=2`.
- **Name shadowing**: an MCP tool named `fetch_chat` shadowed the imported
  service of the same name. The service is imported as `fetch_service`.
- **FTS5 trigram tokenizer**: `snippet()` counts trigrams as tokens, so its
  output is truncated to a few characters. Snippets are computed in Python
  (`store._snippet`) instead.
- **Migration idempotence**: `002` must use `IF NOT EXISTS` for tables that
  `001` already created (`meta`, `fetch_log`), or a downgraded `user_version`
  breaks re-migration.
- **Sidebar hydration**: `nav[aria-label="Chat history"]` exists before its chat
  links. Wait for `a[href^="/c/"]` too, or the listing comes back empty.
- **No per-message timestamps** in the page, and no stable data attributes for
  turn boundaries; `h4[data-conversation-role]` plus its next sibling is the
  contract.
- **Code block language** comes from the `div[data-code-block]` header label
  (`Bash` → ```` ```bash ````), not from a class on `code`.
- **Project chats** are not listed as sidebar links while collapsed; they are
  outside discovery scope.
- Two CDP clients can attach at once (this app and the chrome-devtools MCP), but
  avoid overlapping live runs while debugging.
- **Log files are world-readable by default.** `logging.handlers` creates them
  with the process umask, so `setup_logging` must `chmod(0o600)` after the
  handler opens the file. Same for exports: open with `os.open(..., 0o600)`, not
  `Path.open`.
- **All-hex titles look like ID prefixes.** `decade`, `facade`, and `accede` are
  valid 6+ hex strings, so resolution must fall through to title matching when
  no ID or prefix matches. The final error should still say "not in the visible
  chat list" when the target looked like an ID.
- **FTS5 trigram cannot match terms shorter than 3 characters.** A mixed query
  such as `go store` needs the whole query to use the `LIKE` fallback, not just
  queries shorter than 3 characters overall.
- **`LIKE` treats `%` and `_` as wildcards.** Escape user text and add
  `ESCAPE '\'`; otherwise a title or search term containing `%` matches
  everything.
- **The wheel must contain `migrations/*.sql`.** They are package data read at
  runtime; `uv build` + install into a clean venv is the check.

## Known limitations

- Page-dependent: a ChatGPT DOM change breaks discovery or parsing. All
  selectors live in `adapters/chatgpt.py`.
- Only the displayed branch is stored; alternative count is best-effort.
- Attachments: names and types only, files not downloaded.
- Archived chats are unreachable; project chats are not discoverable.
- Linux verified. macOS and Windows are untested.
- Automating the web UI may conflict with OpenAI's terms; keep volume low.

## Next step

All twelve read-only milestones in `PLAN.md` §16 are done, 0.2.1 is verified,
and 205 unit tests plus 2 live tests pass. Nothing is in flight.

**Blocked on the user: approve the `send_message` design.** The proposal is in
`docs/SEND-MESSAGE-DESIGN.md`; no write path exists in the code. The checklist
needs a yes/no on:

1. Existing chat only in phase 1 (new chat later).
2. CLI prompt plus `--yes`; MCP dry run by default, `confirm=true` plus
   `expected_chat_id` to actually send.
3. Keep the chat's current model and mode; no switching.
4. Append to the displayed branch only; refuse when the composer already has a
   draft.
5. No automatic retry after submit; return `unknown` if the reply does not
   arrive.
6. Optional: metadata-only `send_log` in schema v3, an opt-in MCP send tool, and
   a live-send test gated by `CHATGPT_BRIDGE_ALLOW_SEND=1`.

Until that approval, keep the write path closed. A safe alternative is the
remaining open question below: `fetch a b c` for several targets in one call.

## Engineering vault

Recorded (2026-10-31), for durable context rather than implementation detail:

- `Projects/ChatGPT-Bridge.md` — purpose, repo, stack, status, decisions,
  constraints, and a **Traps** section for project-specific non-obvious
  behaviour. Read the Traps section before changing the adapter, store, or tests.
- `Lessons/MCP-Python-SDK-2-Renamed-FastMCP.md`
- `Lessons/FTS5-Trigram-Snippet-Truncates.md`
- `Lessons/Private-Files-Need-Mode-At-Creation.md`

Update the project note when a decision changes, not when code changes.

## Open questions for the user

- Should `fetch` accept several targets at once (`fetch a b c`)?
- Should the package be published to PyPI, or stay install-from-source only?
- Stored chats do **not** expire; the user rejected an age-based purge. Keep
  `forget` and `reset` as the only deletion paths unless that changes.
