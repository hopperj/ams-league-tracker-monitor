import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ams_recorder.configuration import save_configuration
from ams_recorder.engine import run
from ams_recorder.spool import Spool
from ams_recorder.status import RecorderStatus


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.failures = 0
        self.bad_ack = False
        self.accepted = True
        self.ack_extras = {}
        self.received = {}
        self.retry_bytes = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def send(self,status,body):
                data=json.dumps(body).encode();self.send_response(status);self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self):
                self.send(200,{"recorder_id":"recorder", "driver_name":"Alex Morgan", "server_time_ms":int(time.time()*1000)})
            def do_POST(self):
                raw=self.rfile.read(int(self.headers["Content-Length"]))
                body=json.loads(gzip.decompress(raw))
                owner.retry_bytes.append(raw)
                if owner.failures:
                    owner.failures-=1;self.send(503,{"error":"unavailable"});return
                if not owner.bad_ack: owner.received[body["batch_id"]]=body
                self.send(200,{"batch_id":"wrong" if owner.bad_ack else body["batch_id"],"accepted":owner.accepted,**owner.ack_extras})
        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base = "http://127.0.0.1:"+str(self.server.server_address[1])
        save_configuration(self.root/"config.json", {"server":self.base,"token":"test-token","recorder_id":"recorder","driver_name":"Alex Morgan","event_code":"TEST"})
        self.args = SimpleNamespace(config=str(self.root/"config.json"),spool=str(self.root/"spool.sqlite3"),demo=True,
                                    demo_offset=91,hz=20,batch_seconds=.05,max_spool_mb=10,duration=.35,replay=None,udp=False)

    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close();self.temp.cleanup()

    def test_gui_worker_thread_can_capture_and_emit_confirmed_uploads(self):
        events, errors = [], []
        status=RecorderStatus(batch_seconds=.05,hz=20)
        greens=[]
        def progress(kind,**values):
            events.append(kind);status.update(kind,**values)
            if kind=="uploaded":greens.append(status.snapshot().green)
        def record():
            try:run(self.args,on_event=progress)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=record);worker.start();worker.join(timeout=5)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertIn("uploaded",events);self.assertIn(True,greens)
        uploaded=sum(len(batch["frames"]) for batch in self.received.values())
        spool=Spool(self.args.spool)
        self.assertEqual(uploaded+spool.pending(),events.count("captured"));spool.close()
        self.assertFalse(status.snapshot().green)

    def test_failed_batch_retries_identical_bytes_and_status_recovers(self):
        self.failures=1;self.args.duration=15
        events=[]
        stop=threading.Event()
        def progress(kind,**values):
            events.append(kind)
            if kind=='uploaded':stop.set()
        # Stop after the acknowledgement, allowing slow Windows disk commits
        # without assuming the first attempt + one-second retry fit in 1.35s.
        run(self.args,stop_event=stop,on_event=progress)
        self.assertIn("upload_error",events);self.assertIn("uploaded",events)
        self.assertLess(events.index("upload_error"),events.index("uploaded"))
        self.assertEqual(self.retry_bytes[0],self.retry_bytes[1])

    def test_wrong_ack_does_not_emit_success_or_remove_queued_samples(self):
        self.bad_ack=True;events=[]
        run(self.args,on_event=lambda kind,**values:events.append(kind))
        self.assertIn("upload_error",events);self.assertNotIn("uploaded",events)
        spool=Spool(self.args.spool)
        self.assertEqual(spool.pending(),events.count("captured"));self.assertIsNotNone(spool.batch("recorder"));spool.close()

    def test_memory_initialization_failure_does_not_leave_an_uploader_running(self):
        self.args.demo=False
        before=[t for t in threading.enumerate() if t.name=="uploader"]
        with patch("ams_recorder.engine.MemoryReader",side_effect=RuntimeError("Memory unavailable")):
            with self.assertRaises(RuntimeError):run(self.args)
        self.assertEqual([t for t in threading.enumerate() if t.name=="uploader"],before)

    def test_non_boolean_ack_does_not_turn_green_or_discard_queued_data(self):
        self.accepted="false";events=[]
        run(self.args,on_event=lambda kind,**values:events.append(kind))
        self.assertNotIn("uploaded",events)
        spool=Spool(self.args.spool)
        self.assertEqual(spool.pending(),events.count("captured"));spool.close()

    def test_saved_warning_clears_client_queue_and_reports_review_without_blocking(self):
        self.ack_extras={'stored':True,'processing_status':'needs_review','warnings':[{'severity':'warning','message':'Late capture saved.'}]}
        events=[];reviews=[]
        def progress(kind,**values):
            events.append(kind)
            if kind=='uploaded':reviews.append(values.get('review'))
        run(self.args,on_event=progress)
        self.assertTrue(reviews);self.assertIn('Data saved',reviews[0])
        sent=sum(len(batch['frames']) for batch in self.received.values())
        queue=Spool(self.args.spool)
        self.assertEqual(sent+queue.pending(),events.count('captured'));queue.close()

    def test_explicitly_unstored_response_keeps_the_batch_locally(self):
        self.ack_extras={'stored':False}
        events=[];run(self.args,on_event=lambda kind,**values:events.append(kind))
        self.assertNotIn('uploaded',events)
        queue=Spool(self.args.spool)
        self.assertEqual(queue.pending(),events.count('captured'));queue.close()


if __name__ == "__main__": unittest.main()
