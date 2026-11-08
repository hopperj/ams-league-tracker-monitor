import json
from pathlib import Path
import tempfile
import unittest
from ams_recorder.configuration import connection_values,load_configuration,save_configuration

class AutoConfigurationTest(unittest.TestCase):
    def test_new_users_need_no_session_code_and_explicit_codes_still_work(self):
        for value in ('','  ','auto','AUTO'):
            self.assertEqual(connection_values('http://127.0.0.1:8787','secret',value)[2],'AUTO')
        self.assertEqual(connection_values('http://127.0.0.1:8787','secret','race-code')[2],'RACE-CODE')
        with self.assertRaises(ValueError):connection_values('http://127.0.0.1:8787','','')

    def test_settings_round_trip_preserves_old_backlog_destination_and_auto_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            value={'server':'http://127.0.0.1:8787','token':'secret','event_code':'LEGACY','recorder_id':'id','driver_name':'Alex'}
            save_configuration(path,value)
            self.assertEqual(load_configuration(path)['event_code'],'LEGACY')
            value['event_code']='AUTO';save_configuration(path,value)
            self.assertEqual(load_configuration(path)['event_code'],'AUTO')
