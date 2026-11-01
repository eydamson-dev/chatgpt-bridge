-- Schema 004: ChatGPT Projects.
--
-- A chat fetched from a project records which project it belongs to. The ID is
-- authoritative (it survives a rename); the name is kept for display and for
-- offline filtering. Existing rows stay NULL, meaning "not fetched from a
-- project". Project names are personal data and are never logged.

ALTER TABLE chats ADD COLUMN project TEXT;
ALTER TABLE chats ADD COLUMN project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id);
