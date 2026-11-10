"""Preserve the complete capture; normalize only explicitly supported layouts."""
import base64
import ctypes as C
import math
import struct
import unicodedata
from collections import Counter

from .schema import SharedMemory, STRUCT_BYTES, SUPPORTED_VERSION
from .participants import is_safety_car

PHASES = {1: "practice", 2: "test", 3: "qualifying", 4: "formation", 5: "race", 6: "time_trial"}


def plain(value):
    if isinstance(value, bytes):
        return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")
    if isinstance(value, C.Array):
        if value._type_ is C.c_char:
            return plain(bytes(value))
        return [plain(v) for v in value]
    if isinstance(value, C.Structure):
        return {name: plain(getattr(value, name)) for name, _ in value._fields_}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def identity(name, car, car_class):
    # Slots are local to an observer. Never merge using the slot index.
    return "\x1f".join(unicodedata.normalize("NFC", s).strip() for s in (name, car, car_class))


def decode(raw, driver_name=None):
    version = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else None
    result = {"raw_b64": base64.b64encode(raw).decode("ascii"), "memory_version": version,
              "raw_bytes": len(raw), "decoded": False, "participants": [], "telemetry": None}
    if version != SUPPORTED_VERSION or len(raw) < STRUCT_BYTES:
        result["decode_error"] = "Unsupported layout or incomplete mapping; raw bytes retained"
        return result
    state = SharedMemory.from_buffer_copy(raw[:STRUCT_BYTES])
    # Export all 158 top-level fields, including arrays and currently obsolete fields.
    # These are archival measurements; only verified driver telemetry is indexed.
    fields = plain(state)
    result.update(decoded=True, game_state=state.mGameState,
                  phase=PHASES.get(state.mSessionState, "unknown"),
                  game_sequence=state.mSequenceNumber, build=state.mBuildVersionNumber,
                  session={"track": fields["mTrackLocation"], "variation": fields["mTrackVariation"],
                           "track_length_m": fields["mTrackLength"], "scheduled_laps": state.mLapsInEvent,
                           "duration_s": fields["mSessionDuration"], "extra_laps": state.mSessionAdditionalLaps,
                           "remaining_raw": fields["mEventTimeRemaining"],
                           "air_temp_c": fields["mAmbientTemperature"], "track_temp_c": fields["mTrackTemperature"],
                           "rain": fields["mRainDensity"], "yellow_flag_state": state.mYellowFlagState},
                  published=fields)
    count = max(0, min(64, state.mNumParticipants))
    participants = []
    for i in range(count):
        p = fields["mParticipantInfo"][i]
        if not p["mName"]:
            continue
        car, cls = fields["mCarNames"][i], fields["mCarClassNames"][i]
        if is_safety_car(p["mName"], car, cls):
            continue
        participants.append({
            "slot": i, "key": identity(p["mName"], car, cls), "name": p["mName"],
            "car": car, "class": cls, "active": p["mIsActive"],
            "position": p["mRacePosition"], "laps_completed": p["mLapsCompleted"],
            "lap": p["mCurrentLap"], "sector": p["mCurrentSector"],
            "distance_m": p["mCurrentLapDistance"], "world_position": p["mWorldPosition"],
            "speed_mps": fields["mSpeeds"][i], "orientation": fields["mOrientations"][i],
            "last_lap_s": fields["mLastLapTimes"][i], "best_lap_s": fields["mFastestLapTimes"][i],
            "sectors_s": [fields["mCurrentSector%dTimes" % j][i] for j in (1, 2, 3)],
            "best_sectors_s": [fields["mFastestSector%dTimes" % j][i] for j in (1, 2, 3)],
            "invalid_current_lap": fields["mLapsInvalidated"][i], "race_state": fields["mRaceStates"][i],
            "pit_mode": fields["mPitModes"][i], "pit_schedule": fields["mPitSchedules"][i],
            "flag": fields["mHighestFlagColours"][i], "flag_reason": fields["mHighestFlagReasons"][i],
            "nationality": fields["mNationalities"][i],
        })
    duplicates = Counter(p["key"] for p in participants)
    for p in participants:
        p["ambiguous"] = duplicates[p["key"]] > 1
    result["participants"] = participants
    viewed = next((p for p in participants if p["slot"] == state.mViewedParticipantIndex), None)
    automatic = driver_name is None
    if automatic and viewed:
        driver_name = viewed['name']
    matches = [p for p in participants if p["name"] == driver_name]
    # Fail closed on duplicates, replay, menu/spectator ambiguity, or mismatched car.
    verified = (state.mGameState == 2 and viewed and len(matches) == 1
                and viewed["name"] == driver_name and not viewed["ambiguous"]
                and viewed["car"] == fields["mCarName"]
                and (not automatic or viewed['active'] and viewed['class'] == fields['mCarClassName'])
                and isinstance(viewed["speed_mps"], (int, float))
                and abs(viewed["speed_mps"] - state.mSpeed) < 0.5)
    result["telemetry_verified"] = bool(verified)
    if verified:
        participant_fields = {"mParticipantInfo", "mCarNames", "mCarClassNames", "mOrientations", "mSpeeds",
                              "mLapsInvalidated", "mRaceStates", "mPitModes", "mPitSchedules", "mHighestFlagColours",
                              "mHighestFlagReasons", "mNationalities", "mLastLapTimes", "mFastestLapTimes"}
        participant_fields.update("m%sSector%dTimes" % (kind, j) for kind in ("Current", "Fastest") for j in (1, 2, 3))
        result["telemetry"] = {
            "subject_key": viewed["key"], "name": viewed["name"], "lap": viewed["lap"],
            "distance_m": viewed["distance_m"], "speed_mps": fields["mSpeed"],
            "throttle": fields["mThrottle"], "brake": fields["mBrake"],
            "steering": fields["mSteering"], "clutch": fields["mClutch"],
            "rpm": fields["mRpm"], "gear": state.mGear, "fuel_fraction": fields["mFuelLevel"],
            "fuel_capacity_l": fields["mFuelCapacity"], "tyre_temp_c": fields["mTyreTemp"],
            "tyre_wear": fields["mTyreWear"], "tyre_pressure_kpa": fields["mAirPressure"],
            "tyre_compound": fields["mTyreCompound"], "aero_damage": fields["mAeroDamage"],
            "engine_damage": fields["mEngineDamage"], "brake_bias": fields["mBrakeBias"],
            "channels": {k: v for k, v in fields.items() if k not in participant_fields},
        }
    return result
