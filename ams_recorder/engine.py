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

from .configuration import load_configuration
from .decoder import decode
from .memory import MemoryReader, MemoryUnavailable
from .spool import Spool
from .status import receipt_notice, upload_problem
from .transport import Transport

LOG = logging.getLogger("ams-recorder")


def run(args, stop_event=None, on_event=None):
    stop = stop_event if stop_event is not None else threading.Event()
    spool = reader = thread = udp_thread = None
    previous_signals = {}

    def report(kind, **values):
        if on_event:
            on_event(kind, **values)

    try:
        config = load_configuration(args.config)
        transport = Transport(config["server"], config["token"])
        spool = Spool(args.spool)
        spool.bind(transport.server, config["recorder_id"])
        report("started", pending=spool.pending())
        start = time.monotonic()
        anchor_wall, anchor_mono = time.time() * 1000, time.monotonic()
        clock = {"offset":0, "uncertainty":None, "last_sync":0}
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGINT", "SIGTERM"):
                if hasattr(signal, name):
                    number = getattr(signal, name)
                    previous_signals[number] = signal.signal(number, lambda *_: stop.set())

        if not args.demo and not args.replay:
            reader = MemoryReader()
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
                while not stop.is_set():
                    try:
                        if not clock["last_sync"] or time.monotonic() - clock["last_sync"] > 60:
                            offset, uncertainty, info = transport.clock(cancel=stop)
                            if info["recorder_id"] != config["recorder_id"]:
                                raise ValueError("Recorder configuration no longer matches the server")
                            if isinstance(info.get("driver_name"),str) and info["driver_name"]:
                                config["driver_name"]=info["driver_name"]
                            anchor_delta = time.time()*1000 - (anchor_wall + (time.monotonic()-anchor_mono)*1000)
                            clock.update(offset=offset+anchor_delta, uncertainty=uncertainty, last_sync=time.monotonic())
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
                        stop.wait(0 if pending >= 120 else args.batch_seconds)
                    except (OSError, ValueError, urllib.error.URLError) as exc:
                        if stop.is_set():
                            break
                        report("upload_error", message=upload_problem(exc), pending=outgoing.pending())
                        detail = "HTTP %s" % exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                        LOG.warning("Upload unavailable (%s); queue retained; retry in %ss", detail, backoff)
                        stop.wait(backoff)
                        backoff = min(backoff * 2, 60)
            except Exception as exc:
                LOG.error("Uploader stopped (%s); queued data retained", type(exc).__name__)
                report("fatal", message="The uploader stopped. Check your setup and available disk space.")
                stop.set()
            finally:
                if outgoing:
                    outgoing.close()

        run_id, sequence = str(uuid.uuid4()), itertools.count()
        capture_lock = threading.Lock()

        def persist(frame, queue):
            now = anchor_wall + (time.monotonic() - anchor_mono) * 1000
            with capture_lock:
                frame.update(sequence=next(sequence), captured_at_ms=round(now + clock["offset"]), clock_uncertainty_ms=clock["uncertainty"])
                queue.append(config["event_code"], run_id, frame)

        thread = threading.Thread(target=upload, name="uploader", daemon=True)
        thread.start()
        if args.udp:
            from .udp import receive
            udp_thread = threading.Thread(target=receive, args=(stop,persist,args.spool,args.max_spool_mb,args.udp_port,args.udp_source), name="udp-capture", daemon=True)
            udp_thread.start()
        last_raw, last_warning, next_tick = None, 0, start
        LOG.info("Recording %s at up to %s Hz; capture code %s", "synthetic demo" if args.demo else "AMS2", args.hz, config["event_code"])
        while not stop.is_set():
            if args.duration and time.monotonic() - start >= args.duration:
                break
            try:
                if spool.size_bytes() > args.max_spool_mb * 1024 * 1024:
                    raise MemoryUnavailable("Local queue is full. Capture will resume when uploads catch up.")
                if args.demo:
                    from .demo import snapshot
                    raw, consistent = snapshot(time.monotonic() - start + args.demo_offset, config["driver_name"]), True
                elif replay is not None:
                    try:
                        replay_frame = next(replay)
                        raw = base64.b64decode(replay_frame["raw_b64"], validate=True)
                    except StopIteration:
                        break
                    consistent = True
                else:
                    raw, consistent = reader.read()
                if raw != last_raw:
                    frame = dict(replay_frame) if replay is not None else decode(raw, config["driver_name"])
                    if replay is not None:
                        frame["original_captured_at_ms"] = replay_frame.get("captured_at_ms")
                    frame.update(source_kind=frame.get("source_kind","shared_memory"), consistent=consistent,
                                 synthetic=bool(args.demo or (replay is not None and replay_frame.get("synthetic"))), replay=bool(args.replay))
                    persist(frame, spool)
                    report("captured", decoded=frame["decoded"], consistent=consistent, game_state=frame.get("game_state"), replay=frame["replay"])
                    last_raw = raw
                    if reader and frame.get("game_state") == 0:
                        reader.close()
            except MemoryUnavailable as exc:
                report("capture_wait", message=str(exc))
                if time.monotonic() - last_warning > 10:
                    LOG.warning("%s", exc)
                    last_warning = time.monotonic()
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
