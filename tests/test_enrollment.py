import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ams_recorder.configuration import load_configuration
from ams_recorder.enrollment import prepare_enrolled, connect
from ams_recorder.spool import Spool


class EnrollmentTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.config=self.root/'config.json';self.queue=self.root/'queue.sqlite3';self.setup=self.root/'AMSLeagueRecorder.enroll.json'
        self.setup.write_text(json.dumps(dict(schema_version=1,server='http://127.0.0.1:8787',enrollment_token='A'*43)))

    def tearDown(self):self.temp.cleanup()

    def test_personalized_download_prepares_connection_without_network_or_manual_key(self):
        config,notice=prepare_enrolled(self.config,self.queue,self.setup)
        self.assertIsNone(notice);self.assertEqual(config['server'],'http://127.0.0.1:8787');self.assertEqual(config['enrollment_token'],'A'*43)
        self.assertNotEqual(config['token'],config['enrollment_token'])
        again,_=prepare_enrolled(self.config,self.queue,self.setup);self.assertEqual(config,again)

    def test_download_never_retargets_existing_credentials_or_queued_bytes(self):
        config,_=prepare_enrolled(self.config,self.queue,self.setup)
        queue=Spool(self.queue);queue.append('AUTO','run',{'sequence':1,'captured_at_ms':1000});queue.close()
        self.setup.write_text(json.dumps(dict(schema_version=1,server='https://other.example',enrollment_token='B'*43)))
        result,notice=prepare_enrolled(self.config,self.queue,self.setup)
        self.assertEqual(result,config);self.assertIn('unchanged',notice)
        queue=Spool(self.queue);self.assertEqual(queue.pending(),1);queue.close()

    def test_bad_setup_does_not_block_capture(self):
        self.setup.write_text('not json')
        config,notice=prepare_enrolled(self.config,self.queue,self.setup)
        self.assertTrue(config['recorder_id']);self.assertIn('Recording continues',notice)

    def test_connection_saves_owner_and_clears_only_the_single_use_setup_token(self):
        config,_=prepare_enrolled(self.config,self.queue,self.setup);events=[];cancel=threading.Event()
        class FakeTransport:
            def __init__(self,server,token):self.server=server
            def register(self,ident):return {}
            def request(self,path,payload=None):
                body=json.loads(gzip.decompress(payload));self.body=body
                return dict(status='linked',account_name='Racer',driver_id='permanent-racer')
        with patch('ams_recorder.enrollment.Transport',FakeTransport):connect(self.config,cancel,lambda *event:events.append(event))
        result=load_configuration(self.config)
        for key in ('recorder_id','token','server','event_code'):self.assertEqual(result[key],config[key])
        self.assertTrue(result['account_linked']);self.assertNotIn('enrollment_token',result)
        self.assertEqual(result['account_driver_id'],'permanent-racer')
        self.assertEqual(events[0][0],'link_complete')
        after,_=prepare_enrolled(self.config,self.queue,self.setup);self.assertNotIn('enrollment_token',after)

    def test_browser_url_must_stay_on_the_saved_server(self):
        prepare_enrolled(self.config,self.queue,self.setup);cancel=threading.Event();events=[]
        class FakeTransport:
            def __init__(self,server,token):self.server=server
            def register(self,ident):return {}
            def request(self,*args):return dict(status='pending',pairing_id='B'*43,approval_url='https://evil.example/')
        def notify(kind,value):events.append((kind,value));cancel.set()
        with patch('ams_recorder.enrollment.Transport',FakeTransport):connect(self.config,cancel,notify)
        self.assertFalse(any(kind=='open_browser' for kind,value in events));self.assertIn('Recording continues',events[0][1])
