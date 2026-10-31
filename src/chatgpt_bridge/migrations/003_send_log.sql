-- Schema 003: metadata for the explicit write path.
--
-- No message text is stored here. A successful send refreshes the chat through
-- the normal read path, so the turn lives in `turns` like any fetched message.

CREATE TABLE IF NOT EXISTS send_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id  TEXT NOT NULL,
  sent_at  TEXT NOT NULL,
  status   TEXT NOT NULL CHECK (status IN ('sent', 'unknown')),
  detail   TEXT
);

CREATE INDEX IF NOT EXISTS idx_send_log_chat ON send_log(chat_id);
