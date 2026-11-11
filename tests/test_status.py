import unittest
import urllib.error

from ams_recorder.status import RecorderStatus, receipt_notice, upload_problem


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.status = RecorderStatus(clock=lambda: self.now)

    def ready(self):
        self.status.update('started')
        self.status.update('comms_ok')

    def recording(self):
        self.ready()
        self.status.update('captured', decoded=True, consistent=True, game_state=2)
        self.status.update('uploaded', pending=0)

    def test_green_means_verified_comms_without_requiring_game_data(self):
        self.status.update('started')
        self.assertEqual(self.status.snapshot().color, 'gray')
        self.status.update('comms_ok')
        self.assertEqual(self.status.snapshot().color, 'green')
        self.assertEqual(self.status.snapshot().title, 'Comms open, awaiting race data')
        self.assertIsNone(self.status.snapshot().last_upload_seconds)

    def test_live_capture_is_red_and_returns_to_green_when_data_stops(self):
        self.recording()
        self.assertEqual(self.status.snapshot().color, 'red')
        self.assertEqual(self.status.snapshot().title, 'Recording in progress')
        self.now += 11
        self.status.update('comms_ok')
        self.assertEqual(self.status.snapshot().color, 'green')

    def test_menu_or_absent_game_is_ready_when_communication_is_healthy(self):
        self.ready()
        self.status.update('capture_wait', message='Waiting for AMS2.', waiting=True)
        self.assertEqual(self.status.snapshot().color, 'green')
        self.status.update('captured', decoded=True, consistent=True, game_state=1)
        self.assertEqual(self.status.snapshot().color, 'green')

    def test_heartbeat_cannot_hide_an_unacknowledged_upload_failure(self):
        self.recording()
        self.status.update('upload_error', message='Upload failed')
        self.status.update('comms_ok')
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.assertEqual(self.status.snapshot().title, 'Upload problem')
        self.status.update('queued', pending=0)
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.status.update('uploaded', pending=0)
        self.assertEqual(self.status.snapshot().color, 'red')

    def test_stale_or_failed_comms_are_amber_and_probe_can_recover_them(self):
        self.ready()
        self.now += 11
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.status.update('comms_error', message='Cannot reach server')
        self.status.update('uploaded', pending=0)
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.status.update('comms_ok')
        self.assertEqual(self.status.snapshot().color, 'green')

    def test_link_required_overrides_previous_healthy_capture(self):
        self.recording()
        self.status.update('link_required', message='Approve Steam connection')
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.assertEqual(self.status.snapshot().title, 'Steam link required')
        self.status.update('comms_ok')
        self.assertEqual(self.status.snapshot().color, 'green')

    def test_capture_errors_unsupported_data_and_queue_pressure_are_amber(self):
        self.recording()
        self.status.update('capture_wait', message='Disk full')
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.status.update('captured', decoded=True, consistent=True, game_state=2)
        self.status.update('uploaded', pending=2000)
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.status.update('uploaded', pending=0)
        self.assertEqual(self.status.snapshot().color, 'red')
        self.status.update('captured', decoded=False, consistent=False, game_state=2)
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.status.update('capture_wait', message='Waiting for AMS2.', waiting=True)
        self.assertEqual(self.status.snapshot().color, 'green')

    def test_brief_snapshot_retry_stays_recording_and_sustained_contention_turns_amber(self):
        self.recording()
        message = 'No consistent snapshot available; retrying'
        self.status.update('snapshot_retry', message=message)
        self.now += .1
        self.assertEqual(self.status.snapshot().color, 'red')
        self.status.update('snapshot_retry', message=message)
        self.now += .16
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.assertEqual(self.status.snapshot().detail, message)
        self.status.update('snapshot_read')
        self.assertEqual(self.status.snapshot().color, 'red')

    def test_stopping_and_stopped_are_gray_fatal_is_amber(self):
        self.recording()
        self.status.update('stopping')
        self.assertEqual(self.status.snapshot().color, 'gray')
        self.status.update('stopped', pending=5)
        self.assertEqual(self.status.snapshot().color, 'gray')
        self.assertIn('saved', self.status.snapshot().detail)
        self.status.update('fatal', message='Disk failed')
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.assertEqual(self.status.snapshot().detail, 'Disk failed')

    def test_http_failure_messages_never_include_provider_credentials(self):
        error = urllib.error.HTTPError('https://league.example', 401, 'secret-token', {}, None)
        message = upload_problem(error)
        self.assertIn('connection was not recognized', message)
        self.assertNotIn('secret-token', message)

    def test_saved_processing_warning_is_distinct_from_rejected_upload(self):
        self.recording()
        notice = receipt_notice({'stored': True, 'processing_status': 'needs_review',
            'warnings': [{'severity': 'warning', 'message': 'All data is saved.'}]})
        self.status.update('uploaded', pending=0, review=notice)
        self.assertEqual(self.status.snapshot().color, 'amber')
        self.assertIn('Data saved', self.status.snapshot().title)
        self.assertIn('All data is saved', self.status.snapshot().detail)
        self.status.update('uploaded', pending=0, review=None)
        self.assertEqual(self.status.snapshot().color, 'red')
