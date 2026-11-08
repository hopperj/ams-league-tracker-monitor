import base64
import math
import unittest
from datetime import timezone

from ams_recorder.scenarios import SpaRace, SpaQualifying, QUALIFYING_START, NAMES, START, START_MS


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


class SpaQualifyingTest(unittest.TestCase):
    def test_qualifying_has_full_owner_stream_and_verified_final_times(self):
        scenario=SpaQualifying()
        for observer,name in enumerate(NAMES):
            first=scenario.frame(0,observer)
            self.assertEqual(first['phase'],'qualifying')
            self.assertEqual(first['captured_at_ms'],round(QUALIFYING_START.timestamp()*1000))
            self.assertEqual(len(first['published']),158)
            self.assertEqual(first['raw_bytes'],24576)
            self.assertEqual(first['telemetry']['name'],name)
            self.assertTrue(first['telemetry_verified'])
            final=scenario.frame(scenario.sample_count()-1,observer)
            by_name={p['name']:p for p in final['participants']}
            self.assertEqual({p['laps_completed'] for p in by_name.values()},{6})
            self.assertAlmostEqual(by_name['EPaulD']['best_lap_s'],122,places=3)
            self.assertAlmostEqual(by_name['BootyWiz']['best_lap_s'],122.4,places=3)
            self.assertAlmostEqual(by_name['Foote']['best_lap_s'],125,places=3)
            self.assertEqual(by_name['EPaulD']['position'],1)
            self.assertLess(final['captured_at_ms'],START_MS)

    def test_sector_boundary_describes_previous_lap_and_invalid_fast_lap_is_recorded(self):
        scenario=SpaQualifying()
        for observer in range(3):
            for index,driver in enumerate(scenario.drivers):
                for lap,at in enumerate(driver['starts'][1:],1):
                    seq=round((at+.05)*60)
                    decoded=scenario.frame(seq,observer)
                    p=next(p for p in decoded['participants'] if p['name']==NAMES[index])
                    self.assertEqual(p['laps_completed'],lap)
                    self.assertEqual(p['sector'],0)
                    self.assertAlmostEqual(sum(p['sectors_s']),p['last_lap_s'],places=3)
        mid=scenario.frame(round((scenario.drivers[1]['starts'][2]+10)*60),1)
        self.assertTrue(next(p for p in mid['participants'] if p['name']=='EPaulD')['invalid_current_lap'])
        self.assertEqual(scenario.drivers[1]['durations'][2],121.2)


if __name__=='__main__':unittest.main()
