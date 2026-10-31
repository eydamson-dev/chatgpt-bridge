# Changelog

User-visible changes only. Implementation detail lives in git history,
`PLAN.md`, and `docs/HANDOFF.md`.

## 0.2.0 — on-demand retrieval

Reworked from a mirror model to on-demand fetching. **`sync` is removed.** The
cache now holds only chats you asked for.

### Added

- Public repository under the MIT license.
- `fetch <url|id|prefix|title>` — fetch one conversation and store it.
  `--refresh` refetches and reports whether the content changed.
- `chats` — visible sidebar list for title lookup (read-only, no scrolling);
  `--cached` lists what has been fetched.
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
