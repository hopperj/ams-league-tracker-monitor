import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ams_recorder.configuration import save_configuration
from ams_recorder.gui import GREEN, RED, RecorderWindow


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
        status.update("started");status.update("captured",decoded=True,consistent=True,game_state=2);status.update("uploaded",pending=0)
        self.window.poll()
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),GREEN)
        self.assertEqual(self.window.title.get(),"Transmitting")
        status.update("upload_error",message="Connection unavailable")
        self.window.poll()
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),RED)
        self.assertEqual(self.window.detail.get(),"Connection unavailable")

    def test_background_capture_start_and_stop_keep_the_window_responsive(self):
        def recorder(args,stop,on_event):
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            on_event("started");on_event("captured",decoded=True,consistent=True,game_state=2);on_event("uploaded",pending=0)
            stop.wait(3);on_event("stopped",pending=0)
        with patch("ams_recorder.gui.run",side_effect=recorder):
            self.window.start()
            limit=time.monotonic()+3
            while time.monotonic()<limit and self.window.title.get()!="Transmitting":self.root.update();time.sleep(.01)
            self.assertEqual(self.window.title.get(),"Transmitting")
            self.window.stop()
            while time.monotonic()<limit and self.window.worker:self.root.update();time.sleep(.01)
            self.assertIsNone(self.window.worker)
            self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),RED)

    def test_missing_token_shows_setup_problem_without_a_network_request(self):
        self.window.token.set("")
        self.window.start();self.window.poll()
        self.assertIsNone(self.window.worker)
        self.assertIn("upload token",self.window.detail.get())
        self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),RED)


if __name__ == "__main__":unittest.main()
