"""Per-account SQLite index: message metadata, passages, an FTS5 keyword index and a sqlite-vec vector index."""
import json
import sqlite3
import struct

import sqlite_vec

from .embed import DIM, MODEL

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, thread_id TEXT, received_at TEXT, subject TEXT, sender TEXT,
    recipients TEXT, mailboxes TEXT, keywords TEXT, bulk INTEGER, has_attachment INTEGER, preview TEXT);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS messages_date ON messages(received_at);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY, message_id TEXT NOT NULL, seq INTEGER, text TEXT, source TEXT DEFAULT 'body');
CREATE INDEX IF NOT EXISTS passages_message ON passages(message_id);
-- Every attachment seen, per message. A file sent many times is extracted once (chars > 0 on the first
-- message only); images and other skipped files are recorded with chars 0 so they aren't retried.
CREATE TABLE IF NOT EXISTS attachments (
    blob_id TEXT, message_id TEXT, name TEXT, type TEXT, size INTEGER, chars INTEGER,
    PRIMARY KEY (blob_id, message_id));
CREATE INDEX IF NOT EXISTS attachments_message ON attachments(message_id);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    text, subject, sender, tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS passages_vec USING vec0(embedding float[{DIM}] distance_metric=cosine);
"""


def pack(v):
    return struct.pack(f"{len(v)}f", *v)


class Store:
    def __init__(self, path, readonly=False):
        """readonly: for searching an index another process keeps current (no schema changes, no writes)."""
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False) if readonly \
            else sqlite3.connect(path)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        if readonly:
            if self.get_meta("model") != MODEL:
                raise RuntimeError(f"index {path} was not built with {MODEL}")
            return
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if "source" not in {r[1] for r in self.db.execute("PRAGMA table_info(passages)")}:  # pre-attachment index
            self.db.execute("ALTER TABLE passages ADD COLUMN source TEXT DEFAULT 'body'")
        model = self.get_meta("model")
        if model is None:
            self.set_meta("model", MODEL)
            self.db.commit()
        elif model != MODEL:
            raise SystemExit(f"index was built with {model}, not {MODEL}: rebuild it into a new file")

    def get_meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def known_ids(self):
        return {r[0] for r in self.db.execute("SELECT id FROM messages")}

    def delete_message(self, mid):
        ids = [r[0] for r in self.db.execute("SELECT id FROM passages WHERE message_id=?", (mid,))]
        for pid in ids:
            self.db.execute("DELETE FROM passages_fts WHERE rowid=?", (pid,))
            self.db.execute("DELETE FROM passages_vec WHERE rowid=?", (pid,))
        self.db.execute("DELETE FROM passages WHERE message_id=?", (mid,))
        self.db.execute("DELETE FROM attachments WHERE message_id=?", (mid,))
        self.db.execute("DELETE FROM messages WHERE id=?", (mid,))

    def known_blobs(self):
        """Attachments already extracted somewhere in this account."""
        return {r[0] for r in self.db.execute("SELECT blob_id FROM attachments WHERE chars > 0")}

    def messages_missing_attachments(self):
        """Messages with attachments indexed before attachment support existed."""
        return [r[0] for r in self.db.execute(
            "SELECT id FROM messages WHERE has_attachment AND id NOT IN (SELECT message_id FROM attachments)")]

    def update_flags(self, m):
        """Folder or keyword change: no re-embedding needed."""
        self.db.execute("UPDATE messages SET mailboxes=?, keywords=? WHERE id=?",
                        (json.dumps(sorted(m["mailboxIds"])), json.dumps(sorted(m.get("keywords") or {})), m["id"]))

    def add_message(self, m, sender, recipients, bulk, passages, vectors, attachments=()):
        """passages: [(source, text)], source 'body' or an attachment's file name.
        attachments: [(blob_id, name, type, size, chars)] seen on this message, extracted or not."""
        self.delete_message(m["id"])
        self.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            m["id"], m["threadId"], m["receivedAt"], m.get("subject") or "", sender, recipients,
            json.dumps(sorted(m["mailboxIds"])), json.dumps(sorted(m.get("keywords") or {})),
            int(bulk), int(bool(m.get("hasAttachment"))), m.get("preview") or ""))
        for a in attachments:
            self.db.execute("INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?)", (a[0], m["id"], *a[1:]))
        for seq, ((source, text), vec) in enumerate(zip(passages, vectors)):
            pid = self.db.execute("INSERT INTO passages (message_id, seq, text, source) VALUES (?,?,?,?)",
                                  (m["id"], seq, text, source)).lastrowid
            self.db.execute("INSERT INTO passages_fts (rowid, text, subject, sender) VALUES (?,?,?,?)",
                            (pid, text, m.get("subject") or "", sender))
            self.db.execute("INSERT INTO passages_vec (rowid, embedding) VALUES (?,?)", (pid, pack(vec)))

    def stats(self):
        q = lambda s: self.db.execute(s).fetchone()[0]
        return {"messages": q("SELECT count(*) FROM messages"), "bulk": q("SELECT count(*) FROM messages WHERE bulk"),
                "threads": q("SELECT count(DISTINCT thread_id) FROM messages"),
                "passages": q("SELECT count(*) FROM passages"),
                "attachment_passages": q("SELECT count(*) FROM passages WHERE source != 'body'"),
                "attachments": q("SELECT count(*) FROM attachments"),
                "attachments_with_text": q("SELECT count(*) FROM attachments WHERE chars > 0"),
                "oldest": q("SELECT min(received_at) FROM messages"), "newest": q("SELECT max(received_at) FROM messages"),
                "model": self.get_meta("model"), "email_state": self.get_meta("email_state")}
