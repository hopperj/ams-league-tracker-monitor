"""AMS2 participant evidence shared with the recorder.

Layouts: SMS UDP timings v3 (1059 bytes) and the observed AMS2 v1 layout
(1063 bytes) documented in RangeyRover's SMS_UDP_Definitions_AMS2_RR.hpp.
The local player, names roster and measured positions must agree before the
human-player bit is used. Unrecognised or ambiguous packets yield no evidence.
"""
import base64
import binascii
import math
import struct
from collections import Counter


HEADER = struct.Struct('<IIBBBB')
NAMES_SIZE = 1136
MAX_AGE_MS = 750
# (version, length): (participant start, race position, car index)
TIMINGS = {(3, 1059): (33, 14, 18), (1, 1063): (31, 16, 20)}


def packet_info(packet):
    """Return the roster revision and fragment metadata for supported packets."""
    if len(packet) < HEADER.size:
        return None
    serial, group, part, total, kind, version = HEADER.unpack_from(packet)
    if kind == 2 and version == 2 and len(packet) == NAMES_SIZE and 1 <= total <= 2 and part <= total:
        revision = struct.unpack_from('<I', packet, 12)[0]
    elif kind == 3 and (version, len(packet)) in TIMINGS and total <= 1 and part <= 1:
        revision = struct.unpack_from('<I', packet, 13)[0]
    else:
        return None
    return kind, revision, serial, group, part, total


def _roster(packets, revision):
    fragments = {}
    group = total = None
    for packet in packets:
        info = packet_info(packet)
        if not info or info[0] != 2 or info[1] != revision:
            return None
        _, _, _, group_id, part, count = info
        if group is not None and (group_id != group or count != total) or part in fragments:
            return None
        group, total = group_id, count
        fragments[part] = packet
    if total is None or set(fragments) not in (set(range(total)), set(range(1, total + 1))):
        return None
    rows = []
    for part in sorted(fragments):
        packet = fragments[part]
        for index in range(16):
            start = 16 + index * 64
            name = packet[start:start+64].split(b'\0', 1)[0].decode('utf-8', errors='replace').strip()
            player_id = struct.unpack_from('<H', packet, 1104 + index * 2)[0]
            rows.append((name, player_id))
    return rows


def _matches_position(p, position, age):
    xyz = p.get('world_position')
    if not isinstance(xyz, (list, tuple)) or len(xyz) != 3:
        return False
    if not all(type(v) in (int, float) and abs(v) < 32767 for v in xyz):
        return False
    speed = p.get('speed_mps')
    if type(speed) not in (int, float) or not 0 <= speed <= 150:
        speed = 0
    return math.dist(xyz, position) <= 4 + speed * age / 1000


def match_participants(frame, timing, names, age):
    """Map UDP evidence to the shared-memory slots in a live frame."""
    if (frame.get('source_kind', 'shared_memory') != 'shared_memory' or frame.get('decoded') is not True
            or frame.get('consistent') is not True or frame.get('replay') is not False
            or frame.get('game_state') != 2 or type(age) not in (int, float) or not 0 <= age <= MAX_AGE_MS):
        return {}
    sdk = frame.get('published')
    if not isinstance(sdk, dict) or sdk.get('mVersion') != 14:
        return {}
    info = packet_info(timing)
    if not info or info[0] != 3:
        return {}
    count = timing[12]
    if not 2 <= count <= 32 or count != sdk.get('mNumParticipants'):
        return {}
    roster = _roster(names, info[1])
    if not roster or len(roster) < count:
        return {}
    start, position_offset, car_offset = TIMINGS[timing[11], len(timing)]
    local_id = struct.unpack_from('<H', timing, start + 32 * 32)[0]
    ids = Counter(i for name, i in roster if name)
    names_by_id = {i: name for name, i in roster if name and ids[i] == 1}
    players = frame.get('participants', [])
    name_counts = Counter(p['name'].strip() for p in players)
    by_name = {p['name'].strip(): p for p in players if name_counts[p['name'].strip()] == 1 and p.get('active') is True}
    viewed = sdk.get('mViewedParticipantIndex')
    shared_slots = sdk.get('mParticipantInfo')
    if not isinstance(shared_slots, list) or type(viewed) is not int or not 0 <= viewed < min(count, len(shared_slots)):
        return {}
    matched = {}
    local_ok = False
    for index in range(count):
        offset = start + 32 * index
        if not timing[offset + position_offset] & 0x80:
            continue
        car = struct.unpack_from('<H', timing, offset + car_offset)[0]
        if car == 0xffff:
            continue
        udp_id = struct.unpack_from('<H', timing, offset + 30)[0]
        position = struct.unpack_from('<3h', timing, offset)
        # Some offline rosters repeat the MP index. The ordered name is also
        # usable when it independently matches the measured car position.
        possible = {roster[index][0], names_by_id.get(udp_id, '')}
        candidates = [by_name[name] for name in possible if name in by_name and _matches_position(by_name[name], position, age)]
        if len(candidates) != 1:
            continue
        p = candidates[0]
        slot = p.get('slot')
        if type(slot) is not int or not 0 <= slot < len(shared_slots) or not isinstance(shared_slots[slot], dict):
            continue
        if shared_slots[slot].get('mName') != p['name'] or shared_slots[slot].get('mIsActive') is not True:
            continue
        if slot in matched:
            return {}
        human = bool(car & 0x8000)
        matched[slot] = 'human' if human else 'ai'
        if slot == viewed:
            local_ok = human and local_id in (udp_id, index) and p.get('car') == sdk.get('mCarName')
    return matched if local_ok and len(matched) >= 2 else {}


def types_from_evidence(frame):
    """Recheck bounded raw UDP evidence on the server before recording labels."""
    proof = frame.get('participant_type_evidence')
    if not isinstance(proof, dict) or proof.get('format') != 'ams2-udp-v1':
        return {}
    names = proof.get('names')
    if not isinstance(names, list) or not 1 <= len(names) <= 2:
        return {}
    packets = [proof.get('timing'), *names]
    if any(not isinstance(packet, str) or len(packet) > 1516 for packet in packets):
        return {}
    try:
        timing, *names = [base64.b64decode(packet, validate=True) for packet in packets]
        return match_participants(frame, timing, names, proof.get('age_ms'))
    except (ValueError, TypeError, KeyError, binascii.Error):
        return {}
