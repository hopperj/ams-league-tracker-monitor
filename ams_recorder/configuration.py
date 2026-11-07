"""Shared configuration for the desktop recorder and command-line tools."""
import json
import os
from pathlib import Path
import re
import tempfile

from .transport import Transport

DEFAULT_SERVER = "https://ams2.ioresearch.ca:5443"


def defaults():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local"))) / "AMSLeagueRecorder"
    return root / "config.json", root / "spool.sqlite3"


def connection_values(server, token, event):
    if not all(isinstance(value, str) for value in (server, token, event)):
        raise ValueError("Enter the server, recorder token, and capture code")
    server, token, event = server.strip(), token.strip(), event.strip().upper()
    if not token:
        raise ValueError("Paste your recorder upload token from the league dashboard")
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", event):
        raise ValueError("Enter the capture code supplied for this session")
    return Transport(server, token).server, token, event


def checked_configuration(server, token, event):
    server, token, event = connection_values(server, token, event)
    info = Transport(server, token).request("/api/recorder")
    if not isinstance(info.get("recorder_id"), str) or not isinstance(info.get("driver_name"), str):
        raise ValueError("The server did not identify this recorder")
    return {"server":server, "token":token, "recorder_id":info["recorder_id"],
            "driver_name":info["driver_name"], "event_code":event}


def save_configuration(path, config):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_configuration(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    server, token, event = connection_values(value["server"], value["token"], value["event_code"])
    if not isinstance(value.get("recorder_id"), str) or not isinstance(value.get("driver_name"), str):
        raise ValueError("Recorder settings are incomplete. Save your setup again.")
    return {**value, "server":server, "token":token, "event_code":event}
