-- Schema 002: on-demand retrieval.
--
-- Every cached chat has been fetched explicitly, so the mirror-oriented columns
-- (stale, last_seen_at, sidebar_rank) and the sync run log are gone. Old
-- backfilled data is discarded; the cache only holds what was asked for.

DROP TRIGGER IF EXISTS turns_ai;
DROP TRIGGER IF EXISTS turns_ad;
DROP TRIGGER IF EXISTS turns_au;
DROP TABLE IF EXISTS turns_fts;
DROP TABLE IF EXISTS attachments;
DROP TABLE IF EXISTS turns;
DROP TABLE IF EXISTS sync_runs;
DROP TABLE IF EXISTS chats;

CREATE TABLE chats (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  url            TEXT NOT NULL,
  section        TEXT,
  content_hash   TEXT,
  turn_count     INTEGER,
  fetched_at     TEXT NOT NULL,
  parse_version  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE turns (
  chat_id       TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  position      INTEGER NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  markdown      TEXT NOT NULL,
  text          TEXT NOT NULL,
  alternatives  INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (chat_id, position)
);

CREATE TABLE attachments (
  chat_id   TEXT NOT NULL,
  position  INTEGER NOT NULL,
  idx       INTEGER NOT NULL,
  name      TEXT,
  mime      TEXT,
  kind      TEXT,
  PRIMARY KEY (chat_id, position, idx),
  FOREIGN KEY (chat_id, position) REFERENCES turns(chat_id, position) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE turns_fts USING fts5(
  text,
  content='turns',
  content_rowid='rowid',
  tokenize='trigram'
);

CREATE TRIGGER turns_ai AFTER INSERT ON turns BEGIN
  INSERT INTO turns_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER turns_ad AFTER DELETE ON turns BEGIN
  INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER turns_au AFTER UPDATE ON turns BEGIN
  INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO turns_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE IF NOT EXISTS fetch_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id     TEXT NOT NULL,
  fetched_at  TEXT NOT NULL,
  status      TEXT NOT NULL,
  detail      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  TEXT
);

CREATE INDEX IF NOT EXISTS idx_chats_fetched ON chats(fetched_at);
CREATE INDEX IF NOT EXISTS idx_fetch_log_chat ON fetch_log(chat_id);
