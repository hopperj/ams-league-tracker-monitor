import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from ams_recorder.configuration import prepare_configuration,load_configuration,save_configuration
from ams_recorder.decoder import decode
from ams_recorder.demo import snapshot
from ams_recorder.schema import SharedMemory
from ams_recorder.spool import Spool


class AutomaticConnectionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=self.root/'config.json';self.spool=self.root/'spool.sqlite3'
        self.server='http://127.0.0.1:8787'

    def tearDown(self):self.temp.cleanup()

    def test_new_identity_is_private_durable_and_reused_offline(self):
        with patch('ams_recorder.transport.Transport.request',side_effect=AssertionError('Network is not needed')):
            first=prepare_configuration(self.config,self.spool,self.server)
            second=prepare_configuration(self.config,self.spool)
        self.assertEqual(first,second);self.assertEqual(first,load_configuration(self.config))
        self.assertEqual(str(uuid.UUID(first['recorder_id'])),first['recorder_id'])
        self.assertEqual(len(first['token']),43);self.assertTrue(first['auto_registration'])
        self.assertEqual(first['event_code'],'AUTO')
        other=prepare_configuration(self.root/'other.json',self.root/'other.sqlite3',self.server)
        self.assertNotEqual(first['token'],other['token']);self.assertNotEqual(first['recorder_id'],other['recorder_id'])

    def test_legacy_credentials_and_exact_outbox_survive_upgrade_and_code_changes(self):
        old=dict(server=self.server,token='legacy-secret',recorder_id='legacy-recorder',driver_name='Alex',event_code='OLD')
        save_configuration(self.config,old);queue=Spool(self.spool);queue.bind(self.server,old['recorder_id'])
        queue.append('OLD','run',{'sequence':0});before=queue.batch(old['recorder_id']);queue.close()
        self.assertEqual(prepare_configuration(self.config,self.spool),old)
        current=prepare_configuration(self.config,self.spool,event='AUTO')
        self.assertEqual(current['token'],old['token']);self.assertEqual(current['recorder_id'],old['recorder_id'])
        self.assertNotIn('auto_registration',current)
        queue=Spool(self.spool)
        try:self.assertEqual(queue.batch(current['recorder_id']),before);self.assertEqual(queue.pending(),1)
        finally:queue.close()

    def test_destination_changes_and_missing_or_corrupt_settings_never_replace_old_data(self):
        original=prepare_configuration(self.config,self.spool,self.server)
        with self.assertRaises(ValueError):prepare_configuration(self.config,self.spool,'https://other.example')
        self.assertEqual(load_configuration(self.config),original)
        self.config.unlink()
        with self.assertRaises(ValueError):prepare_configuration(self.config,self.spool,self.server)
        self.assertFalse(self.config.exists())
        self.config.write_text('{corrupt')
        with self.assertRaises(ValueError):prepare_configuration(self.config,self.spool,self.server)
        self.assertEqual(self.config.read_text(),'{corrupt')

    def test_two_first_launches_share_the_same_durable_identity(self):
        Spool(self.spool).close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:prepare_configuration(self.config,self.spool,self.server),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(load_configuration(self.config),results[0])

    def test_automatic_telemetry_identity_comes_from_the_matching_live_car(self):
        raw=snapshot(91);good=decode(raw)
        self.assertTrue(good['telemetry_verified']);self.assertEqual(good['telemetry']['name'],'Alex Morgan')
        self.assertEqual(base64.b64decode(good['raw_b64']),raw)
        for change in ('duplicate','wrong_car','wrong_class','wrong_speed','inactive','paused'):
            with self.subTest(change=change):
                state=SharedMemory.from_buffer_copy(raw)
                if change=='duplicate':state.mParticipantInfo[1].mName=b'Alex Morgan'
                if change=='wrong_car':state.mCarName=b'Another car'
                if change=='wrong_class':state.mCarClassName=b'Another class'
                if change=='wrong_speed':state.mSpeed+=10
                if change=='inactive':state.mParticipantInfo[0].mIsActive=False
                if change=='paused':state.mGameState=3
                self.assertFalse(decode(bytes(state))['telemetry_verified'])
