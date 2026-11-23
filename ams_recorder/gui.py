"""Steam-linked recording with distinct ready, recording, and error indicators."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import webbrowser
from types import SimpleNamespace

from .configuration import DEFAULT_SERVER, connection_values, defaults, prepare_configuration
from . import __version__, updates
from .engine import run
from .enrollment import prepare_enrolled, connect as connect_account
from .status import RecorderStatus, upload_problem

PAPER, INK, MUTED = "#f3f5f4", "#1a222b", "#6d7883"
RED, GREEN, ORANGE = "#c7423a", "#27835b", "#e45c2b"
AMBER, GRAY = '#b57800', '#7c8792'
LIGHT_COLORS = {'red': RED, 'green': GREEN, 'amber': AMBER, 'gray': GRAY}
LOG = logging.getLogger("ams-recorder")


class RecorderWindow:
    def __init__(self, root, config_path=None, spool_path=None, demo=False, auto_start=True,
                 check_updates=True, background_updates=True, failed_update_version=None):
        import tkinter as tk
        from tkinter import ttk, font
        self.tk, self.ttk, self.root = tk, ttk, root
        default_config, default_spool = defaults()
        self.config_path = Path(config_path or default_config)
        self.spool_path = Path(spool_path or default_spool)
        self.demo = demo
        self.config = None
        self.worker = None
        self.link_thread = None
        self.approval_url = None
        self.account_linked = False
        self.auto_link_attempted = False
        self.update_thread = None
        self.update_cancel = threading.Event()
        self.start_after_update = auto_start
        self.startup_update = False
        self.installing_update = False
        self.resume_after_update = False
        self.failed_update_version = failed_update_version
        self.pending_update = None
        self.next_install_check = None
        self.link_cancel = threading.Event()
        self.busy = False
        self.closing = False
        self.failed = False
        self.stop_event = threading.Event()
        self.cancel_setup = threading.Event()
        self.messages = queue.Queue()
        self.status = RecorderStatus()
        self.update_target = updates.native_target() if background_updates else None
        self.next_release_check = self.status.clock() + updates.CHECK_INTERVAL if self.update_target else None
        root.title("AMS League Recorder" + (" — DEMO" if demo else ""))
        root.configure(background=PAPER)
        root.minsize(470, 530)
        root.geometry("540x580")
        family = font.nametofont("TkDefaultFont").actual("family")
        self.family = family
        root.option_add("*Font", (family, 11))
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TButton", padding=(13, 9), font=(family, 10))
        style.configure("Start.TButton", foreground="white", background=ORANGE)
        style.map("Start.TButton", background=[("active", "#ce4c20"), ("disabled", "#c7cbc8")])
        style.configure("TEntry", padding=7)
        header = tk.Frame(root, background="#171c21", padx=22, pady=17)
        header.pack(fill="x")
        tk.Label(header, text="AMS", font=(family, 16, "bold"), foreground=ORANGE, background="#171c21").pack(side="left")
        tk.Label(header, text="  LEAGUE RECORDER", font=(family, 11), foreground="white", background="#171c21").pack(side="left")
        body = tk.Frame(root, background=PAPER, padx=22, pady=20)
        body.pack(fill="both", expand=True)
        identity = tk.Frame(body, background=PAPER)
        identity.pack(fill="x", pady=(0, 16))
        identity.columnconfigure(0, weight=1)
        self.driver = tk.StringVar(value="Connect your recorder")
        tk.Label(identity, textvariable=self.driver, foreground=INK, background=PAPER, anchor="w", justify="left", wraplength=330, font=(family, 11, "bold")).grid(row=0, column=0, sticky="ew")
        self.settings_button = ttk.Button(identity, text="Setup", command=self.toggle_setup)
        self.settings_button.grid(row=0, column=1, padx=(12, 0))
        account_row=tk.Frame(body,background=PAPER);account_row.pack(fill="x",pady=(0,12))
        self.account_label=tk.StringVar(value="Link Steam to enable recording")
        tk.Label(account_row,textvariable=self.account_label,background=PAPER,foreground=MUTED,font=(family,9),wraplength=310,anchor="w",justify="left").pack(side="left",fill="x",expand=True)
        self.link_button=ttk.Button(account_row,text="Link Steam account",command=self.link_account);self.link_button.pack(side="right")
        card = tk.Frame(body, background="white", highlightbackground="#dde2e5", highlightthickness=1, padx=15, pady=20)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        self.light = tk.Canvas(card, width=78, height=82, background="white", highlightthickness=0)
        self.light.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        self.bulb = self.light.create_oval(10, 12, 68, 70, fill=GRAY, outline="")
        self.light.create_oval(23, 21, 37, 32, fill="#ffffff", outline="", stipple="gray75")
        self.title = tk.StringVar(value="Not recording")
        self.detail = tk.StringVar()
        self.title_label = tk.Label(card, textvariable=self.title, background="white", foreground=INK, font=(family, 17, "bold"), anchor="w", justify='left', wraplength=315)
        self.title_label.grid(row=0, column=1, sticky="ew")
        self.detail_label = tk.Label(card, textvariable=self.detail, background="white", foreground=MUTED, font=(family, 10), justify="left", anchor="w", wraplength=315)
        self.detail_label.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        def resize_status(event):
            width = max(150, event.width - 130)
            self.detail_label.configure(wraplength=width)
            self.title_label.configure(wraplength=width)
        card.bind("<Configure>", resize_status)
        tk.Label(body, text='Green: ready  ·  Red: recording  ·  Amber: sustained issue',
                 foreground=MUTED, background=PAPER, font=(family, 9)).pack(anchor='w', pady=(8, 0))
        metrics = tk.Frame(body, background=PAPER)
        metrics.pack(fill="x", pady=(11, 15))
        self.last_upload = tk.StringVar(value="Last upload: —")
        self.pending = tk.StringVar(value="Queued samples: 0")
        for variable, side in ((self.last_upload, "left"), (self.pending, "right")):
            tk.Label(metrics, textvariable=variable, background=PAPER, foreground=MUTED, font=(family, 9)).pack(side=side)
        if demo:
            tk.Label(body, text="DEMO — synthetic data only", foreground=ORANGE, background=PAPER, font=(family, 10, "bold")).pack(anchor="w", pady=(0, 10))
        actions = tk.Frame(body, background=PAPER)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Start recording", style="Start.TButton", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(5, 0))
        self.update_text = tk.StringVar(value='Recorder v'+__version__)
        tk.Label(body, textvariable=self.update_text, foreground=MUTED, background=PAPER,
                 font=(family, 9), wraplength=440, anchor='w', justify='left').pack(fill='x', pady=(10, 0))
        self.setup_dialog = tk.Toplevel(root)
        self.setup_dialog.withdraw()
        self.setup_dialog.title("Recorder setup")
        self.setup_dialog.configure(background=PAPER)
        self.setup_dialog.geometry("520x355")
        self.setup_dialog.minsize(470, 355)
        self.setup_dialog.transient(root)
        self.setup_dialog.protocol("WM_DELETE_WINDOW", self.hide_setup)
        self.setup_frame = tk.Frame(self.setup_dialog, background=PAPER, padx=22, pady=12)
        self.setup_frame.pack(fill="both", expand=True)
        tk.Label(self.setup_frame, text="RECORDER SETUP", foreground=MUTED, background=PAPER, font=(family, 9, "bold")).pack(anchor="w", pady=(22, 5))
        tk.Label(self.setup_frame, text="These settings are optional. Leave the default server address and AUTO to capture all driving. For Human/AI labels, enable Project CARS 2 UDP and a nonzero UDP frequency in AMS2.", foreground=MUTED, background=PAPER, font=(family, 9), wraplength=470, justify="left").pack(anchor="w", pady=(0, 8))
        self.server = tk.StringVar(value=DEFAULT_SERVER)
        self.event_code = tk.StringVar(value="AUTO")
        self.entries = []
        for label, variable, masked in (("Server address", self.server, False), ("Capture code (optional)", self.event_code, False)):
            tk.Label(self.setup_frame, text=label, foreground=INK, background=PAPER, font=(family, 9)).pack(anchor="w", pady=(6, 3))
            entry = ttk.Entry(self.setup_frame, textvariable=variable, show="*" if masked else "")
            entry.pack(fill="x")
            self.entries.append(entry)
        self.save_button = ttk.Button(self.setup_frame, text="Save setup & start", command=lambda: self.save_setup(True))
        self.save_button.pack(anchor="e", pady=(12, 0))
        self.setup_visible = False
        try:
            self.config, enrollment_notice = prepare_enrolled(self.config_path, self.spool_path)
            self.apply_configuration(self.config)
            self.show_saved_account(self.config)
            if enrollment_notice:self.account_label.set(enrollment_notice)
        except (OSError, ValueError, KeyError, TypeError):
            self.status.update("fatal", message="Saved setup could not be read. Restore the original config.json so queued data keeps its recorder connection.")
            self.toggle_setup()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.callback_error
        if self.config and auto_start:
            if check_updates:
                self.startup()
            else:
                self.start()
        self.poll()

    def startup(self):
        if self.worker or self.busy or self.closing or self.update_thread:return
        if self.link_thread and self.link_thread.is_alive():
            self.start()
            return
        target = self.update_target or updates.native_target()
        if target is None:
            self.start()
            return
        self.update_target = target
        self.next_release_check = self.status.clock() + updates.CHECK_INTERVAL
        self.start_after_update = True
        self.startup_update = True
        self.status.update('updating')
        self.check_for_updates()

    def check_for_updates(self):
        startup = self.startup_update
        pending = self.pending_update
        self.update_text.set('Checking for recorder updates…')
        def check():
            folder = self.config_path.parent / 'updates'
            updates.cleanup(folder)
            release = updates.check_release()
            updates.cancelled(self.update_cancel)
            if release and release.version == self.failed_update_version:
                self.update_progress('Update to recorder v'+release.version+' failed. Using recorder v'+__version__+'.')
            elif startup and release:
                plan = updates.prepare_install(release, self.update_target, folder, self.update_cancel,
                    self.update_progress, restart_args=self.update_restart_args())
                self.messages.put(('update_ready', plan))
            elif release and pending and release == pending.release:
                self.update_progress(self.pending_update_message(pending))
            elif release:
                downloaded = updates.stage_release(release, folder, self.update_cancel, self.update_progress)
                self.messages.put(('update_staged', downloaded))
            else:
                self.messages.put(('update_staged', None))
                self.update_progress('Recorder v'+__version__+' · Up to date')
        if startup:
            failure = 'Could not update; continuing with recorder v'+__version__
        elif pending:
            failure = 'Could not check for updates. '+self.pending_update_message(pending)
        else:
            failure = 'Could not check for recorder updates; retrying in 5 minutes.'
        self.run_update_task(check, failure)

    def update_progress(self, text):
        self.messages.put(('update_status', text))

    def pending_update_message(self, downloaded):
        return 'Recorder v'+downloaded.release.version+' downloaded. Waiting for 5 minutes without recording.'

    def update_restart_args(self, *, start_stopped=False):
        args = [arg for arg in sys.argv[1:] if arg not in ('--skip-update', '--start-stopped')
                and not arg.startswith('--failed-update-version=')]
        if start_stopped:
            args.append('--start-stopped')
        return args

    def run_update_task(self, task, failure):
        self.update_cancel.clear()
        def work():
            try:
                task()
            except updates.UpdateCancelled:
                self.update_progress('Recorder v'+__version__+' · Update skipped')
            except Exception as error:
                LOG.warning('Recorder update unavailable (%s); installed version retained', type(error).__name__)
                self.update_progress(failure)
            finally:
                self.messages.put(('update_done', None))
        self.update_thread = threading.Thread(target=work, name='recorder-update', daemon=True)
        self.update_thread.start()

    def tick_updates(self):
        if self.closing or self.installing_update or self.update_target is None:
            return
        now = self.status.clock()
        if self.next_install_check is not None and now >= self.next_install_check:
            self.next_install_check = now + updates.INSTALL_CHECK_INTERVAL
            self.install_pending_update()
        if not self.installing_update and self.next_release_check is not None and now >= self.next_release_check:
            self.next_release_check = now + updates.CHECK_INTERVAL
            if self.update_thread is None:
                self.check_for_updates()

    def install_pending_update(self):
        if (self.pending_update is None or self.update_thread or self.busy or self.setup_visible
                or (self.link_thread and self.link_thread.is_alive())):
            return
        # The capture worker holds this same lock while saving and reporting
        # a frame. Claim the idle window and request its normal shutdown together.
        with self.status.lock:
            if not self.status.recording_idle_for(updates.RECORDING_IDLE_SECONDS):
                return
            self.installing_update = True
            self.resume_after_update = self.worker is not None and not self.stop_event.is_set()
            if self.worker:
                self.stop_event.set()
                self.status.update('stopping')
        self.update_text.set('Preparing the downloaded recorder update…')
        if not self.worker:
            self.prepare_pending_update()

    def prepare_pending_update(self):
        downloaded, self.pending_update = self.pending_update, None
        self.next_install_check = None
        restart_args = self.update_restart_args(start_stopped=not self.resume_after_update)
        def prepare():
            plan = updates.prepare_staged_install(downloaded,self.update_target,self.update_cancel,restart_args)
            self.messages.put(('update_ready', plan))
        self.run_update_task(prepare,'Could not install update; using recorder v'+__version__)

    def discard_update(self, plan):
        try:updates.discard_install(plan)
        except (OSError, ValueError, KeyError, TypeError):
            LOG.warning('Could not clean up the skipped update; installed recorder retained')

    def link_account(self):
        if self.closing or self.startup_update or self.installing_update or not self.config:
            return
        if self.link_thread:
            if self.approval_url:
                webbrowser.open(self.approval_url)
            return
        self.auto_link_attempted = True
        self.link_cancel.clear()
        self.account_label.set("Connecting to the league server…")
        self.link_button.configure(state="disabled")
        def work():
            try:connect_account(self.config_path,self.link_cancel,lambda kind,value:self.messages.put((kind,value)))
            finally:self.messages.put(('link_done',None))
        self.link_thread=threading.Thread(target=work,name="steam-account",daemon=True);self.link_thread.start()

    def show_saved_account(self, config):
        self.account_linked = bool(config.get('account_linked'))
        if self.account_linked:
            self.account_label.set('Linked to: '+config.get('account_name', 'Steam racer'))
        else:
            self.account_label.set('Link Steam to enable recording')

    def apply_configuration(self, config):
        self.server.set(config["server"])
        self.event_code.set(config["event_code"])
        name = config['driver_name'] if config['driver_name'] != 'Waiting for AMS2' else 'AMS2 recorder'
        self.driver.set(name + "  ·  " + ("All sessions (AUTO)" if config["event_code"]=="AUTO" else config["event_code"]))

    def hide_setup(self):
        self.setup_visible = False
        self.setup_dialog.withdraw()

    def toggle_setup(self):
        if self.setup_visible:
            self.hide_setup()
        elif not self.worker and not self.busy and not self.startup_update and not self.installing_update:
            self.setup_visible = True
            self.setup_dialog.deiconify()
            self.setup_dialog.lift()

    def save_setup(self, start_after=False):
        if self.worker or self.busy or self.startup_update or self.installing_update:
            return
        try:
            values = connection_values(self.server.get(), 'automatic', self.event_code.get())
        except ValueError as exc:
            self.status.update("fatal", message=str(exc))
            return
        self.busy = True
        self.cancel_setup.clear()
        self.status.update("fatal", message="Saving recorder setup…")

        def check():
            try:
                config = prepare_configuration(self.config_path, self.spool_path, values[0], values[2])
                if self.cancel_setup.is_set():
                    self.messages.put(("setup_cancelled", None))
                    return
                self.messages.put(("setup_ok", (config, start_after)))
            except ValueError:
                self.messages.put(("setup_error", "Check your setup. Saved data must stay with its original recorder and server."))
            except OSError as exc:
                self.messages.put(("setup_error", upload_problem(exc)))
            except Exception as exc:
                LOG.error("Recorder setup failed (%s)", type(exc).__name__)
                self.messages.put(("setup_error", "Setup could not be saved. Check the server details and available disk space."))

        self.worker = threading.Thread(target=check, name="recorder-setup", daemon=True)
        self.worker.start()

    def start(self):
        if self.closing or self.installing_update:return
        if self.startup_update:
            self.start_after_update = True
            self.update_cancel.set()
            self.update_text.set('Skipping update; starting installed recorder…')
            return
        if self.worker or self.busy or self.closing:
            return
        try:
            values = connection_values(self.server.get(), 'automatic', self.event_code.get())
        except ValueError as exc:
            self.status.update("fatal", message=str(exc))
            if not self.setup_visible:
                self.toggle_setup()
            return
        if not self.config or (values[0], values[2]) != (self.config['server'], self.config['event_code']):
            self.save_setup(True)
            return
        if self.setup_visible:
            self.toggle_setup()
        self.stop_event = threading.Event()
        self.status.update("started")
        args = SimpleNamespace(config=str(self.config_path), spool=str(self.spool_path), hz=60, batch_seconds=2,
                               max_spool_mb=2048, demo=self.demo, demo_offset=91, duration=None,
                               replay=None, udp=False, udp_detection=True, udp_port=5606, udp_source=None,
                               capture_lock=self.status.lock)

        def record():
            try:
                def progress(kind, **values):
                    if kind == 'identity':
                        self.messages.put(('identity', values['driver_name']))
                    else:
                        self.status.update(kind, **values)
                        if kind == 'link_required':
                            self.messages.put(('link_required', None))
                        elif kind == 'comms_ok':
                            self.messages.put(('account_verified', values.get('account_name')))
                run(args, self.stop_event, progress)
            except Exception as exc:
                LOG.error("Recording stopped (%s)", type(exc).__name__)
                message = "Recording stopped. Check your setup and available disk space."
                if os.name != "nt" and not self.demo:
                    message = "Live AMS2 recording requires 64-bit Windows."
                self.messages.put(("run_error", message))
            finally:
                self.messages.put(("run_done", None))

        self.worker = threading.Thread(target=record, name="recorder", daemon=True)
        self.worker.start()

    def stop(self):
        if self.startup_update:
            self.start_after_update = False
            self.update_cancel.set()
        if self.worker:
            self.stop_event.set()
            self.cancel_setup.set()
            self.status.update("stopping")

    def close(self):
        self.closing = True
        self.link_cancel.set()
        self.update_cancel.set()
        if self.pending_update:
            updates.discard_release(self.pending_update)
            self.pending_update = None
        self.stop()
        if not self.worker and not self.update_thread and not (self.link_thread and self.link_thread.is_alive()):
            self.root.destroy()

    def callback_error(self, kind, error, traceback):
        self.failed = True
        LOG.error("Recorder window error (%s)", kind.__name__)
        self.stop()
        self.status.update("fatal", message="The recorder encountered a problem. Restart the app; queued data is saved.")

    def poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == 'update_status':
                    self.update_text.set(value)
                elif kind == 'update_staged':
                    if self.closing or self.update_cancel.is_set():
                        if value:updates.discard_release(value)
                    else:
                        if self.pending_update:updates.discard_release(self.pending_update)
                        self.pending_update = value
                        self.next_install_check = self.status.clock()+updates.INSTALL_CHECK_INTERVAL if value else None
                        if value:self.update_text.set(self.pending_update_message(value))
                elif kind == 'update_ready':
                    if self.closing or self.update_cancel.is_set() or self.worker:
                        self.discard_update(value)
                    else:
                        try:
                            updates.launch_helper(value)
                        except Exception as error:
                            self.discard_update(value)
                            LOG.warning('Recorder update helper unavailable (%s)', type(error).__name__)
                            self.update_text.set('Could not install update; using recorder v'+__version__)
                        else:
                            self.update_text.set('Installing recorder update; restarting…')
                            self.close()
                elif kind == 'update_done':
                    self.update_thread = None
                    if self.startup_update:
                        self.startup_update = False
                        self.status.update('stopped')
                        if self.start_after_update and not self.closing:self.start()
                    elif self.installing_update:
                        self.installing_update = False
                        if self.resume_after_update and not self.closing:self.start()
                elif kind == 'open_browser':
                    if not self.closing:
                        self.approval_url = value
                        webbrowser.open(value)
                elif kind == 'link_status':
                    self.account_label.set(value)
                elif kind == 'link_complete':
                    self.config=value;self.apply_configuration(value)
                    self.show_saved_account(value)
                elif kind == 'link_required':
                    if self.account_linked:
                        self.account_label.set('Steam link required before recording')
                    self.account_linked = False
                    if not self.auto_link_attempted:self.link_account()
                elif kind == 'account_verified':
                    self.account_linked = True
                    self.account_label.set('Linked to: '+value if value else 'Steam account linked')
                elif kind == 'link_done':
                    self.link_thread = self.approval_url = None
                elif kind == "setup_ok":
                    self.worker, self.busy = None, False
                    self.config, start_after = value
                    self.apply_configuration(self.config)
                    self.show_saved_account(self.config)
                    self.status = RecorderStatus()
                    if not self.closing and not self.cancel_setup.is_set() and start_after:
                        self.start()
                elif kind in ("setup_error", "setup_cancelled"):
                    self.worker, self.busy = None, False
                    self.status = RecorderStatus()
                    if value:
                        self.status.update("fatal", message=value)
                elif kind == "run_error":
                    self.status.update("fatal", message=value)
                elif kind == 'identity' and value and value != 'Waiting for AMS2':
                    self.config['driver_name'] = value
                    self.apply_configuration(self.config)
                elif kind == "run_done":
                    self.worker = None
                    self.status.update("stopped")
                    if self.installing_update and not self.closing:
                        self.prepare_pending_update()
        except queue.Empty:
            pass
        if self.closing and not self.worker and not self.update_thread and not (self.link_thread and self.link_thread.is_alive()):
            self.root.destroy()
            return
        self.tick_updates()
        state = self.status.snapshot()
        self.light.itemconfigure(self.bulb, fill=LIGHT_COLORS[state.color])
        if state.title == 'Not recording' and self.config and not self.account_linked:
            self.title.set('Steam link required')
            self.detail.set('Link this recorder to your Steam account to enable recording.')
        else:
            self.title.set(state.title)
            self.detail.set(state.detail)
        self.pending.set("Queued samples: " + str(state.pending))
        self.last_upload.set('Comms: '+(str(state.last_comms_seconds)+'s ago' if state.last_comms_seconds is not None else '—')+
                             ' · Upload: '+(str(state.last_upload_seconds)+'s ago' if state.last_upload_seconds is not None else '—'))
        updating = self.startup_update or self.installing_update
        locked = self.worker is not None or self.closing or updating
        for widget in [self.settings_button, self.save_button, *self.entries]:
            widget.configure(state="disabled" if locked else "normal")
        self.save_button.configure(text='Save setup & start' if self.account_linked else 'Save setup')
        self.link_button.configure(text='Open Steam approval' if self.approval_url else 'Link Steam account',
            state='disabled' if updating or self.closing or not self.config or
            (self.link_thread and not self.approval_url) else 'normal')
        start_label = 'Start recording' if self.account_linked else 'Link Steam first'
        if self.startup_update:
            start_label = 'Skip update & start' if self.account_linked else 'Skip update'
        elif self.account_linked and self.worker and not self.busy and not self.stop_event.is_set():
            start_label = 'Recording enabled'
        self.start_button.configure(text=start_label,
            state='disabled' if self.worker or self.closing or self.installing_update or
            (not self.account_linked and not self.startup_update) else 'normal')
        self.stop_button.configure(state="normal" if (self.worker or self.startup_update) and not (self.closing or self.installing_update) else "disabled")
        self.root.after(200, self.poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    config, spool = defaults()
    parser.add_argument("--config", default=str(config))
    parser.add_argument("--spool", default=str(spool))
    parser.add_argument("--demo", action="store_true", help="Synthetic data for local testing; not a real race")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument('--skip-update', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--start-stopped', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--failed-update-version', help=argparse.SUPPRESS)
    parser.add_argument('--smoke-result', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.smoke_result and not args.smoke_test:parser.error('--smoke-result requires --smoke-test')
    log_path = Path(args.config).parent / "recorder.log"
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(log_path, maxBytes=1024*1024, backupCount=2, encoding="utf-8")])
    import tkinter as tk
    if tk.TkVersion < 8.6:
        raise RuntimeError("The recorder window requires Tk 8.6 or later. Use a current Python installation.")
    root = tk.Tk()
    window = RecorderWindow(root, args.config, args.spool, args.demo, auto_start=not (args.smoke_test or args.start_stopped),
                            check_updates=not args.skip_update, background_updates=not args.smoke_test,
                            failed_update_version=args.failed_update_version)
    if args.smoke_test:
        root.withdraw()
        root.after(250, window.close)
    root.mainloop()
    if window.failed:
        raise SystemExit(1)
    if args.smoke_result:
        Path(args.smoke_result).write_text(json.dumps(dict(complete=True,version=__version__)))


if __name__ == "__main__":
    main()
