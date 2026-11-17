import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ams_recorder.dedicated import Capture, CHECKPOINT, STATUS
from ams_recorder.spool import Spool


def status(stage='Race1', lap=1):
    return {'state':'Running', 'lobbyid':123, 'now':1_790_000_000, 'name':'League lobby',
            'attributes': {'SessionState':'Race', 'SessionPhase':'Green', 'SessionStage':stage,
                           'SessionTimeElapsed':10, 'SessionTimeDuration':2400, 'Flags':1048576, 'TrackId':4},
            'members': [{'refid':13, 'steamid':76561198000000001, 'name':'Alex'}],
            'participants': [{'id':80, 'attributes': {'Name':'Alex', 'RefId':13, 'VehicleId':19, 'IsPlayer':1,
                'RacePosition':2, 'State':'Racing', 'CurrentLap':lap, 'Speed':108,
                'PositionX':4000, 'PositionY':3000, 'PositionZ':2000}}]}


def event(index, name, **attrs):
    return dict(index=index, time=1_790_000_000, name=name, participantid=80, attributes=attrs)


class NativeAPI:
    def __init__(self):
        self.status = status()
        self.events = []
        self.first = 0
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == STATUS:
            return self.status
        if path == '/api/list/tracks':
            return {'list':[{'id':4, 'name':'Interlagos_GP'}]}
        if path == '/api/list/vehicles':
            return {'list':[{'id':19, 'name':'Porsche 911 GT3 R', 'class':'GT3_Gen2'}]}
        if path == '/api/log/overview':
            return {'first':self.first, 'count':len(self.events)}
        if path.startswith('/api/log/range?offset='):
            offset = int(path.split('offset=')[1].split('&')[0])
            return {'first':self.first, 'events':[row for row in self.events if row['index'] >= offset]}
        raise AssertionError(path)


class DedicatedRecorderTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'outbox.db'
        self.spool = Spool(self.path)
        self.addCleanup(self.spool.close)
        self.api = NativeAPI()

    def frames(self):
        rows = self.spool.db.execute('SELECT payload FROM frames ORDER BY id').fetchall()
        return [json.loads(gzip.decompress(raw)) for raw, in rows]

    def test_persists_native_events_and_cursor_across_recorder_restart(self):
        capture = Capture(self.api, self.spool)
        capture.poll()
        original = self.frames()[0]
        self.assertEqual(original['participants'][0]['steam_id'], '76561198000000001')
        self.assertEqual(original['participants'][0]['speed_mps'], 30)
        self.assertEqual(original['participants'][0]['world_position'], [4,3,2])
        self.api.events = [event(0, 'Lap', Lap=1, LapTime=90500, CountThisLapTimes=0, Sector1Time=30000),
                           event(1, 'CutTrackStart', Lap=2, LapTime=100),
                           event(2, 'CutTrackEnd', ElapsedTime=200),
                           event(3, 'CutTrackStart', Lap=2, LapTime=6900)]
        self.api.status = status(lap=2)
        Capture(self.api, self.spool).poll()
        Capture(self.api, self.spool).poll()
        events = [row for row in self.frames() if 'dedicated_event' in row]
        self.assertEqual([row['dedicated_event']['index'] for row in events], [0,1,2,3])
        self.assertEqual(self.spool.checkpoint(CHECKPOINT)['cursor'], 4)
        lap = events[0]['participants'][0]
        self.assertEqual(lap['last_lap_s'], 90.5)
        self.assertFalse(lap['completed_lap_valid'])
        self.assertEqual(events[0]['stage_id'], original['stage_id'])
        self.assertEqual(events[3]['dedicated_event']['attributes']['LapTime'], 6900)

    def test_results_remain_with_the_completed_stage_when_the_session_advances(self):
        capture = Capture(self.api, self.spool)
        capture.poll()
        self.api.events = [event(0, 'Results', Lap=4, State='Finished', RacePosition=1),
                           event(1, 'StageChanged', NewStage='Practice1')]
        self.api.status = status('Practice1')
        capture.poll()
        frames = self.frames()
        self.assertEqual(frames[1]['stage_id'], frames[0]['stage_id'])
        self.assertEqual(frames[1]['participants'][0]['race_state'], 3)
        self.assertEqual(frames[-1]['phase'], 'practice')
        self.assertNotEqual(frames[-1]['stage_id'], frames[0]['stage_id'])

    def test_log_rollover_is_recorded_and_server_restart_starts_a_new_run(self):
        with patch('ams_recorder.dedicated.invocation', return_value='launch-one') as invocation:
            capture = Capture(self.api, self.spool, 'ams2-dedicated.service')
            capture.poll()
            first_run = capture.state['run']
            self.api.first = 5
            self.api.events = [event(5, 'Impact')]
            capture.poll()
            self.assertTrue(any(frame.get('log_gap') == 5 for frame in self.frames()))
            invocation.return_value = 'launch-two'
            self.api.first = 0
            self.api.events = []
            capture.poll()
        self.assertNotEqual(capture.state['run'], first_run)
        self.assertEqual(capture.state['cursor'], 0)
