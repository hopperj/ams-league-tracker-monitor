import json
import secrets
import time
import gzip
import urllib.parse
import urllib.error
import urllib.request

from .compact_wire import ENCODING, MAGIC, MEDIA_TYPE

DEFAULT_SERVER = 'https://ams2.ioresearch.ca'
LEGACY_SERVER = DEFAULT_SERVER + ':5443'


def canonical_server(server):
    """Treat the two HTTPS ports of the league service as one destination."""
    origin = server.rstrip('/')
    if origin.lower() in (LEGACY_SERVER, DEFAULT_SERVER + ':443'):
        return DEFAULT_SERVER
    return origin


class Transport:
    def __init__(self, server, token):
        parsed = urllib.parse.urlsplit(server)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an HTTP(S) server URL without embedded credentials")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote servers require HTTPS to protect recorder credentials")
        if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Use the server origin without a path, query, or fragment")
        self.server, self.token = server.rstrip("/"), token
        self.compact_capture = False
        # Never forward bearer credentials across an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, payload=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if payload is not None:
            headers.update({"Content-Type": "application/json", "Content-Encoding": "gzip"})
            if path == "/api/batches":
                headers["X-AMS-Upload-Ack"] = "stored"
                if payload.startswith(MAGIC):
                    headers["Content-Type"] = MEDIA_TYPE
                    headers.pop("Content-Encoding")
        req = urllib.request.Request(self.server + path, data=payload, headers=headers)
        try:
            response = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            try:
                detail=json.loads(error.read(8192))
                message=detail.get("error") if isinstance(detail,dict) else None
                if isinstance(message,str):
                    message=message.replace(self.token,"[redacted]") if self.token else message
                    error.server_message=" ".join(message.split())[:400]
                if isinstance(detail, dict):
                    error.server_code = detail.get('code')
            except (ValueError,TypeError,OSError):
                pass
            finally:
                error.close()
            raise
        with response:
            result = json.loads(response.read(65536))
            if not isinstance(result, dict):
                raise ValueError("The server returned an unexpected response")
            return result

    def health(self, recorder_id):
        """Exercise authenticated gzip POST intake and a committed storage write."""
        nonce = secrets.token_urlsafe(24)
        payload = gzip.compress(json.dumps({'nonce': nonce}).encode(), mtime=0)
        try:
            result = self.request('/api/recorder/health', payload)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                error.server_message = 'The server needs an update for Steam-linked recorder health checks. Recording is paused.'
            raise
        if (result.get('nonce') != nonce or result.get('recorder_id') != recorder_id
                or result.get('upload_ready') is not True or result.get('account_linked') is not True):
            raise ValueError('The server did not confirm linked recorder upload access')
        formats = result.get("capture_formats")
        self.compact_capture = isinstance(formats, list) and ENCODING in formats
        return result

    def clock(self, cancel=None):
        samples = []
        for _ in range(3):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Recorder stopped")
            before = time.time() * 1000
            started = time.monotonic()
            result = self.request("/api/recorder")
            rtt = (time.monotonic() - started) * 1000
            samples.append((rtt, result["server_time_ms"] - (before + rtt / 2), result))
        rtt, offset, info = min(samples, key=lambda s: s[0])
        return offset, rtt / 2, info

    def register(self, recorder_id):
        payload = gzip.compress(json.dumps({'recorder_id': recorder_id}).encode(), mtime=0)
        try:
            info = self.request('/api/recorders/register', payload)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                error.server_message = 'The server needs an update for automatic recorder connection. Saved data will retry.'
            raise
        if info.get('recorder_id') != recorder_id:
            raise ValueError('The server did not confirm this recorder installation. The local queue is retained.')
        return info
