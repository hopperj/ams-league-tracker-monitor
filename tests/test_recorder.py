import base64
import ctypes as C
import gzip
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import uuid
from unittest.mock import patch

from ams_recorder.decoder import decode
from ams_recorder.demo import snapshot
from ams_recorder.schema import SharedMemory, ParticipantInfo, STRUCT_BYTES, SEQUENCE_OFFSET
from ams_recorder.spool import MAX_BATCH_FRAMES, Spool
from ams_recorder.transport import Transport
from ams_recorder.udp import packet_frame


class DecoderTest(unittest.TestCase):
    def test_abi_matches_sdk_native_layout(self):
        # Independently cross-checked with a C++ compilation of the reference SDK.
        self.assertEqual(STRUCT_BYTES,20700)
        self.assertEqual(SEQUENCE_OFFSET,7320)
        self.assertEqual(C.sizeof(ParticipantInfo),100)
        self.assertEqual(SharedMemory.mParticipantInfo.offset,28)

    def test_preserves_raw_and_every_published_channel(self):
        raw=snapshot(91)
        f=decode(raw,"Alex Morgan")
        self.assertEqual(base64.b64decode(f["raw_b64"]),raw)
        self.assertEqual(len(f["published"]),158)
        self.assertEqual(len(f["participants"]),6)
        self.assertEqual(f["participants"][0]["last_lap_s"],90)
        self.assertEqual(f["telemetry"]["tyre_compound"],["Soft Slick"]*4)
        self.assertIn("mSuspensionTravel",f["telemetry"]["channels"])
        self.assertNotIn("mParticipantInfo",f["telemetry"]["channels"])

    def test_unknown_versions_keep_complete_bytes_without_guessing(self):
        raw=bytearray(snapshot(91));struct.pack_into("<I",raw,0,999)
        f=decode(bytes(raw),"Alex Morgan")
        self.assertFalse(f["decoded"])
        self.assertEqual(base64.b64decode(f["raw_b64"]),raw)
        self.assertIsNone(f["telemetry"])

    def test_replay_and_wrong_driver_do_not_get_personal_telemetry(self):
        state=SharedMemory.from_buffer_copy(snapshot(91))
        state.mGameState=6
        self.assertFalse(decode(bytes(state),"Alex Morgan")["telemetry_verified"])
        self.assertFalse(decode(snapshot(91),"Jamie Chen")["telemetry_verified"])

    def test_duplicate_driver_names_fail_ownership_verification(self):
        state=SharedMemory.from_buffer_copy(snapshot(91))
        state.mParticipantInfo[1].mName=b"Alex Morgan"
        f=decode(bytes(state),"Alex Morgan")
        self.assertFalse(f["telemetry_verified"])
        self.assertTrue(f["participants"][0]["ambiguous"])

    def test_nonfinite_fields_are_null_and_json_stays_standard(self):
        state=SharedMemory.from_buffer_copy(snapshot(91));state.mOilTempCelsius=float("nan")
        f=decode(bytes(state),"Alex Morgan")
        self.assertIsNone(f["published"]["mOilTempCelsius"])
        json.dumps(f,allow_nan=False)


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/"queue.sqlite3"
        self.spool=Spool(self.path);self.spool.bind("http://localhost:8787","source")

    def tearDown(self):
        self.spool.close();self.temp.cleanup()

    def test_pending_batch_survives_restart_with_identical_bytes(self):
        run=str(uuid.uuid4())
        self.spool.append("RACE-ONE",run,{"sequence":0,"raw_b64":"abc"})
        expected=self.spool.batch("source")
        self.spool.close();self.spool=Spool(self.path)
        self.assertEqual(self.spool.batch("source"),expected)
        self.spool.append("RACE-ONE",run,{"sequence":1})
        self.spool.acknowledge(expected[0])
        self.assertEqual(self.spool.pending(),1)
        self.assertNotEqual(self.spool.batch("source")[0],expected[0])

    def test_sessions_and_runs_are_not_combined_in_one_batch(self):
        self.spool.append("RACE-ONE","run1",{"sequence":0})
        self.spool.append("RACE-TWO","run1",{"sequence":1})
        first=self.spool.batch("source")
        self.assertEqual(json.loads(gzip.decompress(first[1]))["event_code"],"RACE-ONE")
        self.assertEqual(len(json.loads(gzip.decompress(first[1]))["frames"]),1)
        self.spool.acknowledge(first[0])
        self.assertEqual(json.loads(gzip.decompress(self.spool.batch("source")[1]))["event_code"],"RACE-TWO")

    def test_larger_batch_keeps_existing_outbox_and_leaves_remaining_frames(self):
        for sequence in range(MAX_BATCH_FRAMES + 130):
            self.spool.append("AUTO","run",{"sequence":sequence,"name":"Élodie"})
        existing = self.spool.batch("source", limit=120)
        self.assertEqual(self.spool.batch("source"), existing)
        self.spool.acknowledge(existing[0])
        batch_id, payload = self.spool.batch("source")
        frames = json.loads(gzip.decompress(payload))["frames"]
        self.assertEqual(frames,[{"sequence":n,"name":"Élodie"} for n in range(120,120+MAX_BATCH_FRAMES)])
        self.spool.acknowledge(batch_id)
        self.assertEqual(self.spool.pending(),10)

    def test_batch_size_counts_envelope_and_preserves_unsent_frames(self):
        for sequence in range(10):
            self.spool.append("AUTO","run",{"sequence":sequence,"raw_b64":"a"*80})
        with patch("ams_recorder.spool.MAX_BATCH_JSON_BYTES", 400):
            batch_id, payload = self.spool.batch("source")
        body = gzip.decompress(payload)
        self.assertLessEqual(len(body),400)
        self.assertEqual([f["sequence"] for f in json.loads(body)["frames"]],[0,1])
        self.spool.acknowledge(batch_id)
        self.assertEqual(self.spool.pending(),8)

    def test_queue_cannot_be_retargeted_to_another_driver(self):
        with self.assertRaises(ValueError):self.spool.bind("http://localhost:8787","another-driver")

    def test_acknowledged_bytes_no_longer_count_toward_queue_cap(self):
        self.spool.append("RACE","run",{"sequence":0})
        batch=self.spool.batch("source");self.assertGreater(self.spool.size_bytes(),0)
        self.spool.acknowledge(batch[0]);self.assertEqual(self.spool.size_bytes(),0)


class TransportTest(unittest.TestCase):
    def test_health_exercises_gzip_upload_path_and_checks_the_echoed_challenge(self):
        t = Transport('http://127.0.0.1:8787', 'test-token')
        def respond(path, payload):
            self.assertEqual(path, '/api/recorder/health')
            body = json.loads(gzip.decompress(payload))
            return dict(nonce=body['nonce'], recorder_id='recorder', upload_ready=True, account_linked=True)
        with patch.object(t, 'request', side_effect=respond):
            self.assertIs(t.health('recorder')['upload_ready'], True)
        for result in (dict(nonce='stale', recorder_id='recorder', upload_ready=True, account_linked=True),
                       dict(nonce='challenge', recorder_id='another-recorder', upload_ready=True, account_linked=True),
                       dict(nonce='challenge', recorder_id='recorder', upload_ready='true', account_linked=True),
                       dict(nonce='challenge', recorder_id='recorder', upload_ready=True, account_linked=False)):
            with patch('ams_recorder.transport.secrets.token_urlsafe', return_value='challenge'), patch.object(t, 'request', return_value=result):
                with self.assertRaises(ValueError): t.health('recorder')

    def test_clock_uses_best_monotonic_round_trip(self):
        t=Transport('http://127.0.0.1:8787','token')
        responses=[{'server_time_ms':100015},{'server_time_ms':200065},{'server_time_ms':300115}]
        with patch.object(t,'request',side_effect=responses),patch('ams_recorder.transport.time.time',side_effect=[100,200,300]),patch('ams_recorder.transport.time.monotonic',side_effect=[0,.02,1,1.1,2,2.2]):
            offset,uncertainty,_=t.clock()
        self.assertAlmostEqual(offset,5)
        self.assertAlmostEqual(uncertainty,10)

    def test_rejects_cleartext_remote_credentials_and_embedded_secrets(self):
        for url in ("http://example.com","https://user:password@example.com","file:///tmp/data","https://example.com/?token=secret"):
            with self.assertRaises(ValueError):Transport(url,"test-token")
        Transport("http://127.0.0.1:8787","test-token")
        Transport("https://league.example","test-token")

    def test_udp_preserves_unrecognized_packets_without_guessing_payload(self):
        raw=struct.pack('<IIBBBB',41,7,0,1,99,4)+b'unknown payload'
        frame=packet_frame(raw,('127.0.0.1',5606))
        self.assertEqual(base64.b64decode(frame['raw_b64']),raw)
        self.assertEqual(frame['udp']['packet_type'],99)
        self.assertFalse(frame['decoded'])
        self.assertIsNone(packet_frame(b'short',('127.0.0.1',5606)))


@unittest.skipUnless(os.name=="nt","Win32 mapping API is tested on Windows CI")
class WindowsMemoryTest(unittest.TestCase):
    def test_readonly_mapping_and_consistent_snapshot(self):
        from ams_recorder.memory import MemoryReader, SnapshotBusy
        from ctypes import wintypes as W
        api=C.WinDLL("kernel32",use_last_error=True)
        api.CreateFileMappingW.argtypes=[W.HANDLE,C.c_void_p,W.DWORD,W.DWORD,W.DWORD,W.LPCWSTR]
        api.CreateFileMappingW.restype=W.HANDLE
        api.MapViewOfFile.argtypes=[W.HANDLE,W.DWORD,W.DWORD,W.DWORD,C.c_size_t];api.MapViewOfFile.restype=C.c_void_p
        api.UnmapViewOfFile.argtypes=[C.c_void_p];api.CloseHandle.argtypes=[W.HANDLE]
        name="$ams2-test-"+str(uuid.uuid4())+"$"
        raw=snapshot(91)
        handle=api.CreateFileMappingW(W.HANDLE(-1),None,4,0,len(raw),name)
        self.assertTrue(handle)
        address=api.MapViewOfFile(handle,6,0,0,len(raw))
        self.assertTrue(address)
        reader=MemoryReader(name)
        try:
            C.memmove(address,raw,len(raw))
            sequence=C.c_uint32.from_address(address+SEQUENCE_OFFSET)
            sequence.value|=1
            with self.assertRaises(SnapshotBusy):
                reader.read()
            C.memmove(address,raw,len(raw))
            captured,consistent=reader.read()
            self.assertTrue(consistent)
            self.assertEqual(captured[:len(raw)],raw)
        finally:
            reader.close();api.UnmapViewOfFile(address);api.CloseHandle(handle)


if __name__=="__main__":unittest.main()
