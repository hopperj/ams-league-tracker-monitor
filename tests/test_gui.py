import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ams_recorder.configuration import save_configuration
from ams_recorder.gui import GREEN, RED, AMBER, GRAY, RecorderWindow


class LauncherTest(unittest.TestCase):
    def test_portable_double_click_opens_ui_and_explicit_arguments_keep_cli(self):
        from ams_recorder.launcher import main
        with patch("sys.argv",["AMSLeagueRecorder.pyz"]),patch("ams_recorder.gui.main") as gui,patch("ams_recorder.__main__.main") as cli:
            main();gui.assert_called_once();cli.assert_not_called()
        with patch("sys.argv",["AMSLeagueRecorder.pyz","run"]),patch("ams_recorder.gui.main") as gui,patch("ams_recorder.__main__.main") as cli:
            main();cli.assert_called_once();gui.assert_not_called()


@unittest.skipUnless(os.name=="nt" or sys.platform=="darwin" or os.environ.get("DISPLAY"),"Desktop display required")
class WindowTest(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.root=tk.Tk();self.root.withdraw()
        save_configuration(self.path/"config.json",{"server":"http://127.0.0.1:8787","token":"test-token","recorder_id":"recorder","driver_name":"Alex Morgan","event_code":"TEST"})
        self.window=RecorderWindow(self.root,self.path/"config.json",self.path/"queue.sqlite3",auto_start=False)

    def tearDown(self):
        self.window.stop()
        if self.window.worker:self.window.worker.join(timeout=5)
        if self.window.update_thread:self.window.update_thread.join(timeout=5)
        self.window.link_cancel.set()
        if self.window.link_thread:self.window.link_thread.join(timeout=5)
        try:self.root.destroy()
        except Exception:pass
        self.temp.cleanup()

    def test_light_and_accessible_text_follow_confirmed_status(self):
        status=self.window.status
        now=100
        status.clock=lambda:now
        status.update('started');status.update('comms_ok')
        self.window.poll()
        self.assertEqual(self.window.light.itemcget(self.window.bulb,'fill'),GREEN)
        self.assertEqual(self.window.title.get(),'Comms open, awaiting race data')
        status.update("started");status.update("captured",decoded=True,consistent=True,game_state=2);status.update("uploaded",pending=0)
        status.update('comms_ok')
        self.window.poll()
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),RED)
        self.assertEqual(self.window.title.get(),"Recording in progress")
        status.update("upload_error",message="Connection unavailable")
        self.window.poll()
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),RED)
        self.assertEqual(self.window.detail.get(),"Connection unavailable")
        now+=3
        self.window.poll()
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),AMBER)

    def test_background_capture_start_and_stop_keep_the_window_responsive(self):
        self.window.auto_link_attempted=True
        def recorder(args,stop,on_event):
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            on_event("started");on_event('comms_ok');on_event("captured",decoded=True,consistent=True,game_state=2);on_event("uploaded",pending=0)
            stop.wait(3);on_event("stopped",pending=0)
        with patch("ams_recorder.gui.run",side_effect=recorder):
            self.window.start()
            limit=time.monotonic()+3
            while time.monotonic()<limit and self.window.title.get()!="Recording in progress":self.root.update();time.sleep(.01)
            self.assertEqual(self.window.title.get(),"Recording in progress")
            self.window.stop()
            while time.monotonic()<limit and self.window.worker:self.root.update();time.sleep(.01)
            self.assertIsNone(self.window.worker)
            self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),GRAY)

    def test_saved_connections_have_no_key_field(self):
        self.assertFalse(hasattr(self.window,'token'))
        self.assertEqual(len(self.window.entries),2)
        self.assertEqual(self.window.config['token'],'test-token')

    def test_ready_message_and_controls_fit_the_minimum_window(self):
        self.root.geometry('470x500')
        self.root.deiconify()
        self.window.status.update('started')
        self.window.status.update('comms_ok')
        self.window.poll()
        self.root.update()
        title = self.window.title_label
        self.assertGreaterEqual(title.winfo_height(), title.winfo_reqheight())
        bottom = self.window.start_button.winfo_rooty() + self.window.start_button.winfo_height()
        self.assertLessEqual(bottom, self.root.winfo_rooty() + self.root.winfo_height())
        self.root.withdraw()

    def test_first_launch_creates_an_offline_connection_without_a_setup_prompt(self):
        import tkinter as tk
        root=tk.Toplevel(self.root);root.withdraw()
        try:
            with patch('ams_recorder.transport.Transport.request',side_effect=AssertionError('No first-launch network call')):
                window=RecorderWindow(root,self.path/'fresh.json',self.path/'fresh-queue.sqlite3',auto_start=False)
            self.assertTrue(window.config['auto_registration'])
            self.assertEqual(len(window.config['token']),43)
            self.assertEqual(window.config['event_code'],'AUTO')
            self.assertFalse(window.setup_visible)
            self.assertFalse(hasattr(window,'token'))
        finally:root.destroy()

    def test_failed_update_check_starts_the_installed_recorder_without_waiting_for_steam_again(self):
        self.window.auto_link_attempted=True
        def recorder(args,stop,on_event):
            on_event('started');stop.wait(3)
        with patch('ams_recorder.gui.updates.native_target',return_value=self.path/'recorder.exe'), \
                patch('ams_recorder.gui.updates.check_release',side_effect=OSError('Offline')), \
                patch('ams_recorder.gui.run',side_effect=recorder) as run:
            self.window.startup()
            limit=time.monotonic()+3
            while time.monotonic()<limit and not run.called:self.root.update();time.sleep(.01)
            self.assertTrue(run.called)
            self.assertIn('continuing with',self.window.update_text.get())

    def test_skip_update_cancels_download_before_starting_the_existing_recorder(self):
        ready=threading.Event();release=threading.Event();self.window.auto_link_attempted=True
        def check():ready.set();release.wait(3);return None
        def recorder(args,stop,on_event):on_event('started');stop.wait(3)
        with patch('ams_recorder.gui.updates.native_target',return_value=self.path/'recorder.exe'), \
                patch('ams_recorder.gui.updates.check_release',side_effect=check), \
                patch('ams_recorder.gui.updates.prepare_install') as install, \
                patch('ams_recorder.gui.run',side_effect=recorder) as run:
            self.window.startup();self.assertTrue(ready.wait(1))
            self.root.update();self.assertFalse(run.called)
            self.window.start();self.assertTrue(self.window.update_cancel.is_set())
            release.set();limit=time.monotonic()+3
            while time.monotonic()<limit and not run.called:self.root.update();time.sleep(.01)
            self.assertTrue(run.called);install.assert_not_called()

    def test_ready_update_hands_off_then_closes_before_recording_or_browser_linking(self):
        plan=self.path/'plan.json'
        with patch('ams_recorder.gui.updates.native_target',return_value=self.path/'recorder.exe'), \
                patch('ams_recorder.gui.updates.check_release',return_value=object()), \
                patch('ams_recorder.gui.updates.prepare_install',return_value=plan), \
                patch('ams_recorder.gui.updates.launch_helper') as helper, \
                patch('ams_recorder.gui.run') as run, \
                patch.object(self.window,'close',side_effect=lambda:setattr(self.window,'closing',True)) as close:
            self.window.startup();self.window.update_thread.join(timeout=3)
            self.window.poll()
            helper.assert_called_once_with(plan);close.assert_called_once();run.assert_not_called()
            self.assertIsNone(self.window.link_thread)

    def test_stop_after_download_cancels_the_queued_handoff_without_starting_capture(self):
        plan=self.path/'plan.json'
        with patch('ams_recorder.gui.updates.native_target',return_value=self.path/'recorder.exe'), \
                patch('ams_recorder.gui.updates.check_release',return_value=object()), \
                patch('ams_recorder.gui.updates.prepare_install',return_value=plan), \
                patch('ams_recorder.gui.updates.discard_install') as discard, \
                patch('ams_recorder.gui.updates.launch_helper') as helper, \
                patch('ams_recorder.gui.run') as run:
            self.window.startup();self.window.update_thread.join(timeout=3)
            self.window.stop();self.window.poll()
            discard.assert_called_once_with(plan);helper.assert_not_called();run.assert_not_called()

    def test_bad_update_plan_or_cleanup_failure_does_not_block_the_installed_recorder(self):
        self.window.auto_link_attempted=True
        def recorder(args,stop,on_event):on_event('started');stop.wait(3)
        with patch('ams_recorder.gui.updates.native_target',return_value=self.path/'recorder.exe'), \
                patch('ams_recorder.gui.updates.check_release',return_value=object()), \
                patch('ams_recorder.gui.updates.prepare_install',return_value=self.path/'plan.json'), \
                patch('ams_recorder.gui.updates.launch_helper',side_effect=ValueError('Bad plan')), \
                patch('ams_recorder.gui.updates.discard_install',side_effect=ValueError('Bad plan')), \
                patch('ams_recorder.gui.run',side_effect=recorder) as run:
            self.window.startup();self.window.update_thread.join(timeout=3);self.window.poll()
            limit=time.monotonic()+3
            while time.monotonic()<limit and not run.called:self.root.update();time.sleep(.01)
            self.assertTrue(run.called);self.assertFalse(self.window.closing)


if __name__ == "__main__":unittest.main()
