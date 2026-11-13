"""A transactional outbox: a retry always sends the exact same batch bytes."""
import gzip
import json
import sqlite3
import struct
import time
import uuid
import zlib
from pathlib import Path

from .compact_wire import MAX_FRAMES, MAX_RAW_BYTES, pack
from .decoder import decode
from .transport import canonical_server


MAX_BATCH_FRAMES = 240
MAX_BATCH_JSON_BYTES = 15 * 1024 * 1024
MAX_BATCH_GZIP_BYTES = 8 * 1024 * 1024
RAW_FRAME = struct.Struct("<4sI")
RAW_MAGIC = b"AMSR"


def read_raw_frame(payload):
    _, size = RAW_FRAME.unpack_from(payload)
    offset = RAW_FRAME.size + size
    return json.loads(payload[RAW_FRAME.size:offset]), zlib.decompress(payload[offset:])


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
        self._append(event_code, run_id, gzip.compress(encode(frame), mtime=0))

    def append_raw(self, event_code, run_id, metadata, raw):
        header = encode(metadata)
        self._append(event_code, run_id, RAW_FRAME.pack(RAW_MAGIC, len(header)) + header + zlib.compress(raw, level=1))

    def _append(self, event_code, run_id, payload):
        with self.db:
            self.db.execute("INSERT INTO frames(event_code,run_id,payload) VALUES(?,?,?)",
                            (event_code, run_id, payload))

    def pending(self):
        return self.db.execute("SELECT count(*) FROM frames").fetchone()[0]

    def size_bytes(self):
        # SQLite reuses freed pages; file length alone would pause capture forever
        # after a large backlog had been acknowledged.
        return int(self.db.execute("SELECT value FROM metadata WHERE key='queued_bytes'").fetchone()[0]) + self.db.execute("SELECT coalesce(sum(length(payload)),0) FROM outbox").fetchone()[0]

    def batch(self, recorder_id, limit=None, *, compact=False, compact_limit=MAX_FRAMES):
        row = self.db.execute("SELECT id,payload FROM outbox LIMIT 1").fetchone()
        if row:
            return row
        gzip_limit = MAX_BATCH_FRAMES if limit is None else limit
        fetch_limit = gzip_limit
        if compact:
            fetch_limit = min(compact_limit, MAX_FRAMES if limit is None else min(limit, MAX_FRAMES))
        rows = self.db.execute("SELECT id,event_code,run_id,payload FROM frames ORDER BY id LIMIT ?", (fetch_limit,)).fetchall()
        if not rows:
            return None
        # Keep each batch within one event and process run. Old backlog keeps its original destination.
        event, run = rows[0][1:3]
        batch_id = str(uuid.uuid4())
        metadata = {"schema_version": 1, "batch_id": batch_id, "recorder_id": recorder_id,
                    "event_code": event, "run_id": run, "created_at_ms": int(time.time() * 1000)}
        if compact:
            packed = self.compact_batch(rows, metadata)
            if packed:
                payload, last_frame = packed
                with self.db:
                    self.db.execute("INSERT INTO outbox VALUES(?,?,?)", (batch_id, payload, last_frame))
                return batch_id, payload
        prefix = encode(metadata)[:-1] + b',"frames":['
        suffix = b']}'
        frames = []
        size = len(prefix) + len(suffix)
        for row in rows[:gzip_limit]:
            if row[1:3] != (event, run):
                break
            stored = row[3]
            if stored.startswith(RAW_MAGIC):
                metadata, raw = read_raw_frame(stored)
                frame = decode(raw, metadata.pop("driver_name", None))
                frame.update(metadata, source_kind="shared_memory")
                body = encode(frame)
            else:
                body = gzip.decompress(stored)
            frame_size = len(body) + bool(frames)
            if frames and size + frame_size > MAX_BATCH_JSON_BYTES:
                break
            frames.append(body)
            size += len(body) + (len(frames) > 1)
        payload = gzip.compress(prefix + b','.join(frames) + suffix, compresslevel=6, mtime=0)
        while len(payload) > MAX_BATCH_GZIP_BYTES and len(frames) > 1:
            frames = frames[:max(1,len(frames)//2)]
            payload = gzip.compress(prefix + b','.join(frames) + suffix, compresslevel=6, mtime=0)
        with self.db:
            self.db.execute("INSERT INTO outbox VALUES(?,?,?)", (batch_id, payload, rows[len(frames)-1][0]))
        return batch_id, payload

    def compact_batch(self, rows, header):
        records = []
        event, run = rows[0][1:3]
        for _, row_event, row_run, body in rows:
            if (row_event, row_run) != (event, run):
                break
            # Old backlog and UDP archives remain in their original format.
            if not body.startswith(RAW_MAGIC):
                break
            extras, raw = read_raw_frame(body)
            if not 1 <= len(raw) <= MAX_RAW_BYTES or records and len(raw) != len(records[0][1]):
                break
            records.append((extras, raw))
        while records:
            try:
                return pack(dict(header, schema_version=2), records), rows[len(records) - 1][0]
            except (ValueError, OverflowError):
                records = records[:len(records) // 2]
        return None

    def acknowledge(self, batch_id):
        with self.db:
            row = self.db.execute("SELECT last_frame FROM outbox WHERE id=?", (batch_id,)).fetchone()
            if not row:
                raise ValueError("Unknown acknowledgement")
            self.db.execute("DELETE FROM frames WHERE id<=?", row)
            self.db.execute("DELETE FROM outbox WHERE id=?", (batch_id,))

    def close(self):
        self.db.close()
