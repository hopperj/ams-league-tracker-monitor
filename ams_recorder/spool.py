"""A transactional outbox: a retry always sends the exact same batch bytes."""
import gzip
import json
import sqlite3
import time
import uuid
from pathlib import Path

from .transport import canonical_server


def encode(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


class Spool:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS frames(id INTEGER PRIMARY KEY, event_code TEXT NOT NULL,
            run_id TEXT NOT NULL, payload BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, payload BLOB NOT NULL, last_frame INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          INSERT OR IGNORE INTO metadata(key,value) SELECT 'queued_bytes',coalesce(sum(length(payload)),0) FROM frames;
          CREATE TRIGGER IF NOT EXISTS frame_bytes_insert AFTER INSERT ON frames BEGIN
            UPDATE metadata SET value=CAST(value AS INTEGER)+length(NEW.payload) WHERE key='queued_bytes';
          END;
          CREATE TRIGGER IF NOT EXISTS frame_bytes_delete AFTER DELETE ON frames BEGIN
            UPDATE metadata SET value=CAST(value AS INTEGER)-length(OLD.payload) WHERE key='queued_bytes';
          END;
        """)

    def check_destination(self, server, recorder_id):
        destination = canonical_server(server) + "|" + recorder_id
        row = self.db.execute("SELECT value FROM metadata WHERE key='destination'").fetchone()
        if row:
            saved_server, separator, saved_recorder = row[0].partition('|')
            if not separator or canonical_server(saved_server) + '|' + saved_recorder != destination:
                raise ValueError("This spool belongs to another server/recorder. Use a different --spool directory.")
        return destination

    def bind(self, server, recorder_id):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            destination = self.check_destination(server, recorder_id)
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('destination',?)", (destination,))

    def append(self, event_code, run_id, frame):
        with self.db:
            self.db.execute("INSERT INTO frames(event_code,run_id,payload) VALUES(?,?,?)",
                            (event_code, run_id, gzip.compress(encode(frame), mtime=0)))

    def pending(self):
        return self.db.execute("SELECT count(*) FROM frames").fetchone()[0]

    def size_bytes(self):
        # SQLite reuses freed pages; file length alone would pause capture forever
        # after a large backlog had been acknowledged.
        return int(self.db.execute("SELECT value FROM metadata WHERE key='queued_bytes'").fetchone()[0]) + self.db.execute("SELECT coalesce(sum(length(payload)),0) FROM outbox").fetchone()[0]

    def batch(self, recorder_id, limit=120):
        row = self.db.execute("SELECT id,payload FROM outbox LIMIT 1").fetchone()
        if row:
            return row
        rows = self.db.execute("SELECT id,event_code,run_id,payload FROM frames ORDER BY id LIMIT ?", (limit,)).fetchall()
        if not rows:
            return None
        # Keep each batch within one event and process run. Old backlog keeps its original destination.
        event, run = rows[0][1:3]
        selected, frames, size = [], [], 0
        for row in rows:
            if row[1:3] != (event, run):
                break
            body = gzip.decompress(row[3])
            if selected and size + len(body) > 12 * 1024 * 1024:
                break
            selected.append(row[0])
            frames.append(json.loads(body))
            size += len(body)
        batch_id = str(uuid.uuid4())
        body = {"schema_version": 1, "batch_id": batch_id, "recorder_id": recorder_id,
                "event_code": event, "run_id": run, "created_at_ms": int(time.time() * 1000), "frames": frames}
        payload = gzip.compress(encode(body), compresslevel=6, mtime=0)
        while len(payload) > 8 * 1024 * 1024 and len(frames) > 1:
            frames = frames[:max(1,len(frames)//2)]
            selected = selected[:len(frames)]
            body["frames"] = frames
            payload = gzip.compress(encode(body), compresslevel=6, mtime=0)
        with self.db:
            self.db.execute("INSERT INTO outbox VALUES(?,?,?)", (batch_id, payload, selected[-1]))
        return batch_id, payload

    def acknowledge(self, batch_id):
        with self.db:
            row = self.db.execute("SELECT last_frame FROM outbox WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise ValueError("Unknown acknowledgement")
            self.db.execute("DELETE FROM frames WHERE id<=?", row)
            self.db.execute("DELETE FROM outbox WHERE id=?", (batch_id,))

    def close(self):
        self.db.close()
