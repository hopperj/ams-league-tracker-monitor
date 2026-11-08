import base64
import math
import unittest
from datetime import timezone

from ams_recorder.scenarios import SpaRace, NAMES, START, START_MS


class SpaScenarioTest(unittest.TestCase):
    def test_requested_date_and_full_recorder_rate(self):
        scenario=SpaRace()
        self.assertEqual(START.strftime('%A'),'Thursday')
        self.assertEqual(START.isoformat(),'2026-11-05T21:00:00-04:00')
        self.assertEqual(START.astimezone(timezone.utc).isoformat(),'2026-11-06T01:00:00+00:00')
        self.assertEqual(scenario.sample_count(),97501)

    def test_each_observer_has_verified_personal_telemetry_and_the_same_field(self):
        scenario=SpaRace()
        for elapsed in (-60,0,123,500,750,1100,1535):
            fields=[]
            for observer,name in enumerate(NAMES):
                sequence=round((elapsed+60)*60);frame=scenario.frame(sequence,observer)
                self.assertEqual(len(frame['published']),158)
                self.assertEqual(len(base64.b64decode(frame['raw_b64'])),24576)
                self.assertEqual(frame['raw_bytes'],24576)
                self.assertTrue(frame['synthetic']);self.assertFalse(frame['replay'])
                self.assertTrue(frame['telemetry_verified']);self.assertEqual(frame['telemetry']['name'],name)
                self.assertEqual(frame['captured_at_ms'],START_MS+elapsed*1000)
                self.assertTrue(all(math.isfinite(p['speed_mps']) for p in frame['participants']))
                fields.append(sorted((p['name'],p['laps_completed'],p['race_state'],p['position']) for p in frame['participants']))
            self.assertEqual(fields[0],fields[1]);self.assertEqual(fields[1],fields[2])

    def test_finished_distances_exercise_the_floor_completion_rule(self):
        scenario=SpaRace();frame=scenario.frame(scenario.sample_count()-1,0)
        self.assertEqual([(p['name'],p['laps_completed'],p['race_state']) for p in frame['participants']],
                         [('BootyWiz',12,3),('EPaulD',12,3),('Foote',9,3)])
        self.assertEqual(12*90//100,10)

    def test_lap_counters_never_roll_back_and_pit_stops_are_observable(self):
        scenario=SpaRace()
        previous=[0,0,0];pits=set()
        for elapsed in range(-60,scenario.end_seconds+1):
            for index in range(3):
                p=scenario.progress(elapsed,index)
                self.assertGreaterEqual(p['completed'],previous[index]);previous[index]=p['completed']
                if p['pit']==2:pits.add(index)
        self.assertEqual(pits,{0,1,2})


if __name__=='__main__':unittest.main()
