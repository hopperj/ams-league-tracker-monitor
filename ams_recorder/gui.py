"""A small desktop recorder with an acknowledgement-based red/green indicator."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
from types import SimpleNamespace

from .configuration import DEFAULT_SERVER, checked_configuration, connection_values, defaults, load_configuration, save_configuration
from .engine import run
from .spool import Spool
from .status import RecorderStatus, upload_problem

PAPER, INK, MUTED = "#f3f5f4", "#1a222b", "#6d7883"
RED, GREEN, ORANGE = "#c7423a", "#27835b", "#e45c2b"
LOG = logging.getLogger("ams-recorder")


class RecorderWindow:
    def __init__(self, root, config_path=None, spool_path=None, demo=False, auto_start=True):
        import tkinter as tk
        from tkinter import ttk, font
        self.tk, self.ttk, self.root = tk, ttk, root
        default_config, default_spool = defaults()
        self.config_path = Path(config_path or default_config)
        self.spool_path = Path(spool_path or default_spool)
        self.demo = demo
        self.config = None
        self.worker = None
        self.busy = False
        self.closing = False
        self.failed = False
        self.stop_event = threading.Event()
        self.cancel_setup = threading.Event()
        self.messages = queue.Queue()
        self.status = RecorderStatus()
        root.title("AMS League Recorder" + (" — DEMO" if demo else ""))
        root.configure(background=PAPER)
        root.minsize(470, 420)
        root.geometry("540x470")
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
        card = tk.Frame(body, background="white", highlightbackground="#dde2e5", highlightthickness=1, padx=15, pady=20)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        self.light = tk.Canvas(card, width=78, height=82, background="white", highlightthickness=0)
        self.light.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        self.bulb = self.light.create_oval(10, 12, 68, 70, fill=RED, outline="")
        self.light.create_oval(23, 21, 37, 32, fill="#ffffff", outline="", stipple="gray75")
        self.title = tk.StringVar(value="Not recording")
        self.detail = tk.StringVar()
        tk.Label(card, textvariable=self.title, background="white", foreground=INK, font=(family, 21, "bold"), anchor="w").grid(row=0, column=1, sticky="ew")
        self.detail_label = tk.Label(card, textvariable=self.detail, background="white", foreground=MUTED, font=(family, 10), justify="left", anchor="w", wraplength=315)
        self.detail_label.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        card.bind("<Configure>", lambda event: self.detail_label.configure(wraplength=max(200, event.width - 130)))
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
        self.setup_dialog = tk.Toplevel(root)
        self.setup_dialog.withdraw()
        self.setup_dialog.title("Recorder setup")
        self.setup_dialog.configure(background=PAPER)
        self.setup_dialog.geometry("520x435")
        self.setup_dialog.minsize(470, 435)
        self.setup_dialog.transient(root)
        self.setup_dialog.protocol("WM_DELETE_WINDOW", self.hide_setup)
        self.setup_frame = tk.Frame(self.setup_dialog, background=PAPER, padx=22, pady=12)
        self.setup_frame.pack(fill="both", expand=True)
        tk.Label(self.setup_frame, text="RECORDER SETUP", foreground=MUTED, background=PAPER, font=(family, 9, "bold")).pack(anchor="w", pady=(22, 5))
        tk.Label(self.setup_frame, text="Use the upload token and capture code from your league dashboard.", foreground=MUTED, background=PAPER, font=(family, 9), wraplength=470, justify="left").pack(anchor="w", pady=(0, 8))
        self.server = tk.StringVar(value=DEFAULT_SERVER)
        self.token = tk.StringVar()
        self.event_code = tk.StringVar()
        self.entries = []
        for label, variable, masked in (("Server address", self.server, False), ("Recorder upload token", self.token, True), ("Session capture code", self.event_code, False)):
            tk.Label(self.setup_frame, text=label, foreground=INK, background=PAPER, font=(family, 9)).pack(anchor="w", pady=(6, 3))
            entry = ttk.Entry(self.setup_frame, textvariable=variable, show="*" if masked else "")
            entry.pack(fill="x")
            self.entries.append(entry)
        self.save_button = ttk.Button(self.setup_frame, text="Save setup & start", command=lambda: self.save_setup(True))
        self.save_button.pack(anchor="e", pady=(12, 0))
        self.setup_visible = False
        try:
            self.config = load_configuration(self.config_path)
            self.apply_configuration(self.config)
        except FileNotFoundError:
            self.toggle_setup()
        except (OSError, ValueError, KeyError, TypeError):
            self.status.update("fatal", message="Saved setup could not be read. Enter your recorder details again.")
            self.toggle_setup()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.callback_error
        self.poll()
        if self.config and auto_start:
            root.after(150, self.start)

    def apply_configuration(self, config):
        self.server.set(config["server"])
        self.token.set(config["token"])
        self.event_code.set(config["event_code"])
        self.driver.set(config["driver_name"] + "  ·  " + config["event_code"])

    def hide_setup(self):
        self.setup_visible = False
        self.setup_dialog.withdraw()

    def toggle_setup(self):
        if self.setup_visible:
            self.hide_setup()
        elif not self.worker and not self.busy:
            self.setup_visible = True
            self.setup_dialog.deiconify()
            self.setup_dialog.lift()

    def save_setup(self, start_after=False):
        if self.worker or self.busy:
            return
        try:
            values = connection_values(self.server.get(), self.token.get(), self.event_code.get())
        except ValueError as exc:
            self.status.update("fatal", message=str(exc))
            return
        self.busy = True
        self.cancel_setup.clear()
        self.status.update("fatal", message="Checking the recorder credentials with your league server…")

        def check():
            try:
                config = checked_configuration(*values)
                if self.cancel_setup.is_set():
                    self.messages.put(("setup_cancelled", None))
                    return
                queue_file = Spool(self.spool_path)
                try:
                    queue_file.bind(config["server"], config["recorder_id"])
                finally:
                    queue_file.close()
                save_configuration(self.config_path, config)
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
        if self.worker or self.busy or self.closing:
            return
        try:
            values = connection_values(self.server.get(), self.token.get(), self.event_code.get())
        except ValueError as exc:
            self.status.update("fatal", message=str(exc))
            if not self.setup_visible:
                self.toggle_setup()
            return
        if not self.config or values != (self.config["server"], self.config["token"], self.config["event_code"]):
            self.save_setup(True)
            return
        if self.setup_visible:
            self.toggle_setup()
        self.stop_event = threading.Event()
        self.status.update("started")
        args = SimpleNamespace(config=str(self.config_path), spool=str(self.spool_path), hz=60, batch_seconds=2,
                               max_spool_mb=2048, demo=self.demo, demo_offset=91, duration=None,
                               replay=None, udp=False, udp_port=5606, udp_source=None)

        def record():
            try:
                run(args, self.stop_event, self.status.update)
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
        if self.worker:
            self.stop_event.set()
            self.cancel_setup.set()
            self.status.update("stopping")

    def close(self):
        self.closing = True
        self.stop()
        if not self.worker:
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
                if kind == "setup_ok":
                    self.worker, self.busy = None, False
                    self.config, start_after = value
                    self.apply_configuration(self.config)
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
                elif kind == "run_done":
                    self.worker = None
                    self.status.update("stopped")
        except queue.Empty:
            pass
        if self.closing and not self.worker:
            self.root.destroy()
            return
        state = self.status.snapshot()
        self.light.itemconfigure(self.bulb, fill=GREEN if state.green else RED)
        self.title.set(state.title)
        self.detail.set(state.detail)
        self.pending.set("Queued samples: " + str(state.pending))
        self.last_upload.set("Last upload: " + (str(state.last_upload_seconds) + "s ago" if state.last_upload_seconds is not None else "—"))
        locked = self.worker is not None or self.closing
        for widget in [self.start_button, self.settings_button, self.save_button, *self.entries]:
            widget.configure(state="disabled" if locked else "normal")
        self.stop_button.configure(state="normal" if self.worker and not self.closing else "disabled")
        self.root.after(200, self.poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    config, spool = defaults()
    parser.add_argument("--config", default=str(config))
    parser.add_argument("--spool", default=str(spool))
    parser.add_argument("--demo", action="store_true", help="Synthetic data for local testing; not a real race")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    log_path = Path(args.config).parent / "recorder.log"
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(log_path, maxBytes=1024*1024, backupCount=2, encoding="utf-8")])
    import tkinter as tk
    if tk.TkVersion < 8.6:
        raise RuntimeError("The recorder window requires Tk 8.6 or later. Use a current Python installation.")
    root = tk.Tk()
    window = RecorderWindow(root, args.config, args.spool, args.demo, auto_start=not args.smoke_test)
    if args.smoke_test:
        root.withdraw()
        root.after(250, window.close)
    root.mainloop()
    if window.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
