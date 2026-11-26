import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ams_recorder import updates
from ams_recorder.configuration import save_configuration
from ams_recorder.gui import GREEN, RED, AMBER, GRAY, RecorderWindow
from ams_recorder.status import RecorderStatus


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
        if self.window.join_thread:self.window.join_thread.join(timeout=6)
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
        with patch("ams_recorder.gui.run",side_effect=recorder), patch("ams_recorder.gui.webbrowser.open") as browser:
            self.window.start()
            limit=time.monotonic()+3
            while time.monotonic()<limit and self.window.title.get()!="Recording in progress":self.root.update();time.sleep(.01)
            self.assertEqual(self.window.title.get(),"Recording in progress")
            self.window.webapp_button.invoke()
            browser.assert_called_once_with('http://127.0.0.1:8787/dashboard')
            self.assertTrue(self.window.worker.is_alive())
            self.window.stop()
            while time.monotonic()<limit and self.window.worker:self.root.update();time.sleep(.01)
            self.assertIsNone(self.window.worker)
            self.assertEqual(self.window.light.itemcget(self.window.bulb,"fill"),GRAY)

    def test_join_lookup_and_launch_keep_recording_and_the_window_running(self):
        self.window.auto_link_attempted = True
        requested, release = threading.Event(), threading.Event()
        join = dict(name='Current lobby', address='198.51.100.21:27015', password=False)

        def lookup(server):
            self.assertEqual(server, 'http://127.0.0.1:8787')
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            requested.set()
            release.wait(3)
            return join

        def recorder(args, stop, on_event):
            on_event('comms_ok')
            stop.wait(5)

        with patch('ams_recorder.gui.run', side_effect=recorder), \
                patch('ams_recorder.gui.current_server', side_effect=lookup) as fetch, \
                patch('ams_recorder.gui.webbrowser.open', return_value=True) as browser:
            self.window.start()
            self.window.join_button.invoke()
            try:
                self.assertTrue(requested.wait(1))
                self.root.update()
                self.window.poll()
                self.assertEqual(str(self.window.join_button.cget('state')), 'disabled')
                self.assertTrue(self.window.worker.is_alive())
                self.assertFalse(self.window.stop_event.is_set())
                self.window.join_server()
                self.assertEqual(fetch.call_count, 1)
            finally:release.set()
            self.window.join_thread.join(timeout=3)
            self.window.poll()
            browser.assert_called_once_with('steam://run/1066890//-connect%20198.51.100.21%3A27015')
            self.assertEqual(str(self.window.join_button.cget('state')), 'normal')
            self.assertTrue(self.window.worker.is_alive())

    def test_private_join_can_cancel_or_launch_without_saving_the_password(self):
        join = dict(name='Private lobby', address='198.51.100.21:27015', password=True)
        original = self.window.config_path.read_bytes()
        with patch('ams_recorder.gui.current_server', return_value=join) as fetch, \
                patch('ams_recorder.gui.webbrowser.open', return_value=True) as browser:
            self.window.join_button.invoke()
            self.window.join_thread.join(timeout=3)
            self.window.poll()
            self.assertEqual(self.window.join_password.cget('show'), '*')
            self.window.join_password.insert(0, 'discard this')
            actions = self.window.join_dialog.winfo_children()[0].winfo_children()[-1]
            actions.winfo_children()[0].invoke()
            self.assertIsNone(self.window.join_dialog)
            self.assertIsNone(self.window.join_password)
            browser.assert_not_called()
            self.window.join_button.invoke()
            self.window.join_thread.join(timeout=3)
            self.window.poll()
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(self.window.join_password.get(), '')
            self.window.join_password.insert(0, 'secret password')
            actions = self.window.join_dialog.winfo_children()[0].winfo_children()[-1]
            actions.winfo_children()[1].invoke()
            from urllib.parse import unquote
            self.assertEqual(unquote(browser.call_args[0][0]),
                             'steam://run/1066890//-connect 198.51.100.21:27015 -password "secret password"')
            self.assertIsNone(self.window.join_dialog)
            self.assertIsNone(self.window.join_password)
            self.assertEqual(self.window.config_path.read_bytes(), original)

    def test_join_failures_are_separate_from_capture_health_and_can_retry(self):
        with patch('ams_recorder.gui.current_server', side_effect=OSError('Offline')), \
                patch.object(self.window, 'join_error') as error:
            self.window.join_button.invoke()
            self.window.join_thread.join(timeout=3)
            self.window.poll()
            error.assert_called_once()
            self.assertFalse(self.window.failed)
            self.assertFalse(self.window.stop_event.is_set())
            self.assertEqual(str(self.window.join_button.cget('state')), 'normal')
        with patch('ams_recorder.gui.webbrowser.open', return_value=False), \
                patch.object(self.window, 'join_error') as error:
            self.window.launch_join(dict(address='198.51.100.21:27015', password=False))
            self.assertIn('Steam and AMS2', error.call_args[0][0])
            self.assertFalse(self.window.failed)

    def test_normal_launch_enables_recording_without_pressing_start(self):
        import tkinter as tk

        config_path = self.path / 'linked.json'
        save_configuration(config_path, dict(self.window.config, account_linked=True))
        root = tk.Toplevel(self.root)
        root.withdraw()
        active = threading.Event()

        def recorder(args, stop, on_event):
            on_event('comms_ok')
            active.set()
            stop.wait(5)

        with patch('ams_recorder.gui.updates.native_target', return_value=self.path / 'recorder.exe'), \
                patch('ams_recorder.gui.updates.check_release', return_value=None) as check, \
                patch('ams_recorder.gui.run', side_effect=recorder) as run:
            window = RecorderWindow(root, config_path, self.path / 'linked-queue.sqlite3')
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and window.title.get() != 'Comms open, awaiting race data':
                    self.root.update()
                    time.sleep(.01)
                check.assert_called_once()
                run.assert_called_once()
                self.assertTrue(active.is_set())
                self.assertEqual(window.title.get(), 'Comms open, awaiting race data')
                self.assertEqual(window.start_button.cget('text'), 'Recording enabled')
                self.assertEqual(str(window.stop_button.cget('state')), 'normal')

                window.stop()
                window.worker.join(timeout=3)
                window.poll()
                self.assertIsNone(window.worker)
                self.assertEqual(window.start_button.cget('text'), 'Start recording')
                self.assertEqual(str(window.start_button.cget('state')), 'normal')
            finally:
                window.stop()
                if window.worker:
                    window.worker.join(timeout=3)
                if window.update_thread:
                    window.update_thread.join(timeout=3)
                root.destroy()

    def test_window_shows_driver_and_status_without_setup_controls_or_colour_legend(self):
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)

        labels = [widget.cget('text') for widget in descendants(self.root) if 'text' in widget.keys()]
        self.assertNotIn('Setup', labels)
        self.assertFalse(any('Green: ready' in label for label in labels))
        self.assertEqual(self.window.driver.get(), 'Alex Morgan')
        self.assertEqual(self.window.config['event_code'], 'TEST')
        self.assertEqual(self.window.config['token'], 'test-token')
        self.window.messages.put(('identity', 'Jordan'))
        self.window.poll()
        self.assertEqual(self.window.driver.get(), 'Jordan')

    def test_unlinked_window_disables_start_and_can_reopen_browser_approval(self):
        window = self.window
        self.assertEqual(window.title.get(), 'Steam link required')
        self.assertEqual(window.start_button.cget('text'), 'Link Steam first')
        self.assertEqual(str(window.start_button.cget('state')), 'disabled')
        self.assertEqual(str(window.link_button.cget('state')), 'normal')
        approval = 'http://127.0.0.1:8787/connect-recorder/' + 'A' * 43
        requested = threading.Event()
        approved = threading.Event()

        def connect(config_path, cancel, notify):
            notify('open_browser', approval)
            notify('link_status', 'Approve this recorder in your browser to enable recording.')
            requested.set()
            if approved.wait(3):
                notify('link_complete', dict(window.config, account_linked=True, account_name='Alex Morgan'))

        with patch('ams_recorder.gui.connect_account', side_effect=connect), \
                patch('ams_recorder.gui.webbrowser.open') as browser:
            window.link_button.invoke()
            self.assertTrue(requested.wait(1))
            window.messages.put(('link_required', None))
            window.poll()
            browser.assert_called_once_with(approval)
            self.assertEqual(window.link_button.cget('text'), 'Open Steam approval')
            self.assertIn('Approve this recorder in your browser', window.account_label.get())
            self.assertEqual(str(window.start_button.cget('state')), 'disabled')
            window.link_button.invoke()
            self.assertEqual(browser.call_count, 2)
            approved.set()
            window.link_thread.join(timeout=3)
            window.poll()
            self.assertEqual(window.account_label.get(), 'Linked to: Alex Morgan')
            self.assertEqual(window.start_button.cget('text'), 'Start recording')
            self.assertEqual(str(window.start_button.cget('state')), 'normal')

    def test_ready_message_and_controls_fit_the_minimum_window(self):
        self.root.geometry('470x500')
        self.root.deiconify()
        self.window.status.update('started')
        self.window.status.update('comms_ok',processing={'frames':12345})
        self.window.poll()
        self.root.update()
        self.assertIn('Queued on PC: 0',self.window.pending.get())
        self.assertIn('Server processing: 12,345',self.window.pending.get())
        title = self.window.title_label
        self.assertGreaterEqual(title.winfo_height(), title.winfo_reqheight())
        bottom = self.window.start_button.winfo_rooty() + self.window.start_button.winfo_height()
        self.assertLessEqual(bottom, self.root.winfo_rooty() + self.root.winfo_height())
        for button in (self.window.webapp_button, self.window.join_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
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
            self.assertEqual(window.driver.get(), 'AMS2 recorder')
            self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in root.winfo_children()))
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
                patch('ams_recorder.gui.updates.check_release',return_value=updates.Release('1.0.0','a'*64,128)), \
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
                patch('ams_recorder.gui.updates.check_release',return_value=updates.Release('1.0.0','a'*64,128)), \
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
                patch('ams_recorder.gui.updates.check_release',return_value=updates.Release('1.0.0','a'*64,128)), \
                patch('ams_recorder.gui.updates.prepare_install',return_value=self.path/'plan.json'), \
                patch('ams_recorder.gui.updates.launch_helper',side_effect=ValueError('Bad plan')), \
                patch('ams_recorder.gui.updates.discard_install',side_effect=ValueError('Bad plan')), \
                patch('ams_recorder.gui.run',side_effect=recorder) as run:
            self.window.startup();self.window.update_thread.join(timeout=3);self.window.poll()
            limit=time.monotonic()+3
            while time.monotonic()<limit and not run.called:self.root.update();time.sleep(.01)
            self.assertTrue(run.called);self.assertFalse(self.window.closing)

    def enable_periodic_updates(self):
        self.now=0
        self.window.status=RecorderStatus(clock=lambda:self.now)
        self.window.update_target=self.path/'recorder.exe'
        self.window.next_release_check=300
        self.window.auto_link_attempted=True
        release=updates.Release('1.0.0','a'*64,128)
        return updates.StagedRelease(release,self.path/'staged')

    def drain_update(self):
        if self.window.update_thread:
            self.window.update_thread.join(timeout=3)
            self.window.poll()

    def test_runtime_download_and_rechecks_do_not_interrupt_recording(self):
        staged=self.enable_periodic_updates()
        plan=self.path/'plan.json'
        active=threading.Event()
        def recorder(args,stop,on_event):
            on_event('comms_ok')
            on_event('captured',decoded=True,consistent=True,game_state=2)
            active.set()
            stop.wait(5)
            on_event('stopped')
        with patch('ams_recorder.gui.run',side_effect=recorder), \
                patch('ams_recorder.gui.updates.check_release',return_value=staged.release) as check, \
                patch('ams_recorder.gui.updates.stage_release',return_value=staged) as download, \
                patch('ams_recorder.gui.updates.prepare_staged_install',return_value=plan) as prepare, \
                patch('ams_recorder.gui.updates.launch_helper') as helper:
            self.window.start()
            self.assertTrue(active.wait(1))
            self.now=299;self.window.tick_updates();check.assert_not_called()
            self.now=300;self.window.tick_updates();self.drain_update()
            self.assertIs(self.window.pending_update,staged)
            self.assertEqual(self.window.status.mode,'recording')
            self.assertFalse(self.window.stop_event.is_set())
            self.assertEqual(self.window.next_install_check,360)
            self.now=360;self.window.tick_updates();prepare.assert_not_called()
            self.now=370;self.window.status.update('captured',decoded=True,consistent=True,game_state=1)
            for stamp in (420,480,540):
                self.now=stamp;self.window.tick_updates()
            self.now=600;self.window.tick_updates();self.drain_update()
            self.assertEqual(check.call_count,2)
            download.assert_called_once()
            self.now=660;self.window.tick_updates();prepare.assert_not_called()
            self.assertFalse(self.window.stop_event.is_set())
            self.now=720;self.window.tick_updates()
            self.assertTrue(self.window.stop_event.is_set())
            helper.assert_not_called()
            self.window.worker.join(timeout=3)
            self.window.poll()
            self.drain_update()
            helper.assert_called_once_with(plan)
            self.assertNotIn('--start-stopped',prepare.call_args.args[3])

    def test_runtime_install_preserves_an_explicitly_stopped_recorder(self):
        staged=self.enable_periodic_updates()
        plan=self.path/'plan.json'
        with patch('ams_recorder.gui.updates.check_release',return_value=staged.release), \
                patch('ams_recorder.gui.updates.stage_release',return_value=staged), \
                patch('ams_recorder.gui.updates.prepare_staged_install',return_value=plan) as prepare, \
                patch('ams_recorder.gui.updates.launch_helper') as helper, \
                patch('ams_recorder.gui.run') as run:
            self.now=300;self.window.tick_updates();self.drain_update()
            self.now=359;self.window.tick_updates();prepare.assert_not_called()
            self.now=360;self.window.tick_updates();self.drain_update()
            self.assertIn('--start-stopped',prepare.call_args.args[3])
            helper.assert_called_once_with(plan)
            run.assert_not_called()

    def test_recovered_recorder_checks_for_newer_releases_without_retrying_failed_release(self):
        failed=self.enable_periodic_updates()
        self.window.failed_update_version=failed.release.version
        newer=updates.StagedRelease(updates.Release('1.0.1','b'*64,128),self.path/'newer')
        with patch('ams_recorder.gui.updates.check_release',side_effect=[failed.release,newer.release]), \
                patch('ams_recorder.gui.updates.stage_release',return_value=newer) as download:
            self.now=300;self.window.tick_updates();self.drain_update()
            download.assert_not_called()
            self.now=600;self.window.tick_updates();self.drain_update()
            self.assertIs(self.window.pending_update,newer)
            self.assertEqual(download.call_args.args[0],newer.release)


if __name__ == "__main__":unittest.main()
