"""Separate communication readiness, active recording, and errors."""
from dataclasses import dataclass
import threading
import time
import urllib.error


AMBER_AFTER_SECONDS = 3


def upload_problem(error):
    if isinstance(error, urllib.error.HTTPError):
        message=vars(error).get("server_message")
        if message:
            return "Upload not saved: " + message
        return {400:"Check the capture code. The server rejected the upload.",
                401:"Recorder connection was not recognized. Saved data will retry; contact your league administrator if it continues.",
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
    if response.get("processing_status") in ("needs_review","error"):
        return "Data saved. Check the capture inbox in the league dashboard for processing details."
    return None


@dataclass(frozen=True)
class Indicator:
    color: str
    title: str
    detail: str
    pending: int
    last_upload_seconds: object
    last_comms_seconds: object

    @property
    def green(self):
        return self.color == 'green'


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
        self.snapshot_retry = None
        self.upload_error = None
        self.comms_error = None
        self.link_required = False
        self.server_notice = None
        self.last_capture = None
        self.last_upload = None
        self.last_comms = None
        self.pending = 0
        self.attention_since = None
        self.healthy_color = 'gray'

    def update(self, kind, **values):
        with self.lock:
            at = values.get("at", self.clock())
            if kind == "started":
                self.mode = "recording"
                self.problem = self.capture_error = self.capture_reason = self.upload_error = None
                self.snapshot_retry = None
                self.comms_error = None
                self.link_required = False
                self.last_comms = None
                self.last_capture = self.last_upload = None
                self.server_notice = None
            elif kind == 'updating':
                self.mode = 'updating'
            elif kind == "captured":
                self.last_capture = None
                self.capture_error = None
                self.snapshot_retry = None
                if not values.get("decoded"):
                    self.capture_reason = "AMS2 data format is unsupported. Raw data is being saved."
                elif not values.get("consistent"):
                    self.capture_reason = "Waiting for a consistent AMS2 snapshot. Raw data is being saved."
                elif values.get("replay"):
                    self.capture_reason = "Replay data is being archived; live race recording is not active."
                elif values.get("game_state") not in (2, 4):
                    self.capture_reason = None
                else:
                    self.capture_reason = None
                    self.last_capture = at
            elif kind == "capture_wait":
                self.snapshot_retry = None
                self.capture_error = None if values.get('waiting') else values['message']
                if values.get('waiting'):
                    self.capture_reason = None
                self.last_capture = None
            elif kind == "snapshot_retry":
                self.snapshot_retry = values["message"]
            elif kind == "snapshot_read":
                self.snapshot_retry = None
            elif kind == 'comms_ok':
                self.last_comms = at
                self.comms_error = None
                self.link_required = False
            elif kind == 'comms_error':
                self.comms_error = values['message']
            elif kind == 'link_required':
                self.link_required = True
                self.comms_error = values['message']
                self.last_capture = None
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
            # A recovery between UI polls must reset the warning delay.
            self._track_attention(at)

    def _attention(self, now):
        if self.problem:
            return 'Recorder error', self.problem
        if self.mode != 'recording':
            return None
        if self.link_required:
            return 'Steam link required', self.comms_error
        if self.upload_error:
            return 'Upload problem', self.upload_error
        if self.comms_error:
            return 'Connection problem', self.comms_error
        if self.last_comms is None:
            return None
        if now - self.last_comms > self.fresh_seconds:
            return 'Connection problem', 'Upload access has not been confirmed recently. Retrying the connection.'
        if self.server_notice:
            return 'Data saved — review', self.server_notice
        capture_problem = self.capture_error or self.capture_reason or self.snapshot_retry
        if capture_problem:
            return 'Capture problem', capture_problem
        if self.pending and self.last_upload is not None and now - self.last_upload > self.fresh_seconds:
            return 'Upload problem', 'No recent upload confirmation. Check the connection; data stays saved.'
        if self.pending > self.max_pending:
            return 'Catching up on saved data', 'Catching up on saved data. Keep the recorder running.'
        return None

    def _track_attention(self, now):
        attention = self._attention(now)
        if attention is None:
            self.attention_since = None
            if self.mode != 'recording' or self.last_comms is None:
                self.healthy_color = 'gray'
            elif self.last_capture is not None and now - self.last_capture <= self.fresh_seconds:
                self.healthy_color = 'red'
            else:
                self.healthy_color = 'green'
        elif self.attention_since is None:
            self.attention_since = now
        return attention

    def snapshot(self):
        with self.lock:
            now = self.clock()
            age = None if self.last_upload is None else max(0, int(now - self.last_upload))
            comms_age = None if self.last_comms is None else max(0, int(now - self.last_comms))
            attention = self._track_attention(now)
            color = self.healthy_color
            if attention:
                title, detail = attention
                if now - self.attention_since >= AMBER_AFTER_SECONDS:
                    color = 'amber'
                elif self.mode != 'recording':
                    color = 'gray'
            elif self.mode == "stopping":
                title, detail = "Stopping", "Finishing the current request. Queued data stays saved."
            elif self.mode == 'updating':
                title = 'Checking for updates'
                detail = 'Recording starts after the update check. Skip it to use the installed version.'
            elif self.mode == "idle":
                title = "Not recording"
                detail = "Press Start recording when you are ready."
                if self.pending:
                    detail = "Queued data is saved. Start recording again to resume uploads."
                elif self.server_notice:
                    detail = self.server_notice
            elif self.last_comms is None:
                title = 'Checking connection'
                detail = 'Verifying Steam link and upload access with the league server.'
            elif color == 'red':
                title = 'Recording in progress'
                detail = 'Race data is being recorded and sent to the league server.'
                if self.last_upload is None:
                    detail = 'Race data is saved locally; awaiting the first upload confirmation.'
            else:
                title = 'Comms open, awaiting race data'
                detail = 'Steam account linked. Upload access verified. Waiting for an AMS2 driving session.'
            return Indicator(color, title, detail, self.pending, age, comms_age)
