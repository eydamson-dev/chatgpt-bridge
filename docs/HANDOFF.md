# Handoff — chatgpt-bridge

**Read this first in a new session.** It reflects the state of the repo right now,
not history. Update it at the end of every milestone: rewrite the "Current state"
and "Next step" sections, and append to "Decisions" and "Gotchas" only when
something durable was learned.

Last updated: 2026-11-01 (0.6.0 background browser)

## What this project is

A CLI and MCP server that pull one ChatGPT conversation at a time out of a
logged-in Chromium browser (CDP) and store it in a local SQLite database for
reading and search. They can also send one explicitly confirmed message to an
existing chat, create one new chat with one message, and create one empty
project with a name.

- **Repository:** <https://github.com/eydamson-dev/chatgpt-bridge> (public, MIT).
  The default and only branch is `main`. CI triggers on `main` and on pull
  requests; `.github/workflows/ci.yml` holds the branch filter.
- Contains code and docs only. Chat data never enters the repository; the
  database is git-ignored.

- **On demand only.** Nothing scans or mirrors the user's chat history.
- **The local store is not a disposable cache.** Chats you have not fetched are
  not stored anywhere locally, so `forget`/`reset` removes them from this tool.
- **Writes are narrow and confirmed.** `send_message`, `create_chat`, and
  `create_project` are the only write paths. No edit, delete, rename, branch
  switching, or bulk sends.
- Full design: `../PLAN.md`. Write decisions:
  `SEND-MESSAGE-DESIGN.md`. Hard rules: `../AGENTS.md`.

## Current state

Version **0.6.0**. Working, verified against a live browser (Brave on
`127.0.0.1:9222`):

- Target resolution by URL, full ID, ID prefix, exact title, unique title
  substring; ambiguity is reported with candidates and no page loads.
- `fetch` retrieves one chat, converts messages to markdown, stores it, and
  records the fetch. A second call is served from the local store without the
  browser.
- `--refresh` refetches and reports `changed: true|false` using a content hash.
- Search over stored messages (FTS5 trigram), with context snippets. An empty
  result explains itself: the hint states that titles are not indexed, and if
  the term matches a stored **title** it names that chat and suggests `show`.
  `--json` keeps stdout a JSON array and sends the hint to stderr; MCP
  `search_chats` returns it as `hint`.
- `forget` removes one chat; `reset` clears the whole local store (both prompt).
- `doctor` checks config, database, CDP reachability, login, and sidebar lookup.
- `projects` lists ChatGPT projects; `--project NAME` scopes `chats`, `fetch`,
  `search`, `send`, and `new`. A chat fetched from a project stores its name and
  `g-p-…` ID (schema v4), so `chats --stored --project` and `search --project`
  work offline.
- `send` appends one confirmed message to an existing chat; `new` creates a chat
  with one message. Both wait for the reply and refresh the local copy.
  `--dry-run` resolves without a page or a send; `--yes` skips the prompt.
- `browser start|stop|restart|status|login` manages an optional Docker-hosted
  Chromium. `[browser] mode = "managed"` points every command and the MCP server
  at it (`127.0.0.1:9223`), with the profile at
  `~/.local/share/chatgpt-bridge/browser`. Verified live: login survived a
  container restart and a profile migration, `doctor`, `chats`, `fetch
  --refresh`, `projects`, and a confirmed `send` all pass through it, and no tab
  appears on the desktop. `new-project` (alias `create-project`) creates one empty project. It sets the
  name only, verifies the field reads back exactly before clicking Create, and
  confirms the project appeared (list row or project URL) before reporting
  success. `--dry-run` and `--yes` behave as on `new`.
- MCP server exposes eleven tools: `status`, `chats`, `fetch_chat`, `get_chat`,
  `search_chats`, `forget_chat`, `doctor`, `send_message`, `create_chat`,
  `create_project`, `projects`. Five of them accept a `project` argument.
  `send_message` is a dry run unless `confirm=true` and `expected_chat_id`
  matches; `create_chat` requires `confirm=true`. Chat text is wrapped as
  untrusted content.
- 311 unit tests, 4 opt-in read live tests, and 2 gated live send tests pass;
  `ruff` and `mypy --strict` are clean on Python 3.12 and 3.14.
- The built wheel includes all three migrations, installs into a clean virtual
  environment, and both entry points work. `.github/workflows/ci.yml` repeats
  lint, format, types, tests (3.12–3.14), and a wheel smoke test.
- A real stdio MCP handshake exercises all nine tools; live `doctor` passes
  against Chrome/Brave.
- Logs, database, `send_log`, and `export-raw` output are user-only (`0600`).
  A failed fetch records metadata only; a send records status and error type,
  never message text.
- Installed on the user's machine as a uv tool from git (`chatgpt-bridge 0.4.0`
  in `~/.local/bin`, usable from any directory), with the MCP server pointed at
  the project venv.
- Full feature evidence: `docs/ACCEPTANCE.md`.

Schema is **version 4**. Migration `002_on_demand.sql` rebuilds the tables and
discards v1 backfill data; `003_send_log.sql` adds metadata for the write path; `004_project.sql` adds
`project`/`project_id` to `chats`. v1, v2, and v3 databases migrate
automatically.

## Repo map

```
src/chatgpt_bridge/
  cli.py            command surface, exit codes, no business logic
  mcp_server.py     nine tools; imports services under aliases
  resolve.py        target -> chat id (URL/ID/prefix/title), pure
  fetch.py          fetch service: stored hit, refresh, save, fetch log
  send.py           explicit write service: send one, create one, send log
  project.py        explicit write service: create one empty project
  managed_browser.py  Docker container for the background browser
  query.py          local-store reads: list, get, search, render, envelope
  store.py          SQLite schema, migrations, FTS, fetch/send logs
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
tests/test_live.py  opt-in read checks (`-m live`)
tests/test_live_send.py  gated create/send check (`-m live_send`, needs env)
.github/workflows/ci.yml   lint, types, tests, wheel smoke test
docs/SEND-MESSAGE-DESIGN.md   write-path decisions, implemented in 0.3.0
docs/ACCEPTANCE.md            sanitized evidence for every feature
```

## Install (no PyPI)

The project is **not published to PyPI** — a deliberate decision, recorded
below. Install from source or from git.

```bash
uv tool install git+https://github.com/eydamson-dev/chatgpt-bridge   # from git
git clone <repo> && cd chatgpt-bridge && uv tool install .           # from a clone
uv tool install --editable .                                         # development
uv tool upgrade --reinstall chatgpt-bridge                           # after pushing
uv tool uninstall chatgpt-bridge                                     # remove
```

Installs two console scripts, `chatgpt-bridge` and `chatgpt-bridge-mcp`, into
`~/.local/bin`, backed by an isolated environment at
`~/.local/share/uv/tools/chatgpt-bridge/`.

**The user's machine currently has two installs, deliberately:**

| Location | Used by | Updates when |
|---|---|---|
| `~/.local/share/uv/tools/chatgpt-bridge/` | `chatgpt-bridge` in the shell | `uv tool upgrade --reinstall` |
| `~/projects/chatgpt-bridge/.venv/` | tests and the MCP server | `uv sync` / `uv run` |

The MCP entry points at the **project venv**, so MCP tracks local changes
without a reinstall, while the shell command tracks pushed commits. They agree
whenever the tree is pushed and synced. If that ever causes confusion, repoint
the MCP command at the tool install instead.

A `git+` install pins the pushed commit, not the working tree.

The MCP client config needs the **absolute** path from `command -v
chatgpt-bridge-mcp`; a bare name fails when the client's `PATH` differs — MCP
clients do not necessarily inherit your shell `PATH`.

`uv run chatgpt-bridge …` only works inside the repository, because `uv run`
needs a `pyproject.toml` in the current directory. From anywhere else, use the
installed command or `.venv/bin/chatgpt-bridge`.

## Agent skill (lives outside the repo)

An agent skill at `~/.config/opencode/skills/chatgpt-bridge/SKILL.md` tells agents
how to use the CLI and MCP safely. It is deliberately **not** in this repo: it is
about how this user works, not about the code.

It is a copy of procedure, so it drifts. **When a command, flag, exit code, or
rule changes, update the skill in the same session.** Check its flags against
`chatgpt-bridge <command> --help`. The skill's last sync was 0.5.0.

The global `~/.config/opencode/AGENTS.md` has a short `ChatGPT Conversations`
section that makes agents load the skill before any ChatGPT action. It is the
reason the skill is used without being named.

## Verify before changing anything

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run pytest -m live          # needs a reachable, logged-in CDP browser
CHATGPT_BRIDGE_ALLOW_SEND=1 uv run pytest -m live_send   # creates and sends
uv build                       # wheel must contain migrations/*.sql
```

Manual check against the real browser (requires a logged-in CDP browser):

```bash
uv run chatgpt-bridge doctor
uv run chatgpt-bridge chats --stored   # what is in the local store
uv run chatgpt-bridge chats            # visible sidebar, read-only
uv run chatgpt-bridge fetch "<title>"  # one chat, stored locally
uv run chatgpt-bridge fetch "<title>"  # served from local store, no page load
uv run chatgpt-bridge send "<title>" "test" --dry-run
uv run chatgpt-bridge send "<title>" "test"            # prompts; writes
uv run chatgpt-bridge new "Start a test chat"          # prompts; writes
```

## Invariants (do not break)

1. Reads by default. Only `send_message`, `create_chat`, and `create_project`
   write, all confirmed, one object per call. No edit, delete, rename, branch
   switching, or bulk. Project creation sets a name and nothing else.
2. No credentials. Never read cookies, tokens, headers, or profile files.
3. Own tabs only, closed in `finally`; never `browser.close()`.
4. Logs never contain chat text, titles, or URLs.
5. Chat text is untrusted data; MCP output wraps it in the envelope.
6. `show`/`search`/`get_chat` never open a page. `chats` may load the sidebar
   for lookup; `fetch`, `send`, `new`, and `new-project` open their own tab.
7. The local SQLite database is the product's storage, not a throwaway cache:
   conversations the user has not fetched exist nowhere locally.
8. Title lookup reads the visible sidebar only, never scrolls.
9. A failed parse stores nothing.
10. Before typing, the loaded chat URL must match the resolved ID and a
    non-matching `expected_chat_id` must abort.
11. An existing composer draft is refused; typed text must match exactly before
    Send is clicked.
12. Never retry after submit. A missing reply is `unknown`, never a silent
    resend.
13. The managed browser's CDP port is published on loopback only. It grants
    control of a logged-in session, so it never binds 0.0.0.0 and never sits
    behind a tunnel.
14. `mode = "managed"` never falls back to another browser. A stopped container
    is an error, not a reason to use a different session.

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
- **2026-11-01 — write path approved and built.** The user authorized testing,
  sending, and creating chats. `send_message` and `create_chat` are the only
  writes; MCP sending needs `confirm=true` plus the dry-run `expected_chat_id`.
  Reply timeout is `unknown`, never an automatic retry.
- **2026-11-01 — new chats are allowed, edits are not.** The approved scope
  covers creating one chat with one message. Rename, delete, branch switching,
  model selection, attachments, and bulk sends remain out of scope.
- **2026-11-01 — send log is metadata only.** Schema v3 records chat ID, time,
  status, and error type. Message text lives only in `turns`, written by the
  normal refresh.
- **2026-11-01 — no PyPI publication.** Install from git or a local clone.
  Rationale: personal tool over a logged-in browser, terms-of-use grey area, and
  a public package implies release and support commitments. `uv tool install
  git+…` covers other machines without it.
- **2026-11-01 — Projects are in scope for 0.4.0, narrowly.** Read: list
  projects, list their chats, search their chat titles. Write: create one chat in
  a project, send one message to a project chat. Creating, renaming, deleting, or
  pinning a project and touching its files or instructions stay out. Search stays
  title-only (option A): no bulk fetching of project content.
- **2026-11-01 — store both project ID and name.** `project_id` (`g-p-<hex>`) is
  authoritative; `project` is the display name and may go stale after a rename.
  Both are treated as personal data and never logged.
- **2026-11-01 — the background browser is a container, not a systemd unit.**
  The user asked for no visible tabs. ChatGPT proposed systemd + Xvfb; a spike
  showed `jlesage/chromium` already does Xvfb + Chromium + a login GUI + CDP
  forwarding, with CDP reachable over ordinary bridge networking. Docker owns
  the lifecycle, so no systemd unit, no host Xvfb, no bundled Chromium. Rejected:
  Steel and Browserless (session-per-request), Neko (no CDP), Scry (2 stars,
  idle), Moli (headless-only engine, no human login).
- **2026-11-01 — no silent browser fallback.** If `managed` is down, fail. The
  alternative risks sending a message through a session the user did not
  confirm.
- **2026-11-01 — project creation is in scope for 0.5.0, name only.** The user
  asked for it after asking why the tool could not create a project. Creation is
  limited to one empty project with a name: the dialog's memory, icon, and
  colour controls are never touched, and rename/delete/pin/files/instructions
  stay out. Verified live by creating `chatgpt-bridge create test`.
- **2026-11-01 — creation is verified, not assumed.** The name must read back
  from the field exactly, and the project must appear (list row or project URL)
  before success is reported. A timeout says "check before retrying" instead of
  retrying, because a silent retry would create a duplicate project.
- **2026-11-01 — empty searches explain themselves.** A hitless `search` says
  that titles are not indexed and how to fetch from the sidebar; if the term
  matches a stored title it points at `show` instead. This keeps the
  browser-free rule intact — the hint is computed from the local store, never
  from the sidebar.

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
- **The send button only exists once the composer has text.** The composer is
  `div[contenteditable="true"][role="textbox"]`; after typing, the control is
  `button[aria-label="Send"]`. Waiting for it before typing finds nothing.
- **A new chat shows a temporary URL.** It becomes
  `/c/local-chatgpt%3A<uuid>` before settling on `/c/<real-id>`; wait for the
  real UUID, not the first `/c/` match.
- **Reply completion is a stop-control + stability check.** While generating,
  the button's aria-label is `Stop`. The reply is complete when that control is
  gone and the last assistant turn's text length is stable for three polls.
- **`send` is not idempotent.** After a reply timeout there is no safe way to
  know whether ChatGPT accepted the message; report `unknown` and stop.
- **Project rows are not links.** In the sidebar and on `/projects`, a project is
  `div[data-project-row]` holding buttons (`Toggle project`,
  `Start new chat in project`). `a[href]`-only extraction cannot see projects,
  which is why project chats were undiscoverable.
- **Project chat links are outside the project row.** They are ordinary anchors
  whose href carries both IDs: `/g/g-p-<project-id>/c/<chat-id>`. Group by the
  href, not by the row element, and de-duplicate by chat ID — each chat yields
  two identical anchors on `/projects`.
- **A project chat URL has two forms.** In the DOM it is
  `/g/g-p-<id>/c/<chat-id>`; after a send the address bar shows the slugged
  `/g/g-p-<id>-<project-slug>/c/<chat-id>`. `chat_id_from_url()` handles both,
  `CHAT_URL_RE` handles neither.
- **A long multi-line message duplicates with `keyboard.insert_text`.** The
  composer re-renders while it is still inserting, so the text lands twice and
  out of order (1972 chars in, 3986 out). Set the message with `fill()` instead;
  it lands in one shot. The verification compares words with whitespace
  collapsed, because the editor reflows paragraph breaks.
- **A running MCP server keeps its old code, including the config schema.** After
  adding `[browser] mode`, a still-running 0.5.x MCP server rejected the key as
  unknown and every tool that reads settings failed with "Error executing tool".
  Restart the MCP server (or the OpenCode backend) after upgrading.
- **A logged-out ChatGPT hides the sidebar instead of removing it.** The nav is
  in the DOM with `display: none`, so waiting for visibility looks like a 30s
  hang and a raw traceback. Wait for it attached, then for visible with a short
  timeout, and raise `LoginRequired` (0.6.0).
- **jlesage/chromium exposes CDP over ordinary bridge networking.** No host
  networking and no socat sidecar are needed, unlike the linuxserver image.
  Publish `127.0.0.1:<port>:9222`; the image forwards internally.
- **The container's Chromium runs with `--no-sandbox`** (fine inside the
  container) and `--user-data-dir=/config/chromium`, which Chrome 136+ requires
  before it will accept `--remote-debugging-port`.
- **The projects page hydrates in two stages.** `div[data-project-row]` is in
  the main area almost immediately, but the sidebar (and with it `Add new
  project`) appears roughly a second later. Wait for the create control, not
  for the row — reading too early looks like a missing button. This is what
  0.5.0 got wrong and 0.5.1 fixed.
- **The sidebar "Add new project" button is covered by an overlay.** A plain
  `page.click('button[aria-label="Add new project"]')` times out: the Projects
  section header and the sidebar scroll container intercept pointer events. The
  button is clicked in-page (`OPEN_PROJECT_DIALOG_JS`) instead.
- **The create dialog has exactly one text field.** `#chatgpt-project-name`
  (name `project-name`, placeholder `Copenhagen Trip`); `Create project` stays
  disabled until it has text. The same dialog also carries a "Default memory"
  toggle and an icon/colour control — never touch them.
- **After Create, the page navigates to the project.** The URL becomes
  `/g/g-p-<id>/project`, which is where `create_project` gets the ID from.
- **`/projects` only lists chats for expanded projects.** Expand the project
  named in the request and diff the link set before and after, which also
  excludes links belonging to other projects.
- **A project chat does not open at `/c/<id>`.** The page loads with no
  conversation, so parsing fails with "message 1 (assistant) is empty". Use
  `/g/<project-id>/c/<chat-id>`. `fetch` and `send` pass the resolved URL when
  the chat has a `project_id`.
- **The projects list renders incrementally.** Reading as soon as the first
  `div[data-project-row]` appears drops the remaining projects; poll until the
  count is stable twice.
- **OpenCode spawns one MCP server per workspace connection.** `directory=~` and
  `directory=<project>` each get their own process; two processes is normal, not
  a leak.
- **Killing an MCP server process breaks that server for the life of the
  backend.** OpenCode logs `mcp transport error … MCP server process exited` and
  never respawns it. The desktop window is a separate systemd scope from the
  backend (`opencode-cli serve --service`), so closing and reopening the window
  does nothing. Stop the backend scope, then relaunch.
- **A project chat's address-bar URL is slugged** (`/g/g-p-<id>-<slug>/c/<chat>`)
  while the DOM link is `/g/g-p-<id>/c/<chat>`. Both parse; only
  `PROJECT_CHAT_URL_RE` accepts them.

## Known limitations

- Page-dependent: a ChatGPT DOM change breaks discovery or parsing. All
  selectors live in `adapters/chatgpt.py`.
- Only the displayed branch is stored; alternative count is best-effort.
- Attachments: names and types only, files not downloaded.
- Archived chats are unreachable.
- Projects can be created with a name, but not renamed, deleted, or pinned, and
  their memory, icon, files, and instructions are unreachable. A project created
  by mistake has to be removed in the ChatGPT UI.
- Linux verified. macOS and Windows are untested.
- Writes are one message per call; no edit, delete, rename, or branch switching.
- A reply timeout leaves the send `unknown`; the tool never retries.
- Automating the web UI may conflict with OpenAI's terms; keep volume low.

## Next step

Nothing is in flight. Milestones 1–17 in `PLAN.md` §16 are done; 0.6.0 is
verified; all unit tests, 4 read live tests, and 2 gated send live tests pass.
`docs/ACCEPTANCE.md` records the 0.3.0 feature run and the 0.3.1/0.4.0
additions. `docs/PROJECTS-PLAN.md` remains the design record for projects —
read it before changing project resolution or the adapter.

Deliberately not built: `fetch a b c` for several targets, edit/delete/rename
and branch switching, project management beyond creation (rename/delete/pin,
memory, icon, files, instructions), and PyPI publication.

The user verified the CLI by hand on 2026-11-01: `doctor`, `chats`, `chats
--stored`, `fetch`, `show`, `search`, dry run, prompt cancel, `send --yes`, and
`new --yes` all behaved as designed.

Remaining, and deliberately not built:

- `fetch a b c` for several targets — **deferred**, not rejected; see Settled.
- Edit, delete, rename, and branch switching (out of scope).
- PyPI publication (rejected; see Decisions).

## Engineering vault

Recorded for durable context rather than implementation detail:

- `Projects/ChatGPT-Bridge.md` — purpose, repo, stack, status, decisions,
  constraints, and a **Traps** section for project-specific non-obvious
  behaviour. Read the Traps section before changing the adapter, store, or tests.
- `Lessons/MCP-Python-SDK-2-Renamed-FastMCP.md`
- `Lessons/FTS5-Trigram-Snippet-Truncates.md`
- `Lessons/Private-Files-Need-Mode-At-Creation.md`
- `Lessons/OpenCode-MCP-Server-Dies-With-Backend.md` — why an MCP server stays
  broken after killing it, and why restarting the window does not help.

Update the project note when a decision changes, not when code changes.

## Open questions for the user

Nothing is open. Stored chats do **not** expire; the user rejected an age-based
purge, so `forget` and `reset` remain the only deletion paths.

Settled:

- **Several targets in one `fetch` is deferred, not rejected** (2026-11-01). The
  user has no current need and a shell loop already covers it:
  `for t in a b c; do chatgpt-bridge fetch "$t"; done`. If it is ever built, do
  it as `fetch --from-file targets.txt` with per-target status and pacing, not
  variadic arguments: variadic targets make bulk fetching look casual and break
  the single-document output shape.

- **No PyPI publication** (2026-11-01). Install from git or a local clone.
  Rationale: it is a personal tool driving a logged-in browser, it sits in a
  terms-of-use grey area, and a public package implies a support and release
  commitment. `uv tool install git+…` already covers multi-machine use.
