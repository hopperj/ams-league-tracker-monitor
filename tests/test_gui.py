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
        try:self.root.destroy()
        except Exception:pass
        self.temp.cleanup()

    def test_light_and_accessible_text_follow_confirmed_status(self):
        status=self.window.status
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
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),AMBER)
        self.assertEqual(self.window.detail.get(),"Connection unavailable")

    def test_background_capture_start_and_stop_keep_the_window_responsive(self):
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


if __name__ == "__main__":unittest.main()
