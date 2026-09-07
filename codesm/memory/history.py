"""Local, project-scoped full-text index of codesm's durable session records."""

from contextlib import closing, contextmanager
from datetime import datetime
import json
import hashlib
from pathlib import Path
import re
import sqlite3

from codesm.storage.storage import Storage


def visible_record(record):
    """Keep opaque provider replay data on disk, outside retrieved model context."""
    if isinstance(record, list):
        return [visible_record(item) for item in record]
    if not isinstance(record, dict):
        return record
    result = {key: visible_record(value) for key, value in record.items() if key not in {
        "response_items", "chat_response", "anthropic_content", "thinking_blocks",
        "response_model", "response_provider", "response_prefix",
    }}
    if isinstance(result.get("image"), dict):
        from codesm.storage.images import image_path
        if path := image_path(result["image"]):
            result["image"]["local_path"] = str(path)
    return result


def search_text(value) -> str:
    if isinstance(value, dict):
        return "\n".join(str(key) + "\n" + search_text(item) for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(search_text(item) for item in value)
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return search_text(json.loads(value))
        except (ValueError, RecursionError):
            pass
    return str(value)


class HistoryStore:
    @property
    def path(self) -> Path:
        return Storage.BASE_DIR / "memory" / "history.sqlite3"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            self.path.chmod(0o600)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, message_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, source TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, text TEXT NOT NULL,
                    display TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(session_id, source)
                );
                CREATE INDEX IF NOT EXISTS records_session ON records(session_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
                    text, content=records, content_rowid=id
                );
                CREATE TRIGGER IF NOT EXISTS history_insert AFTER INSERT ON records BEGIN
                    INSERT INTO history_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS history_delete AFTER DELETE ON records BEGIN
                    INSERT INTO history_fts(history_fts, rowid, text) VALUES ('delete', old.id, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS history_update AFTER UPDATE ON records BEGIN
                    INSERT INTO history_fts(history_fts, rowid, text) VALUES ('delete', old.id, old.text);
                    INSERT INTO history_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TABLE IF NOT EXISTS imported (project TEXT PRIMARY KEY);
            """)
            with db:
                yield db

    @staticmethod
    def _put(db, session_id, source, kind, payload):
        db.execute("""INSERT INTO records(session_id, source, kind, payload, text, display, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, source) DO UPDATE SET
            payload=excluded.payload, text=excluded.text, display=excluded.display, updated=excluded.updated""",
            (session_id, source, kind, json.dumps(payload, ensure_ascii=False, default=str),
             search_text(visible_record(payload)),
             json.dumps(visible_record(payload), ensure_ascii=False, default=str, indent=2),
             datetime.now().isoformat()))

    def sync_session(self, data: dict):
        """Index appended messages; JSON remains the complete, atomic session snapshot."""
        with self.connect() as db:
            row = db.execute("SELECT message_count FROM sessions WHERE id=?", (data["id"],)).fetchone()
            start = row[0] if row else 0
            messages = data.get("messages", [])
            if start > len(messages):
                db.execute("DELETE FROM records WHERE session_id=? AND kind='message'", (data["id"],))
                start = 0
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)",
                       (data["id"], str(Path(data["directory"]).resolve()), len(messages)))
            for index, message in enumerate(messages[start:], start):
                self._put(db, data["id"], f"message:{index}", "message", message)
            state = {key: value for key, value in data.items() if key not in {"messages", "context_messages"}}
            self._put(db, data["id"], "state", "state", state)

    def record_event(self, session_id: str, event: dict):
        with self.connect() as db:
            self._put(db, session_id, self._event_source(event), "event", event)

    @staticmethod
    def _event_source(event):
        return "event:" + hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()

    def import_project(self, directory: Path, force: bool = False):
        """Import pre-existing sessions and event logs once, without reading repository files."""
        project = str(Path(directory).resolve())
        with self.connect() as db:
            if not force and db.execute("SELECT 1 FROM imported WHERE project=?", (project,)).fetchone():
                return
        for key in Storage.list(["session"]):
            data = Storage.read(key)
            if not data or str(Path(data.get("directory", ".")).resolve()) != project:
                continue
            self.sync_session(data)
            log = Storage.BASE_DIR / "events" / f"{data['id']}.jsonl"
            if log.is_file():
                with self.connect() as db, log.open() as stream:
                    for index, line in enumerate(stream):
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # A process may have stopped midway through its last log line.
                        self._put(db, data["id"], self._event_source(event), "event", event)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO imported VALUES (?)", (project,))

    def search(self, directory: Path, query: str, session_id: str | None = None, limit: int = 8):
        terms = re.findall(r"\w+", query)[:24]
        if not terms:
            return []
        match = " OR ".join('"' + term + '"' for term in terms)
        with self.connect() as db:
            rows = db.execute("""SELECT r.id, r.session_id, r.source, r.kind,
                snippet(history_fts, 0, '', '', ' … ', 48) AS excerpt
                FROM history_fts JOIN records r ON r.id=history_fts.rowid
                JOIN sessions s ON s.id=r.session_id
                WHERE history_fts MATCH ? AND s.project=? AND (? IS NULL OR s.id=?)
                ORDER BY rank, r.id DESC LIMIT ?""",
                (match, str(Path(directory).resolve()), session_id, session_id, max(1, min(limit, 50))))
            return [dict(row) for row in rows]

    def read(self, directory: Path, record_id: int, offset: int = 0, limit: int = 12000):
        start = max(0, offset)
        size = max(1, min(limit, 24000))
        with self.connect() as db:
            row = db.execute("""SELECT r.id, r.session_id, r.source,
                substr(r.display, ?, ?) AS text, length(r.display) AS size
                FROM records r JOIN sessions s ON s.id=r.session_id
                WHERE r.id=? AND s.project=?""",
                (start + 1, size, record_id, str(Path(directory).resolve()))).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "session_id": row["session_id"], "source": row["source"],
                "text": row["text"], "next_offset": start + size if start + size < row["size"] else None}

    def delete_session(self, session_id: str):
        if not self.path.exists():
            return
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
