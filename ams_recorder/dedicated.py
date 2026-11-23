"""Headless capture from the native AMS2 dedicated-server HTTP API."""
import argparse
import http.client
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from .spool import Spool
from .transport import Transport


LOG = logging.getLogger("ams2-dedicated-recorder")
CHECKPOINT = "dedicated-server"
STATUS = "/api/session/status?attributes=true&members=true&participants=true"
PHASES = {"Practice1": "practice", "Practice2": "practice", "Warmup": "practice",
          "Qualifying": "qualifying", "FormationLap": "race", "Race1": "race", "Race2": "race"}
PITS = {"EnteringPits": 1, "InPits": 2, "ExitingPits": 3, "InGarage": 4}
RACE_STATES = {"Finished": 3, "Disqualified": 4, "Retired": 5, "DNF": 6}
TIMED_RACE = 1048576
EVENT_PAGE = 128
MAX_RESPONSE = 8 * 1024 * 1024


class Server:
    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("Use the dedicated server's local HTTP origin")
        self.host, self.port = parsed.hostname, parsed.port or 80

    def get(self, endpoint):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        try:
            connection.request("GET", endpoint, headers={"Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise OSError(f"Dedicated server returned HTTP {response.status} for {endpoint.split('?')[0]}")
            payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise ValueError("Dedicated-server response exceeds the capture limit")
            envelope = json.loads(payload)
            data = envelope.get("response")
            if envelope.get("result") != "ok" or not isinstance(data, dict):
                raise ValueError(f"Unexpected dedicated-server response for {endpoint.split('?')[0]}")
            return data
        finally:
            connection.close()


def invocation(unit):
    if not unit:
        return None
    value = subprocess.run(["systemctl", "--user", "show", "--property=InvocationID", "--value", "--", unit],
                           check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    if not value or set(value) == {"0"}:
        raise OSError("The dedicated-server service is not running")
    return value


def milliseconds(value):
    return value * 1000 if type(value) is int and value > 0 else None


def seconds(value):
    return value / 1000 if type(value) is int and value > 0 else None


def steam_id(value):
    value = str(value)
    return value if value.isascii() and value.isdecimal() and len(value) == 17 else None


def stage_name(value):
    return "Race1" if value == "FormationLap" else value


def stage_info(status, stamp, tracks):
    a = status["attributes"]
    stage = a.get("SessionStage")
    track = tracks.get(a.get("TrackId"))
    if (status.get("state") != "Running" or a.get("SessionState") not in ("Race", "PostRace")
            or stage not in PHASES or not track):
        return None
    phase = PHASES[stage]
    elapsed, duration = a.get("SessionTimeElapsed"), a.get("SessionTimeDuration")
    elapsed = elapsed if type(elapsed) is int and elapsed >= 0 else None
    duration = duration if type(duration) is int and duration > 0 else None
    timed = phase != "race" or bool(a.get("Flags", 0) & TIMED_RACE)
    if not duration and timed and type(length := a.get({"practice": "PracticeLength",
            "qualifying": "QualifyLength", "race": "RaceLength"}[phase])) is int and length > 0:
        duration = length * 60
    laps = a.get("RaceLength") if not timed else None
    started = stamp - elapsed * 1000 if elapsed is not None and a.get("SessionPhase") == "Green" else None
    session = {"track": track, "variation": track, "duration_s": duration if timed else None,
               "scheduled_laps": laps, "extra_laps": a.get("RaceExtraLap") if phase == "race" and timed else None,
               "remaining_raw": duration - elapsed if timed and duration and elapsed is not None else None,
               "air_temp_c": a.get("TemperatureAmbient"), "track_temp_c": a.get("TemperatureTrack"),
               "rain": a.get("RainDensity")}
    return {"key": [str(status.get("lobbyid", "")), track, stage_name(stage)], "phase": phase,
            "started_at_ms": started, "session": session, "elapsed": elapsed, "green": a.get("SessionPhase") == "Green"}


def car_state(ident, attributes, members, vehicles):
    name = attributes.get("Name")
    if not isinstance(name, str) or not name.strip() or type(ident) is not int or ident < 0:
        return None
    vehicle = vehicles.get(attributes.get("VehicleId"), {})
    state = attributes.get("State")
    player = attributes.get("IsPlayer")
    lap = attributes.get("CurrentLap")
    lap = max(0, lap) if type(lap) is int else 0
    coords = [attributes.get("Position" + axis) for axis in ("X", "Y", "Z")]
    speed = attributes.get("Speed")
    position = attributes.get("RacePosition")
    return {"slot": ident, "name": name, "car": vehicle.get("name"), "class": vehicle.get("class"),
            "is_player": bool(player) if player in (0, 1) else None,
            "steam_id": members.get(attributes.get("RefId")) if player == 1 else None,
            "position": max(0, position) if type(position) is int else 0, "lap": lap,
            "laps_completed": max(0, lap - 1), "active": state not in ("InGarage", "Retired", "DNF", "Disqualified"),
            "sector": attributes.get("CurrentSector"),
            "world_position": [value / 1000 for value in coords] if all(type(v) is int for v in coords) else None,
            "speed_mps": speed / 3.6 if type(speed) is int and speed >= 0 else None,
            "heading_deg": attributes.get("Orientation"), "rpm": attributes.get("RPM"), "gear": attributes.get("Gear"),
            "race_state": RACE_STATES.get(state, 2), "pit_mode": PITS.get(state, 0),
            "pit_mode_known": state in {"Racing", *PITS} | RACE_STATES.keys(),
            "last_lap_s": seconds(attributes.get("LastLapTime")), "best_lap_s": seconds(attributes.get("FastestLapTime")),
            "sectors_s": [seconds(attributes.get("Sector%dTime" % n)) for n in (1, 2, 3)]}


def frame_data(context, participants):
    if not context or not participants:
        return {"decoded": False}
    return {"decoded": True, "game_state": 2, "phase": context["phase"], "session": context["session"],
            "stage_id": context["id"], "stage_started_at_ms": context["started_at_ms"], "participants": participants}


class Capture:
    def __init__(self, server, spool, unit=None):
        self.server, self.spool, self.unit = server, spool, unit
        self.state = spool.checkpoint(CHECKPOINT) or {}
        self.tracks = {}
        self.vehicles = {}

    def catalog(self):
        tracks = self.server.get("/api/list/tracks")["list"]
        vehicles = self.server.get("/api/list/vehicles")["list"]
        self.tracks = {row["id"]: row["name"] for row in tracks}
        self.vehicles = {row["id"]: row for row in vehicles}

    def poll(self):
        instance = invocation(self.unit)
        if not self.tracks or instance != self.state.get("instance"):
            self.catalog()
        status = self.server.get(STATUS)
        stamp = time.time_ns() // 1_000_000
        overview = self.server.get("/api/log/overview")
        first, end = overview["first"], overview["first"] + overview["count"]
        previous = self.state
        if (not previous or instance != previous["instance"] or end < previous["cursor"]):
            previous = {"instance": instance, "run": str(uuid.uuid4()), "cursor": first, "sequence": 0}
        pending = []
        cursor = max(first, previous["cursor"])
        gap = cursor - previous["cursor"]
        # Read a finite number per iteration. A busy server can append while a page is in flight.
        for _ in range(16):
            if cursor >= end:
                break
            page = self.server.get(f"/api/log/range?offset={cursor}&count={min(end-cursor, EVENT_PAGE)}")
            if page["first"] > cursor:
                gap += page["first"] - cursor
                cursor = page["first"]
            for event in page["events"]:
                index = event["index"]
                if index < cursor:
                    continue
                gap += index - cursor
                pending.append(event)
                cursor = index + 1
            if not page["events"]:
                break
        old = previous.get("stage")
        stage = stage_info(status, stamp, self.tracks)
        boundaries = [event for event in pending if event["name"] in
                      ("SessionCreated", "SessionSetup", "SessionDestroyed", "SoftRestart", "StageChanged")]
        if stage:
            same = (old and old["key"] == stage["key"] and not any(e["name"] != "StageChanged" or
                    stage_name(e["attributes"].get("NewStage")) != stage["key"][2] for e in boundaries))
            if (same and old.get("green") and stage["green"] and old["elapsed"] is not None
                    and stage["elapsed"] is not None and old["elapsed"] - stage["elapsed"] > 3):
                same = False
            stage["id"] = old["id"] if same else str(uuid.uuid4())
        members = {row.get("refid"): steam_id(row.get("steamid")) for row in status["members"]}
        current = {}
        if stage:
            for row in status["participants"]:
                car = car_state(row.get("id"), row.get("attributes", {}), members, self.vehicles)
                if car:
                    current[str(car["slot"])] = car
        cars = previous.get("cars", {}).copy()
        finals = previous.get("finals", {}).copy()
        context = old if not gap else None
        if stage and old and old["id"] == stage["id"]:
            # Keep the earlier roster available for late log events.
            cars = {**current, **cars}
        records = []
        if gap:
            LOG.warning("Dedicated server discarded %d unread event(s)", gap)
            records.append((stamp, {"log_gap": gap}))
        for event in pending:
            name = event["name"]
            attrs = event["attributes"]
            if name == "StageChanged":
                if stage and stage_name(attrs.get("NewStage")) == stage["key"][2] and cursor >= end:
                    if not context or context["id"] != stage["id"]:
                        finals = {}
                    context, cars = stage, current.copy()
                else:
                    context, cars, finals = None, {}, {}
            elif name in ("SessionCreated", "SessionSetup", "SessionDestroyed", "SoftRestart"):
                context, cars, finals = None, {}, {}
            elif name == "ParticipantCreated" and context:
                ident = event.get("participantid")
                if car := car_state(ident, attrs, members if context is stage else {}, self.vehicles):
                    prior = cars.get(str(ident))
                    if prior and (car["name"], car["car"]) == (prior["name"], prior["car"]):
                        car["steam_id"] = car["steam_id"] or prior["steam_id"]
                    cars[str(ident)] = car
            car = cars.get(str(event.get("participantid"))) if context else None
            projected = {}
            if car and name == "Lap" and type(lap := attrs.get("Lap")) is int and lap > 0:
                projected = dict(car, laps_completed=lap, lap=lap+1, last_lap_s=seconds(attrs.get("LapTime")),
                                 completed_lap_valid=bool(attrs["CountThisLapTimes"])
                                 if attrs.get("CountThisLapTimes") in (0, 1) else None,
                                 sectors_s=[seconds(attrs.get("Sector%dTime" % n)) for n in (1, 2, 3)],
                                 position=attrs.get("RacePosition", car["position"]), sector=0)
            elif car and name == "Results":
                result_state = attrs.get("State")
                lap = attrs.get("Lap")
                completed = max(car["laps_completed"], lap - 1) if type(lap) is int else car["laps_completed"]
                projected = dict(car, laps_completed=completed, result=True,
                                 lap=lap if type(lap) is int else car["lap"], last_lap_s=None,
                                 best_lap_s=seconds(attrs.get("FastestLapTime")) or car["best_lap_s"],
                                 position=attrs.get("RacePosition", car["position"]),
                                 race_state=RACE_STATES.get(result_state, car["race_state"]))
                finals[str(car["slot"])] = {key: projected[key] for key in
                                            ("laps_completed", "lap", "position", "race_state", "best_lap_s", "result")}
            if projected:
                # Event positions are missing from the native log. The surrounding
                # status frames hold the native samples used for position matching.
                projected.update(world_position=None, speed_mps=None)
            recorded_at = stamp if name == "Results" else milliseconds(event.get("time")) or stamp
            records.append((min(stamp, recorded_at), dict(frame_data(context, [projected] if projected else []),
                                                        dedicated_event=event)))
        # Catching up can span multiple stages. Take the fresh snapshot only
        # once the observed log range has been drained.
        if cursor >= end:
            status_key = {key: value for key, value in status.items() if key != "now"}
            if stage or status_key != previous.get("status"):
                if not stage or not context or stage["id"] != context["id"]:
                    finals = {}
                field = [dict(p, **finals.get(ident, {})) for ident, p in current.items()]
                records.append((stamp, dict(frame_data(stage, field), dedicated_status=status)))
            if stage and context and context["id"] == stage["id"]:
                current = {**cars, **current}
            previous = dict(previous, stage=stage, cars=current, status=status_key, finals=finals)
        sequence = previous["sequence"]
        frames = [dict(record, sequence=sequence+i, captured_at_ms=captured,
                       source_kind="dedicated_server", consistent=True, replay=False, synthetic=False)
                  for i, (captured, record) in enumerate(records, 1)]
        checkpoint = dict(previous, cursor=cursor, sequence=sequence+len(frames))
        self.spool.append_frames("AUTO", checkpoint["run"], frames, checkpoint=(CHECKPOINT, checkpoint))
        self.state = checkpoint
        return len(frames)


def upload(stop, spool_path, config, *, once=False):
    recorder = config["recorder_id"]
    transport = Transport(config["server"], config["token"])
    spool = Spool(spool_path)
    try:
        spool.bind(transport.server, recorder)
        delay = 2
        while not stop.is_set():
            batch = spool.batch(recorder, limit=24)
            if not batch:
                if once:
                    return
                stop.wait(2)
                continue
            batch_id, payload = batch
            try:
                receipt = transport.request("/api/batches", payload)
                if receipt.get("batch_id") != batch_id or receipt.get("stored") is not True or receipt.get("accepted") is not True:
                    raise ValueError("Upload was not durably acknowledged")
                spool.acknowledge(batch_id)
                delay = 2
            except (OSError, ValueError) as error:
                LOG.warning("Upload delayed; local queue retained (%s)", type(error).__name__)
                if once:
                    raise
                stop.wait(delay)
                delay = min(60, delay * 2)
    finally:
        spool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:9000")
    parser.add_argument("--unit", help="Local systemd user service running the dedicated server")
    parser.add_argument("--interval", default=1.0, type=float)
    parser.add_argument("--spool", required=True, type=Path)
    parser.add_argument("--config", type=Path, help="JSON containing recorder_id, server and token; omit for local capture")
    parser.add_argument("--once", action="store_true", help="Poll and upload once, then exit")
    args = parser.parse_args()
    if not 0.5 <= args.interval <= 10:
        parser.error("Use a polling interval between 0.5 and 10 seconds")
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = Server(args.api)
    config = json.loads(args.config.read_text()) if args.config and args.config.exists() else None
    if config:
        Transport(config["server"], config["token"])
    else:
        LOG.info("Saving captures locally; no upload credential configured")
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    spool = Spool(args.spool)
    sender = None
    try:
        if config:
            spool.bind(config["server"], config["recorder_id"])
            if not args.once:
                sender = threading.Thread(target=upload, args=(stop, args.spool, config))
                sender.start()
        capture = Capture(server, spool, args.unit)
        while not stop.is_set():
            started = time.monotonic()
            try:
                capture.poll()
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException, subprocess.SubprocessError) as error:
                LOG.warning("Dedicated-server poll will retry (%s)", type(error).__name__)
                if args.once:
                    raise
            if args.once:
                if config:
                    upload(stop, args.spool, config, once=True)
                break
            stop.wait(max(0, args.interval - (time.monotonic()-started)))
    finally:
        stop.set()
        if sender:
            sender.join()
        spool.close()


if __name__ == "__main__":
    main()
