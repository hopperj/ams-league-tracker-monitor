import gzip
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from ams_recorder.compact_wire import MAGIC, pack, unpack
from ams_recorder.decoder import decode
from ams_recorder.demo import snapshot
from ams_recorder.spool import Spool


class CompactCaptureTest(unittest.TestCase):
    def test_outbox_retry_preserves_wire_bytes_and_unacknowledged_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "spool.sqlite3"
            recorder, run = str(uuid.uuid4()), str(uuid.uuid4())
            spool = Spool(path)
            originals = [snapshot(i / 60) for i in range(20)]
            for i, raw in enumerate(originals):
                spool.append_raw("AUTO", run, {"sequence": i, "captured_at_ms": 1000 + i * 17}, raw)
            first = spool.batch(recorder, compact=True)
            self.assertTrue(first[1].startswith(MAGIC))
            header, records = unpack(first[1])
            self.assertEqual(header["batch_id"], first[0])
            self.assertEqual([meta["sequence"] for meta, _ in records], list(range(20)))
            self.assertEqual([raw for _, raw in records], originals)
            spool.append_raw("AUTO", run, {"sequence": 20}, originals[-1])
            spool.close()

            spool = Spool(path)
            self.assertEqual(spool.batch(recorder, compact=False), first)
            spool.acknowledge(first[0])
            self.assertEqual(spool.pending(), 1)
            ident, payload = spool.batch(recorder, compact=False)
            frame = json.loads(gzip.decompress(payload))["frames"][0]
            self.assertEqual(frame, dict(decode(originals[-1]), sequence=20, source_kind="shared_memory"))
            spool.acknowledge(ident)
            self.assertEqual(spool.pending(), 0)
            spool.close()

    def test_rejects_corrupt_or_mismatched_records(self):
        header = dict(schema_version=2, batch_id="id", recorder_id="rec", run_id="run", event_code="AUTO")
        with self.assertRaises(ValueError):
            pack(header, [])
        with self.assertRaises(ValueError):
            pack(header, [({}, b"short"), ({}, b"longer")])
        payload = pack(header, [({}, b"frame")])
        with self.assertRaises(ValueError):
            unpack(payload[:-1])
