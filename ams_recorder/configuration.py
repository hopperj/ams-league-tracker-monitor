"""Shared configuration for the desktop recorder and command-line tools."""
import json
import os
from pathlib import Path
import re
import tempfile
import secrets
import uuid

from .transport import DEFAULT_SERVER, Transport, canonical_server


def defaults():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local"))) / "AMSLeagueRecorder"
    return root / "config.json", root / "spool.sqlite3"


def connection_values(server, token, event):
    if not all(isinstance(value, str) for value in (server, token, event)):
        raise ValueError("Enter the server and recorder token")
    server, token, event = server.strip(), token.strip(), event.strip().upper() or "AUTO"
    if not token:
        raise ValueError("Paste your recorder upload token from the league dashboard")
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", event):
        raise ValueError("Use AUTO, leave the capture code blank, or enter the code supplied for a specific session")
    return Transport(server, token).server, token, event


def checked_configuration(server, token, event):
    server, token, event = connection_values(server, token, event)
    info = Transport(server, token).request("/api/recorder")
    if not isinstance(info.get("recorder_id"), str) or not isinstance(info.get("driver_name"), str):
        raise ValueError("The server did not identify this recorder")
    return {"server":server, "token":token, "recorder_id":info["recorder_id"],
            "driver_name":info["driver_name"], "event_code":event}


def save_configuration(path, config, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        if exclusive:
            try:
                # The fully written identity becomes visible atomically. A second
                # first-launch window reuses the winner instead of replacing it.
                os.link(temporary, path)
            except FileExistsError:
                return False
        else:
            os.replace(temporary, path)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_configuration(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    server, token, event = connection_values(value["server"], value["token"], value["event_code"])
    if not isinstance(value.get("recorder_id"), str) or not isinstance(value.get("driver_name"), str):
        raise ValueError("Recorder settings are incomplete. Save your setup again.")
    return {**value, "server":server, "token":token, "event_code":event}


def prepare_configuration(path, spool_path, server=None, event=None):
    """Save a durable installation identity before capture or any network request."""
    from .spool import Spool
    try:
        previous = load_configuration(path)
    except FileNotFoundError:
        previous = None
    server = server if server is not None else previous['server'] if previous else DEFAULT_SERVER
    event = event if event is not None else previous['event_code'] if previous else 'AUTO'
    server, _, event = connection_values(server, 'automatic', event)
    server = canonical_server(server)
    if previous and canonical_server(previous['server']) == server:
        config = {**previous, 'server': server, 'event_code': event}
    else:
        config = dict(server=server, event_code=event, token=secrets.token_urlsafe(32),
                      recorder_id=str(uuid.uuid4()), driver_name='Waiting for AMS2', auto_registration=True)
    spool = Spool(spool_path)
    try:
        try:
            spool.check_destination(server, config['recorder_id'])
        except ValueError:
            if previous is not None or not Path(path).exists():
                raise
            config = load_configuration(path)
            if canonical_server(config['server']) != server:
                raise ValueError('Another window saved a different server connection. Reopen the app.')
            spool.check_destination(server, config['recorder_id'])
        if previous is None and (spool.pending() or spool.db.execute('SELECT count(*) FROM outbox').fetchone()[0]):
            raise ValueError('Restore the original config.json for this saved queue. Its recorder connection must be preserved.')
        if previous is None:
            if not save_configuration(path, config, exclusive=True):
                config = load_configuration(path)
                if canonical_server(config['server']) != server:
                    raise ValueError('Another window saved this recorder setup. Reopen the app to use its saved connection.')
                spool.check_destination(server, config['recorder_id'])
        elif config != previous:
            save_configuration(path, config)
        spool.bind(server, config['recorder_id'])
    finally:
        spool.close()
    return config
