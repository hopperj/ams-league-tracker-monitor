"""Capture and durable uploads, usable from a CLI or a desktop worker thread."""
import base64
import gzip
import itertools
import json
import logging
from pathlib import Path
import signal
import threading
import time
import urllib.error
import uuid

from .configuration import prepare_configuration
from .decoder import decode
from .memory import MemoryReader, MemoryUnavailable, SnapshotBusy
from .spool import Spool
from .status import receipt_notice, upload_problem
from .transport import Transport

LOG = logging.getLogger("ams-recorder")
HEALTH_SECONDS = 5


def run(args, stop_event=None, on_event=None):
    stop = stop_event if stop_event is not None else threading.Event()
    spool = reader = thread = udp_thread = None
    previous_signals = {}

    def report(kind, **values):
        if on_event:
            on_event(kind, **values)

    try:
        config = prepare_configuration(args.config, args.spool)
        capture_name = 'Demo driver' if args.demo and config.get('auto_registration') else config['driver_name']
        transport = Transport(config["server"], config["token"])
        spool = Spool(args.spool)
        spool.bind(transport.server, config["recorder_id"])
        report("started", pending=spool.pending())
        start = time.monotonic()
        anchor_wall, anchor_mono = time.time() * 1000, time.monotonic()
        clock = {"offset":0, "uncertainty":None, "last_sync":0}
        capture_allowed = threading.Event()
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGINT", "SIGTERM"):
                if hasattr(signal, name):
                    number = getattr(signal, name)
                    previous_signals[number] = signal.signal(number, lambda *_: stop.set())

        replay = None
        if args.replay:
            body = Path(args.replay).read_bytes()
            recorded = json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)
            replay = iter(recorded["frames"])

        def upload():
            outgoing = None
            try:
                outgoing = Spool(args.spool)
                backoff = 1
                registered = not config.get('auto_registration', False)
                last_health = None
                while not stop.is_set():
                    checking_health = True
                    try:
                        if not registered:
                            transport.register(config['recorder_id'])
                            registered = True
                        if last_health is None or time.monotonic() - last_health >= HEALTH_SECONDS:
                            info = transport.health(config['recorder_id'])
                            last_health = time.monotonic()
                            capture_allowed.set()
                            report('comms_ok', account_name=info.get('account_name'))
                        if not clock["last_sync"] or time.monotonic() - clock["last_sync"] > 60:
                            offset, uncertainty, info = transport.clock(cancel=stop)
                            if info["recorder_id"] != config["recorder_id"]:
                                raise ValueError("Recorder configuration no longer matches the server")
                            if isinstance(info.get("driver_name"),str) and info["driver_name"]:
                                config["driver_name"]=info["driver_name"]
                                report('identity', driver_name=info['driver_name'])
                            anchor_delta = time.time()*1000 - (anchor_wall + (time.monotonic()-anchor_mono)*1000)
                            clock.update(offset=offset+anchor_delta, uncertainty=uncertainty, last_sync=time.monotonic())
                        checking_health = False
                        batch = outgoing.batch(config["recorder_id"])
                        report("queued", pending=outgoing.pending())
                        if batch:
                            batch_id, body = batch
                            ack = transport.request("/api/batches", body)
                            if (ack.get("batch_id") != batch_id or ack.get("accepted") is not True
                                    or ("stored" in ack and ack["stored"] is not True)):
                                raise ValueError("Server did not acknowledge this batch")
                            outgoing.acknowledge(batch_id)
                            pending = outgoing.pending()
                            notice=receipt_notice(ack)
                            if notice:
                                notice=notice.replace(config["token"],"[redacted]")
                            report("uploaded", pending=pending, review=notice)
                            if notice:
                                LOG.warning("%s",notice.replace(config["token"],"[redacted]"))
                            LOG.info("Uploaded batch; %d frames queued", pending)
                        else:
                            pending = outgoing.pending()
                        backoff = 1
                        until_health = max(0, HEALTH_SECONDS - (time.monotonic() - last_health))
                        stop.wait(0 if pending >= 120 else min(args.batch_seconds, until_health))
                    except (OSError, ValueError, urllib.error.URLError) as exc:
                        if isinstance(exc, urllib.error.HTTPError) and exc.code == 401 and config.get('auto_registration'):
                            registered = False
                        if stop.is_set():
                            break
                        denied = isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403)
                        if denied:
                            with capture_lock:
                                capture_allowed.clear()
                            last_health = None
                        if getattr(exc, 'server_code', None) == 'steam_link_required':
                            report('link_required', message='Link Steam account and approve this recorder in your browser before recording.', pending=outgoing.pending())
                            LOG.warning('Steam approval required. Open the recorder window and choose Link Steam account before recording.')
                        else:
                            report('comms_error' if checking_health else 'upload_error', message=upload_problem(exc), pending=outgoing.pending())
                        detail = "HTTP %s" % exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                        LOG.warning("Upload unavailable (%s); queue retained; retry in %ss", detail, backoff)
                        stop.wait(min(backoff, HEALTH_SECONDS))
                        backoff = min(backoff * 2, HEALTH_SECONDS)
            except Exception as exc:
                LOG.error("Uploader stopped (%s); queued data retained", type(exc).__name__)
                report("fatal", message="The uploader stopped. Check your setup and available disk space.")
                stop.set()
            finally:
                if outgoing:
                    outgoing.close()

        run_id, sequence = str(uuid.uuid4()), itertools.count()
        capture_lock = threading.RLock()
        udp_participants = None
        if getattr(args, 'udp_detection', False) and not (args.demo or args.replay):
            from .udp import ParticipantCache
            udp_participants = ParticipantCache()

        def persist(frame, queue):
            now = anchor_wall + (time.monotonic() - anchor_mono) * 1000
            with capture_lock:
                if not capture_allowed.is_set() or stop.is_set():
                    return False
                frame.update(sequence=next(sequence), captured_at_ms=round(now + clock["offset"]), clock_uncertainty_ms=clock["uncertainty"])
                queue.append(config["event_code"], run_id, frame)
                return True

        thread = threading.Thread(target=upload, name="uploader", daemon=True)
        thread.start()
        last_raw, last_warning, next_tick = None, 0, start
        snapshot_busy = False
        LOG.info("Recorder started for %s at up to %s Hz; capture waits for verified Steam upload access; code %s", "synthetic demo" if args.demo else "AMS2", args.hz, config["event_code"])
        while not stop.is_set():
            if args.duration and time.monotonic() - start >= args.duration:
                break
            if not capture_allowed.is_set():
                if reader:
                    reader.close()
                stop.wait(.1)
                continue
            if (args.udp or udp_participants) and udp_thread is None:
                from .udp import receive
                udp_thread = threading.Thread(target=receive,
                    args=(stop,persist,args.spool,args.max_spool_mb,args.udp_port,args.udp_source),
                    kwargs={'observe': udp_participants.receive if udp_participants else None, 'archive': args.udp},
                    name="udp-capture", daemon=True)
                udp_thread.start()
            try:
                if spool.size_bytes() > args.max_spool_mb * 1024 * 1024:
                    raise MemoryUnavailable("Local queue is full. Capture will resume when uploads catch up.")
                if args.demo:
                    from .demo import snapshot
                    raw, consistent = snapshot(time.monotonic() - start + args.demo_offset, capture_name), True
                elif replay is not None:
                    try:
                        replay_frame = next(replay)
                        raw = base64.b64decode(replay_frame["raw_b64"], validate=True)
                    except StopIteration:
                        break
                    consistent = True
                else:
                    if reader is None:
                        reader = MemoryReader()
                    raw, consistent = reader.read()
                    if snapshot_busy:
                        report("snapshot_read")
                        snapshot_busy = False
                if raw != last_raw:
                    frame = dict(replay_frame) if replay is not None else decode(raw,
                        capture_name if args.demo else None if config.get('auto_registration') else config['driver_name'])
                    if replay is not None:
                        frame["original_captured_at_ms"] = replay_frame.get("captured_at_ms")
                    frame.update(source_kind=frame.get("source_kind","shared_memory"), consistent=consistent,
                                 synthetic=bool(args.demo or (replay is not None and replay_frame.get("synthetic"))), replay=bool(args.replay))
                    if udp_participants:
                        udp_participants.annotate(frame)
                    if not capture_allowed.is_set():
                        continue
                    with capture_lock:
                        if not persist(frame, spool):
                            continue
                        report("captured", decoded=frame["decoded"], consistent=consistent, game_state=frame.get("game_state"), replay=frame["replay"])
                    last_raw = raw
                    if reader and frame.get("game_state") == 0:
                        reader.close()
            except MemoryUnavailable as exc:
                snapshot_busy = isinstance(exc, SnapshotBusy)
                if snapshot_busy:
                    report("snapshot_retry", message=str(exc))
                else:
                    report("capture_wait", message=str(exc), waiting=str(exc).startswith('Waiting for AMS2.'))
                if time.monotonic() - last_warning > 10:
                    LOG.warning("%s", exc)
                    last_warning = time.monotonic()
                if not snapshot_busy:
                    stop.wait(.25)
            next_tick = max(next_tick + 1 / args.hz, time.monotonic())
            stop.wait(max(0, next_tick - time.monotonic()))
    except Exception:
        report("fatal", message="Recording stopped. Check your setup, game connection, and available disk space.")
        raise
    finally:
        stop.set()
        if reader:
            reader.close()
        if udp_thread:
            udp_thread.join()
        if thread:
            thread.join()
        if spool:
            try:
                pending = spool.pending()
                report("stopped", pending=pending)
                LOG.info("Stopped. %d frames remain safely queued for the next run.", pending)
            finally:
                spool.close()
        else:
            report("stopped")
        for number, previous in previous_signals.items():
            signal.signal(number, previous)
