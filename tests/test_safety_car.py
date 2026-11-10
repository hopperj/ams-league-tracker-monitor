import base64
import unittest

from ams_recorder.decoder import decode
from ams_recorder.demo import snapshot
from ams_recorder.schema import SharedMemory


SERVICE_IDENTITIES = (
    ("Safety Car  (AI)", "BMW M3 Safety Car", "SafetyCar"),
    ("Game service", "Chevrolet Camaro", "SafetyCar"),
    ("Game service", "Chevrolet Camaro Safety Car", "Road"),
)


class SafetyCarTest(unittest.TestCase):
    def state(self, identity, driver="Jamie Chen", slot=0):
        state = SharedMemory.from_buffer_copy(snapshot(91, driver))
        name, car, car_class = identity
        state.mParticipantInfo[slot].mName = name.encode()
        state.mCarNames[slot].value = car.encode()
        state.mCarClassNames[slot].value = car_class.encode()
        if state.mViewedParticipantIndex == slot:
            state.mCarName = car.encode()
            state.mCarClassName = car_class.encode()
        return state

    def test_service_vehicles_are_archived_but_excluded_from_drivers(self):
        for identity in SERVICE_IDENTITIES:
            with self.subTest(identity=identity):
                raw = bytes(self.state(identity))
                result = decode(raw)
                self.assertEqual([p["slot"] for p in result["participants"]], [1, 2, 3, 4, 5])
                self.assertTrue(result["telemetry_verified"])
                self.assertEqual(result["telemetry"]["name"], "Jamie Chen")
                self.assertEqual(base64.b64decode(result["raw_b64"]), raw)
                self.assertEqual(result["published"]["mParticipantInfo"][0]["mName"], identity[0])
                self.assertEqual(result["published"]["mNumParticipants"], 6)

    def test_viewing_a_safety_car_never_assigns_driver_telemetry(self):
        for identity in SERVICE_IDENTITIES:
            raw = bytes(self.state(identity, driver="Alex Morgan"))
            for driver in (None, identity[0]):
                with self.subTest(identity=identity, driver=driver):
                    result = decode(raw, driver)
                    self.assertFalse(result["telemetry_verified"])
                    self.assertIsNone(result["telemetry"])

    def test_similar_names_inactive_drivers_and_unclassified_positions_are_kept(self):
        state = self.state(("Safety Carl", "Chevrolet Camaro", "Road"))
        state.mParticipantInfo[0].mIsActive = False
        state.mParticipantInfo[0].mRacePosition = 0
        result = decode(bytes(state))
        self.assertEqual(len(result["participants"]), 6)
        self.assertEqual(result["participants"][0]["name"], "Safety Carl")
        self.assertTrue(result["telemetry_verified"])

    def test_racer_names_alone_cannot_hide_a_competitor(self):
        for name in ('Safety Car', 'Pace Car', 'Safety Car (AI)'):
            result = decode(bytes(self.state((name, 'Chevrolet Camaro', 'Road'))))
            self.assertEqual(len(result['participants']), 6)
