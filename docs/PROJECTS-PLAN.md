# ChatGPT Projects — implementation plan

**Status:** **implemented in 0.4.0** (2026-11-01); project creation added in
0.5.0. Milestones 1–7 complete. This document records the design and the
verification that shaped it.
**Approved:** 2026-11-01 by the user.

Adds awareness of ChatGPT Projects: list them, list their chats, search them by
title, store which project a chat belongs to, and create/send inside one.

Project-level writes — creating, renaming, deleting a project, and managing its
files or instructions — were **out of scope** for 0.4.0. Creation was added in
0.5.0 (§12); everything else stays out.

---

## 1. Why this is needed

Today a project chat is reachable only by URL. Two facts, confirmed against the
live UI on 2026-11-01, explain why:

1. **Project entries are not anchors.** The sidebar parser collects `a[href]`;
   project rows are `div[data-project-row]` containing buttons. So
   `list_chats()` never sees them, and a title lookup cannot resolve a project
   chat.
2. **The Projects section is collapsed by default** and holds no anchors even
   when expanded. Expanding it is a click on
   `button[aria-label="Toggle project"]`, not a scroll.

The `/g/<project>/c/<chat>` URL form is already parsed by `chat_id_from_url`, so
`fetch` and `send` by URL may already work. That must be verified, not assumed.

## 2. Findings from the live inspection (2026-11-01)

| Item | Value |
|---|---|
| Projects page | `https://chatgpt.com/projects` |
| Project page | `https://chatgpt.com/g/g-p-<project-id>/project` |
| Project row | `div[data-project-row]`, wrapped in `div[data-project-row-wrapper]` |
| Expand a project | `button[aria-label="Toggle project"]` |
| Start a chat in a project | `button[aria-label="Start new chat in project"]` |
| Project menu (out of scope) | `button[aria-label="Project actions for <name>"]` |
| Add new project | `button[aria-label="Add new project"]` (sidebar only) |
| Pin (out of scope) | `button[aria-label="Pin project"]` |
| Project chat link | `a[href="/g/g-p-<project-id>/c/<chat-id>"]` |

Project IDs have the form `g-p-<32 hex chars>`.

### Verified in milestone 1 (2026-11-01)

One chat was created in `chatgpt-bridge test` through
`Start new chat in project`, and the results were observed directly:

- Clicking `Start new chat in project` navigates to
  `/g/g-p-<id>/project`, which loads the project composer.
- After sending, the **address bar** becomes
  `/g/g-p-<id>-<project-slug>/c/<chat-id>` (the slug is appended to the id).
- The **canonical link in the DOM** is
  `/g/g-p-<id>/c/<chat-id>`, without the slug. Both are equivalent.
- The chat appears as a normal `a[href]` carrying its **title**, so title
  lookup works on `/projects` once the project is expanded.
- The anchor is **not** a descendant of `div[data-project-row]`; the row holds
  only buttons. Grouping therefore comes from the href, not the row.
- Each chat yields two identical anchors on `/projects` (one in the nav, one in
  the row listing), so results must be de-duplicated by chat ID.
- `chat_id_from_url()` already parses both forms. `CHAT_URL_RE` (used to wait
  for a URL after creating a chat) does **not** match either, so it needs an
  additional project form.

## 3. Decisions

- **Search covers project chat titles only** (option A). Live lookup from the
  project page; no bulk fetching of project content.
- **Project membership is stored** (schema v4), so stored chats can be filtered
  by project offline.
- **`--project` is an option on existing commands**, not a parallel set of
  commands.
- One new MCP tool, `projects`, because listing project names has no existing
  home. Everything else gains a `project` parameter.

## 4. Data model — schema v4

`migrations/004_project.sql`:

```sql
ALTER TABLE chats ADD COLUMN project TEXT;
CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project);
```

Milestone 1 found a stable project ID, so both identities are stored:

```sql
ALTER TABLE chats ADD COLUMN project TEXT;       -- human-facing name
ALTER TABLE chats ADD COLUMN project_id TEXT;    -- g-p-<32 hex>, stable
CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id);
```

- `SCHEMA_VERSION` becomes 4.
- Existing rows get `NULL` for both, meaning "not fetched from a project".
- `project_id` is authoritative; `project` is the display name and may go stale
  if the user renames the project. Matching never depends on the name alone.
- Project names are personal data. They are stored locally and shown in output,
  but **never logged**, matching the existing rule for titles and URLs. Project
  IDs are account identifiers and are treated the same way.
- `reset` and `forget` need no change; `chats` is deleted wholesale.

## 5. Components

### 5.1 `adapters/chatgpt.py` — all new selectors live here

New constants:

```python
PROJECTS_URL = "https://chatgpt.com/projects"
PROJECT_ROW = "div[data-project-row]"
PROJECT_TOGGLE = 'button[aria-label="Toggle project"]'
PROJECT_NEW_CHAT = 'button[aria-label="Start new chat in project"]'
PROJECT_CHAT_URL_RE = re.compile(
    r"^https?://(?:www\.)?chatgpt\.com/g/(g-p-[0-9a-f]+)(?:-[^/]*)?/c/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:$|[?#])"
)
```

`PROJECT_CHAT_URL_RE` accepts both the slugged address-bar form and the plain
DOM form, and exposes the project ID and chat ID together.

New in-page script `EXTRACT_PROJECTS_JS`. Because project chats are anchors
**outside** the row element (§2), it returns two lists from `/projects`:

- `projects`: `{name, chat_count_text, has_chats}` per `div[data-project-row]`;
- `links`: `{href, title}` for every `a[href*="/c/"]`, which the caller groups by
  the project ID in the href and de-duplicates by chat ID.

New methods:

| Method | Behaviour |
|---|---|
| `list_projects()` | Open `/projects`, return `ProjectRef(id, name, chat_count)` |
| `list_project_chats(project)` | Open `/projects`, expand that project, collect `/g/<id>/c/<chat>` anchors, de-duplicate by chat ID |
| `create_chat_in_project(project, message)` | Click `Start new chat in project`, type, send, wait for `PROJECT_CHAT_URL_RE` |

`create_chat_in_project` must wait on `PROJECT_CHAT_URL_RE`, not `CHAT_URL_RE`:
the address bar settles on the slugged `/g/…-<slug>/c/…` form, which the plain
pattern rejected in milestone 1.

`create_chat_in_project` reuses the existing `_type_message`, `_click_send`,
`_wait_for_reply`, and draft/typing guards unchanged. Only the entry point
differs.

The project page is capped at `PROJECT_CHAT_LIMIT = 50` entries. When the cap is
hit, return a `truncated` flag so the CLI can say so rather than implying the
list is complete. No scrolling of the sidebar; the project page is read as
presented.

### 5.2 `resolve.py`

- `Resolved` gains `project: str | None = None`.
- New `resolve_with_projects(target, refs)` — same rules, but ambiguity output
  includes the project name, because two projects can each hold a "Plan".
- A target of the form `project:<name>` (or a `--project` filter) scopes the
  candidate list before matching.

### 5.3 `store.py`

- `save_chat(..., project: str | None = None)`.
- `chat_summaries(conn, project: str | None = None)`.
- `search(conn, query, project: str | None = None)`.
- `StoredChat` and `ChatSummary` gain `project`.

### 5.4 `fetch.py`

`fetch_chat(target, refresh=False, project=None, ...)`:

- `project` narrows resolution and is stored on save.
- A chat fetched from a project URL records that project even without the flag.

### 5.5 `query.py`

- `list_cached(project=None)`, `search(..., project=None)`.
- `search_hint` gains a project branch: when a term matches a project chat title
  that is not stored, say which project it is in and how to fetch it.

### 5.6 `send.py`

- `send_message(target, message, project=None, ...)` — scopes resolution and
  refuses when `expected_chat_id` does not match.
- `create_chat(message, project=None, ...)` — routes through
  `create_chat_in_project` when a project is named.

### 5.7 CLI

| Command | Change |
|---|---|
| `projects` | **new** — list projects with chat counts |
| `chats --project NAME` | filter stored chats |
| `fetch <target> --project NAME` | scope and record membership |
| `search <term> --project NAME` | scope to one project |
| `send <target> "<msg>" --project NAME` | scope; prompt shows the project |
| `new "<msg>" --project NAME` | create inside a project |

Exit codes are unchanged. An unknown project is `EXIT_NOT_FOUND` (5); an
ambiguous project name is `EXIT_AMBIGUOUS` (7).

### 5.8 MCP

- New tool `projects()` → names and chat counts.
- `project` parameter added to `chats`, `fetch_chat`, `search_chats`,
  `send_message`, `create_chat`.
- `send_message` keeps the two-step gate: dry run returns the chat ID **and** its
  project; the confirmed call must match both.

Tool count goes from 9 to 10.

## 6. Safety rules (additions to `AGENTS.md`)

13. Project writes are limited to *creating one chat in a project* and *sending
    one message to an existing project chat*. Creating, renaming, deleting a
    project, and changing its files or instructions are out of scope.
14. Project names are never written to logs.
15. Expanding a project is a click in our own tab, never a scroll of the user's
    sidebar, and only for the project named in the request.

## 7. Test plan

**Unit, no browser**

- `EXTRACT_PROJECTS_JS` against a sanitized fixture: names, counts, empty
  project.
- Project-scoped resolution: unique match, ambiguous across projects, missing.
- `save_chat`/`chat_summaries`/`search` with `project`.
- Schema v3 → v4 migration: column added, existing rows `NULL`, backup written.
- `search_hint` project branch.
- `create_chat` routes to the project entry point when `project` is set.
- CLI: `projects`, each `--project` flag, unknown project, ambiguous project.
- MCP: `projects`, `project` parameters, `send_message` project mismatch refusal.

**Live, read-only (`-m live`)**

- `list_projects()` returns at least `chatgpt-bridge test`.
- `list_project_chats("chatgpt-bridge test")` returns an empty list without
  error while the project is empty.

**Live write (`-m live_send`, gated)**

- Create a chat in `chatgpt-bridge test`, verify its URL contains `/g/`, verify
  the stored row records the project, send a follow-up, read it back.

**Manual acceptance** — same shape as `docs/ACCEPTANCE.md`, appended as a 0.4.0
section.

## 8. Milestones

1. ~~**Verify the unknown.**~~ **Done 2026-11-01.** Project ID, project URL,
   chat URL, and anchor shape all confirmed (§2, §11). Two follow-ups carried
   into milestone 2: confirm whether collapsed projects list their chats, and
   add `PROJECT_CHAT_URL_RE` before the first project send.
2. ~~**Read path.**~~ ✅ `list_projects`, `list_project_chats`, CLI `projects`,
   MCP `projects`.
3. ~~**Schema v4 + storage.**~~ ✅ Migration `004_project.sql`,
   `project`/`project_id` on save/summaries/search, `--project` filters.
4. ~~**Fetch.**~~ ✅ `--project` on `fetch`; membership recorded from the URL or
   the flag, and preserved on a later plain re-fetch.
5. ~~**Search titles.**~~ ✅ Project-scoped resolution and a project-aware
   `search_hint`.
6. ~~**Write path.**~~ ✅ `new --project`, `send --project`, MCP equivalents,
   gated live test.
7. ~~**Docs.**~~ ✅ `README.md`, `CHANGELOG.md` 0.4.0, `PLAN.md`,
   `docs/HANDOFF.md`, `docs/ACCEPTANCE.md`, Obsidian note.

Each milestone leaves the suite green and the tree committed.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Project chat anchors differ from plain chats | Milestone 1 verifies before any code |
| No stable project ID in the DOM | Match on name; store name; re-resolve per request |
| Large projects exceed the 50-entry cap | Report `truncated` rather than silently cutting |
| Project instructions change replies | `send` output states which project was used |
| Extra page loads | Keep existing pacing; one project per request; no loops |
| DOM drift | All selectors in `adapters/chatgpt.py`; live test is the alarm |

## 10. Out of scope

- Creating, renaming, deleting, or pinning a project.
- Uploading, listing, or reading project files.
- Editing project instructions.
- Bulk fetching a whole project.
- Moving a chat between projects.

## 11. Project ID — resolved in milestone 1

**Resolved: option (a).** Opening a project yields
`/g/g-p-<id>/project`, and every project chat link carries the same ID:

```
/g/g-p-6ae70048e0988191babe7d3b4bd2619f/c/6ae70265-f2e0-83ec-99a3-f7d1b4e1dd29
```

Consequences for the design:

- `project_id` is captured from `location.href` on the project page, or directly
  from a chat href — no navigation is needed to list a project's chats, because
  the ID is in the link the DOM already exposes.
- The schema stores both ID and name (§4): the ID is authoritative, the name is
  for display.
- `list_project_chats` does not need to click into the project; expanding the
  row is only needed if the page hides chats for collapsed projects. Milestone 2
  must confirm which is true and prefer the cheaper path.

**Answered in milestone 2:** `/projects` only lists chats for expanded
projects, so `list_project_chats` expands the requested project and diffs the
link set before and after, which also excludes chat links from other projects.

**Found during implementation, not predicted by the design:** a project chat
does **not** open at `/c/<chat-id>`; the page loads without a conversation. It
must be opened at `/g/<project-id>/c/<chat-id>`. `fetch` and `send` now pass the
resolved URL when the chat belongs to a project. This was the single largest
bug in the milestone.

### Note

Milestone 1 created one chat in `chatgpt-bridge test`
(titled `Reply Exactly OK`). It is throwaway data in the test project.

## 12. Project creation (0.5.0)

Verified live on 2026-11-01 by creating `chatgpt-bridge create test`.

| Item | Value |
|---|---|
| Control | `button[aria-label="Add new project"]`, sidebar only, also `[data-app-action-sidebar-project-create]` |
| Dialog | `div[role="dialog"]`, heading `Create project` |
| Name field | `#chatgpt-project-name`, `name="project-name"`, placeholder `Copenhagen Trip` |
| Submit | `div[role="dialog"] button:text-is("Create project")`, disabled until the name is non-empty |
| Untouched | `Default memory` toggle, `Change icon and color` control, `Close dialog` |
| After create | the page navigates to `/g/g-p-<id>/project`, which yields the project ID |

**Trap.** A normal Playwright click on the sidebar button times out: the
Projects section header and the sidebar scroll container intercept pointer
events. The button is clicked in-page instead (`OPEN_PROJECT_DIALOG_JS`).

**Trap.** The main project list renders before the sidebar, so waiting on
`div[data-project-row]` and reading immediately finds no create control. Wait
for `button[aria-label="Add new project"]` itself (0.5.1).

**Rules.** Only the name is set. The name must read back exactly before Create
is clicked; a mismatch closes the dialog and creates nothing. After Create, the
project must appear as a row or the URL must match the project page, otherwise
the command fails with "check before retrying" rather than retrying and risking
a duplicate. Renaming, deleting, pinning, memory, icon, files, and instructions
remain unreachable.

**Tests.** Adapter fakes cover success, trimming, empty/overlong names, a name
the field alters, a missing control, and a project that never appears. CLI and
MCP cover the dry run, the missing-terminal refusal, the confirmed path, and a
page failure. No live write test is gated for creation: creating a project on
every CI run would leave clutter in the user's account, so it was verified by
hand once.
