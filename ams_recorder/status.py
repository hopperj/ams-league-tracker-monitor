"""Thread-safe status based on captured frames and committed server acknowledgements."""
from dataclasses import dataclass
import threading
import time
import urllib.error


def upload_problem(error):
    if isinstance(error, urllib.error.HTTPError):
        message=vars(error).get("server_message")
        if message:
            return "Upload not saved: " + message
        return {400:"Check the capture code. The server rejected the upload.",
                401:"Recorder token rejected. Check your setup.",
                403:"The server refused access. Check the server address.",
                409:"A queued session is closed or conflicted. Ask your league administrator to review it.",
                429:"The server is busy. Uploads will retry automatically."}.get(error.code,
                    "The server could not accept the upload. Retrying automatically.")
    if isinstance(error, ValueError):
        return "The server could not confirm this recorder or upload. Check your setup."
    return "Cannot reach the server. Data is saved locally while uploads retry."


def receipt_notice(response):
    """A processing warning must not look like a failed transmission."""
    warnings=response.get("warnings",[])
    if isinstance(warnings,list):
        for warning in warnings:
            if isinstance(warning,dict) and warning.get("severity") in ("warning","error"):
                message=warning.get("message")
                if isinstance(message,str):
                    return "Data saved. " + " ".join(message.split())[:320]
    if response.get("processing_status") in ("needs_review","error","pending"):
        return "Data saved. Check the capture inbox in the league dashboard for processing details."
    return None


@dataclass(frozen=True)
class Indicator:
    green: bool
    title: str
    detail: str
    pending: int
    last_upload_seconds: object


class RecorderStatus:
    def __init__(self, batch_seconds=2, hz=60, clock=time.monotonic):
        self.lock = threading.RLock()
        self.clock = clock
        self.fresh_seconds = max(10, batch_seconds * 3 + 2)
        self.max_pending = max(240, int(hz * batch_seconds * 2))
        self.mode = "idle"
        self.problem = None
        self.capture_error = None
        self.capture_reason = None
        self.upload_error = None
        self.server_notice = None
        self.last_capture = None
        self.last_upload = None
        self.pending = 0

    def update(self, kind, **values):
        with self.lock:
            at = values.get("at", self.clock())
            if kind == "started":
                self.mode = "recording"
                self.problem = self.capture_error = self.capture_reason = self.upload_error = None
                self.last_capture = self.last_upload = None
                self.server_notice = None
            elif kind == "captured":
                self.last_capture = at
                self.capture_error = None
                if not values.get("decoded"):
                    self.capture_reason = "AMS2 data format is unsupported. Raw data is being saved."
                elif not values.get("consistent"):
                    self.capture_reason = "Waiting for a consistent AMS2 snapshot. Raw data is being saved."
                elif values.get("replay"):
                    self.capture_reason = "Replay data is being archived; live race recording is not active."
                elif values.get("game_state") not in (2, 4):
                    self.capture_reason = "Waiting for an AMS2 driving session."
                else:
                    self.capture_reason = None
            elif kind == "capture_wait":
                self.capture_error = values["message"]
            elif kind == "uploaded":
                self.last_upload = at
                self.upload_error = None
                self.server_notice = values.get("review")
            elif kind == "upload_error":
                self.upload_error = values["message"]
            elif kind == "stopping":
                self.mode = "stopping"
            elif kind == "stopped":
                self.mode = "idle"
            elif kind == "fatal":
                self.problem = values["message"]
            if "pending" in values:
                self.pending = values["pending"]

    def snapshot(self):
        with self.lock:
            now = self.clock()
            age = None if self.last_upload is None else max(0, int(now - self.last_upload))
            title, green = "Not transmitting", False
            if self.problem:
                detail = self.problem
            elif self.mode == "stopping":
                title, detail = "Stopping", "Finishing the current request. Queued data stays saved."
            elif self.mode == "idle":
                title = "Not recording"
                detail = "Press Start recording when you are ready."
                if self.pending:
                    detail = "Queued data is saved. Start recording again to resume uploads."
                elif self.server_notice:
                    detail = self.server_notice
            elif self.upload_error:
                detail = self.upload_error
            elif self.server_notice:
                title,detail="Data saved — review",self.server_notice
            elif self.capture_error:
                detail = self.capture_error
            elif self.capture_reason:
                detail = self.capture_reason
            elif self.last_capture is None:
                detail = "Waiting for AMS2. Enable Shared Memory: Project CARS 2."
            elif now - self.last_capture > self.fresh_seconds:
                detail = "Waiting for new AMS2 data. Resume your driving session."
            elif self.last_upload is None:
                detail = "Waiting for the server to confirm the first upload."
            elif now - self.last_upload > self.fresh_seconds:
                detail = "No recent upload confirmation. Check the connection; data stays saved."
            elif self.pending > self.max_pending:
                detail = "Catching up on saved data. Keep the recorder running."
            else:
                green, title = True, "Transmitting"
                detail = "AMS2 data is being received by the league server."
            return Indicator(green, title, detail, self.pending, age)
