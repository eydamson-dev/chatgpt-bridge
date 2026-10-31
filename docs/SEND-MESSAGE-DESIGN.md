# `send_message` — design proposal (not implemented)

**Status:** proposed, awaiting approval. There is no write path in the code today.
`AGENTS.md` says sending is a separately designed feature, so nothing here is
built until the checklist at the end is approved.

This document answers the five questions in `docs/HANDOFF.md` and records the
safety rules that make a write path acceptable at all.

## Scope

**Phase 1 (recommended):** append one user message to one **existing** chat,
submit it, wait for the assistant reply, and return it.

Explicitly out of scope for phase 1:

- creating a new chat (`F-61`);
- renaming, deleting, archiving, or branching;
- changing the model or mode;
- uploading attachments, voice, or images;
- sending to several chats in one call;
- any background or scheduled send.

## The five decisions

### 1. Target

Reuse the existing resolver unchanged: URL, full ID, unique ID prefix, exact
title, or unique title substring. The resolved chat is displayed before sending:

```
Send to "Fedora notes" (6ae5de7d)?
```

Ambiguous targets fail with the candidate list and send nothing. A URL that is
not the loaded chat's URL aborts the send.

**New chat** is a later milestone. It shares the composer but adds creation,
navigation, and a different failure surface.

### 2. Confirmation gate

**CLI**

```
chatgpt-bridge send <target> "<message>"
```

- Shows the chat title, chat ID, and the message (or its first ~200 characters).
- Prompts `Send? [y/N]`; anything but `y`/`yes` cancels with exit 0.
- Refuses when stdin is not a terminal unless `--yes` is passed.
- `--dry-run` resolves the target and prints the preview without opening a page.

**MCP**

```python
send_message(target, message, confirm=False, expected_chat_id=None)
```

- `confirm=False` is a **dry run**: it resolves locally and returns the chat ID,
  title, and message preview. It never opens a page.
- `confirm=true` requires `expected_chat_id` to equal the resolved chat ID. If
  it is missing or different, the call is refused. This prevents an agent from
  sending to whatever a fuzzy title happened to match.
- One message per call. No batching.
- The tool description must say this is the only tool that changes ChatGPT and
  that it requires explicit user approval.

Recommended: keep the MCP tool **out of the default tool set** until the user
enables it per session, so an agent cannot stumble into it.

### 3. Model and mode

Use whatever the chat already uses. Do not touch the model picker or mode
selector in phase 1. Switching models is a separate decision with its own
confirmation, because it silently changes the answer's provenance.

### 4. Branch behaviour

- Append to the **currently displayed branch only**.
- Never click a branch arrow, regenerate, or switch alternatives.
- If the composer already contains text, **refuse** rather than overwrite it.
  A draft is the user's unsaved work.
- If the chat has alternatives, the new turn becomes the newest turn on the
  displayed branch; report the branch count if it changes.

### 5. Failure handling

The send button click is the point of no return. Before it, every failure is
clean (nothing typed, or the tab closed). After it, the tool cannot know whether
ChatGPT accepted the message.

- Poll for a new assistant turn for a bounded timeout (default 90 s).
- If the reply arrives: return it, with `outcome: "sent"`.
- If it does not: return `outcome: "unknown"`, the chat URL, and
  "check the chat before retrying".
- **Never retry automatically.** A retry can post the same message twice.
- Do not store a partial chat; a successful refresh through the existing read
  path is the only way content enters the local store.

## Safety invariants (must hold in the implementation)

1. **Own tab only.** Open the chat in a new tab, close it in `finally`, never
   call `browser.close()`, never navigate the user's tabs.
2. **Verify before typing.** After loading, confirm the page URL contains the
   resolved chat ID. Abort otherwise.
3. **No credentials.** No cookie, token, header, or profile access.
4. **No chat text in logs.** Log chat ID, outcome, timing, and error type only.
5. **Selectors stay in the adapter.** Composer, send button, and draft detection
   live in `adapters/chatgpt.py`, like every other selector.
6. **Drift sends nothing.** If the composer or send button cannot be found
   uniquely, raise `ParseDriftError` before typing.
7. **Read-only stays the default.** `send` is not part of `fetch`, `show`,
   `search`, `chats`, `doctor`, or any MCP read tool.
8. **Polite pacing.** Enforce a minimum gap between sends (default 5 s) and never
   send more than one message per call.

## Data model (optional, schema v3)

If send history is wanted, add metadata only:

```sql
CREATE TABLE send_log (
  id          INTEGER PRIMARY KEY,
  chat_id     TEXT NOT NULL,
  sent_at     TEXT NOT NULL,
  status      TEXT NOT NULL,   -- sent | unknown | refused
  detail      TEXT             -- error type or refusal reason, never chat text
);
```

Do **not** duplicate the message text in `send_log`; after a successful send the
conversation is refreshed through the read path and the turn is stored normally.
If send history is not wanted, phase 1 needs no schema change at all.

## Test plan

Unit, no browser:

- `confirm=false` never constructs a browser session;
- `confirm=true` without `expected_chat_id` is refused;
- wrong `expected_chat_id` is refused;
- ambiguous title is refused;
- existing composer draft is refused;
- URL mismatch after load aborts before typing;
- missing composer/send button raises drift and stores nothing;
- submit timeout returns `unknown` and does not retry;
- success returns the reply and records one `sent` row (if `send_log` is added);
- logs contain no message text, title, or URL.

Live, opt-in only:

- marker `live_send`, skipped unless `CHATGPT_BRIDGE_ALLOW_SEND=1` and CDP is
  reachable;
- uses a dedicated test chat named in the environment, sends one harmless
  message, asserts one new assistant turn;
- never runs in CI.

## Approval checklist

- [ ] Existing chat only for phase 1; new chat later.
- [ ] CLI prompt plus `--yes`; MCP dry run by default, `confirm=true` plus
      `expected_chat_id` to send.
- [ ] Keep the user's current model and mode.
- [ ] Append to the displayed branch; refuse when the composer has a draft.
- [ ] No automatic retry; `unknown` outcome after submit timeout.
- [ ] Whether to add `send_log` (metadata only) in schema v3.
- [ ] Whether the MCP send tool is opt-in per session.
- [ ] Whether to build the live-send test behind `CHATGPT_BRIDGE_ALLOW_SEND=1`.

## Estimated work

Adapter composer methods, a send service, CLI and MCP surfaces, confirmation
tests, optional schema v3, docs, and a gated live test: roughly one to two days,
with the live test run manually against a disposable chat.
