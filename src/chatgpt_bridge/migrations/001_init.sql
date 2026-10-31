-- Schema 001: chats, turns, attachments, FTS, sync runs.
-- FTS uses the trigram tokenizer so code identifiers and non-Latin text match.

CREATE TABLE chats (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  url            TEXT NOT NULL,
  section        TEXT,
  sidebar_rank   INTEGER,
  content_hash   TEXT,
  turn_count     INTEGER,
  fetched_at     TEXT,
  last_seen_at   TEXT NOT NULL,
  stale          INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE sync_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  status       TEXT NOT NULL,
  summary      TEXT
);

CREATE TABLE meta (
  key    TEXT PRIMARY KEY,
  value  TEXT
);

CREATE INDEX idx_chats_section ON chats(section);
CREATE INDEX idx_chats_fetched ON chats(fetched_at);
CREATE INDEX idx_chats_stale ON chats(stale);
