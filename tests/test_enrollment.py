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

    def test_bad_setup_directs_user_to_link_before_recording(self):
        self.setup.write_text('not json')
        config,notice=prepare_enrolled(self.config,self.queue,self.setup)
        self.assertTrue(config['recorder_id']);self.assertIn('before recording',notice)

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

    def test_generic_download_opens_browser_and_remembers_the_approved_account(self):
        self.setup.unlink()
        config,notice=prepare_enrolled(self.config,self.queue,self.setup)
        self.assertIsNone(notice);self.assertNotIn('enrollment_token',config)
        queue=Spool(self.queue);queue.append('AUTO','run',{'sequence':1,'captured_at_ms':1000});queue.close()
        events=[];requests=[];ident='B'*43
        class NoWait:
            def is_set(self):return False
            def wait(self,seconds):return False
        class FakeTransport:
            def __init__(self,server,token):self.server=server
            def register(self,recorder_id):return {}
            def request(self,path,payload=None):
                requests.append((path,json.loads(gzip.decompress(payload)) if payload else None))
                if path=='/api/recorders/link/start':return dict(status='pending',pairing_id=ident,approval_url=self.server+prefix+ident)
                return dict(status='linked',account_name='Approved racer',driver_id='permanent-racer')
        for prefix in ('/connect-recorder/', '/#connect-recorder/'):
            with self.subTest(prefix=prefix), patch('ams_recorder.enrollment.Transport',FakeTransport):
                requests.clear()
                events.clear()
                connect(self.config,NoWait(),lambda *event:events.append(event))
                self.assertEqual(requests,[('/api/recorders/link/start',{'approval_url_format':'path'}),('/api/recorders/link/status?pairing_id='+ident,None)])
                self.assertEqual(events[0],('open_browser',config['server']+prefix+ident))
        linked=load_configuration(self.config)
        self.assertTrue(linked['account_linked']);self.assertEqual(linked['account_driver_id'],'permanent-racer')
        for key in ('recorder_id','token','server','event_code'):self.assertEqual(linked[key],config[key])
        queue=Spool(self.queue);self.assertEqual(queue.pending(),1);queue.close()

    def test_browser_url_must_stay_on_the_saved_server(self):
        config,_=prepare_enrolled(self.config,self.queue,self.setup)
        class FakeTransport:
            def __init__(self,server,token):self.server=server
            def register(self,ident):return {}
            def request(self,*args):return dict(status='pending',pairing_id='B'*43,approval_url=url)
        for url in ('https://evil.example/', config['server']+'/connect-recorder/'+'C'*43):
            with self.subTest(url=url), patch('ams_recorder.enrollment.Transport',FakeTransport):
                events=[]
                connect(self.config,threading.Event(),lambda *event:events.append(event))
                self.assertEqual(len(events),1)
                self.assertEqual(events[0][0],'link_status')
                self.assertIn('Recording is paused',events[0][1])
