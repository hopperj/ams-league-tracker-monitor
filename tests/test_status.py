import unittest
import urllib.error

from ams_recorder.status import RecorderStatus, receipt_notice, upload_problem


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.status = RecorderStatus(clock=lambda:self.now)

    def healthy(self):
        self.status.update("started")
        self.status.update("captured", decoded=True, consistent=True, game_state=2)
        self.status.update("uploaded", pending=0)

    def test_green_requires_both_game_data_and_an_upload_acknowledgement(self):
        self.assertFalse(self.status.snapshot().green)
        self.status.update("started")
        self.status.update("captured", decoded=True, consistent=True, game_state=2)
        self.assertFalse(self.status.snapshot().green)
        self.status.update("uploaded", pending=0)
        self.assertTrue(self.status.snapshot().green)

    def test_server_connection_without_game_data_never_turns_green(self):
        self.status.update("started")
        self.status.update("uploaded", pending=0)
        self.assertFalse(self.status.snapshot().green)

    def test_upload_failure_turns_red_until_another_batch_is_confirmed(self):
        self.healthy()
        self.status.update("upload_error", message="Cannot connect")
        self.assertFalse(self.status.snapshot().green)
        self.status.update("queued", pending=0)
        self.assertFalse(self.status.snapshot().green)
        self.status.update("uploaded", pending=0)
        self.assertTrue(self.status.snapshot().green)

    def test_old_ack_or_old_capture_cannot_leave_the_light_green(self):
        self.healthy(); self.now += 11
        self.status.update("captured", decoded=True, consistent=True, game_state=2)
        self.assertFalse(self.status.snapshot().green)
        self.status.update("uploaded", pending=0)
        self.assertTrue(self.status.snapshot().green)
        self.now += 11
        self.status.update("uploaded", pending=0)
        self.assertFalse(self.status.snapshot().green)

    def test_missing_game_queue_pressure_and_unsupported_data_turn_red(self):
        self.healthy()
        self.status.update("capture_wait", message="Waiting for AMS2")
        self.assertFalse(self.status.snapshot().green)
        self.status.update("captured", decoded=True, consistent=True, game_state=2)
        self.status.update("uploaded", pending=2000)
        self.assertFalse(self.status.snapshot().green)
        self.status.update("uploaded", pending=0)
        self.assertTrue(self.status.snapshot().green)
        self.status.update("captured", decoded=False, consistent=False, game_state=2)
        self.assertFalse(self.status.snapshot().green)

    def test_stopping_stopped_and_fatal_states_are_red(self):
        self.healthy(); self.status.update("stopping")
        self.assertFalse(self.status.snapshot().green)
        self.status.update("stopped", pending=5)
        self.assertIn("saved", self.status.snapshot().detail)
        self.status.update("fatal", message="Disk failed")
        self.assertEqual(self.status.snapshot().detail, "Disk failed")

    def test_http_failure_messages_never_include_provider_details_or_credentials(self):
        error = urllib.error.HTTPError("https://league.example",401,"secret-token-in-provider-message",{},None)
        message = upload_problem(error)
        self.assertIn("token rejected",message)
        self.assertNotIn("secret-token-in-provider-message",message)

    def test_saved_processing_warning_is_distinct_from_a_rejected_upload(self):
        self.healthy()
        notice=receipt_notice({'stored':True,'processing_status':'needs_review','warnings':[{'severity':'warning','message':'Session is closed. All data is saved.'}]})
        self.status.update('uploaded',pending=0,review=notice)
        snapshot=self.status.snapshot()
        self.assertFalse(snapshot.green)
        self.assertIn('Data saved',snapshot.title)
        self.assertIn('All data is saved',snapshot.detail)
        self.assertIsNotNone(snapshot.last_upload_seconds)
        self.status.update('uploaded',pending=0,review=None)
        self.assertTrue(self.status.snapshot().green)


if __name__ == "__main__": unittest.main()
