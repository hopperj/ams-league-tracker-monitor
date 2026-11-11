"""Optional datagram archive and UDP participant evidence for shared memory."""
import base64
import logging
import socket
import struct
import threading
import time

from .spool import Spool
from .udp_participants import MAX_AGE_MS, match_participants, packet_info

LOG = logging.getLogger("ams-recorder")


def packet_frame(data, address):
    # The pCARS2-family base header is 12 bytes. Preserve the entire payload,
    # even when the type/version is unfamiliar; do not guess packed offsets.
    if len(data) < 12:
        return None
    global_count, category_count, part, total, kind, version = struct.unpack_from("<IIBBBB",data)
    return {"source_kind":"udp","raw_b64":base64.b64encode(data).decode("ascii"),"raw_bytes":len(data),
            "memory_version":None,"decoded":False,"consistent":False,"participants":[],"telemetry":None,
            "synthetic":False,"replay":False,"udp":{"sender_ip":address[0],"sender_port":address[1],
            "packet_number":global_count,"category_number":category_count,"partial_index":part,
            "partial_total":total,"packet_type":kind,"packet_version":version}}


class ParticipantCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.senders = {}
        self.next_check = 0
        self.context = None

    def receive(self, packet, address):
        info = packet_info(packet)
        if info is None:
            return
        kind, revision, _, group, part, _ = info
        with self.lock:
            if address not in self.senders and len(self.senders) >= 4:
                self.senders.pop(next(iter(self.senders)))
            state = self.senders.setdefault(address, {'names': {}, 'timing': None})
            if kind == 3:
                state['timing'] = (time.monotonic(), packet, revision)
                return
            rosters = state['names']
            key = (revision, group)
            if key not in rosters and len(rosters) >= 4:
                rosters.pop(next(iter(rosters)))
            rosters.setdefault(key, {})[part] = packet

    def annotate(self, frame):
        if frame.get('game_state') not in (2, 3, 4):
            with self.lock:
                self.senders.clear()
            self.context = None
            return
        session = frame.get('session', {})
        context = (session.get('track'), session.get('variation'), frame.get('phase'))
        now = time.monotonic()
        if context == self.context and now < self.next_check:
            return
        self.context = context
        self.next_check = now + 1
        with self.lock:
            candidates = []
            for state in self.senders.values():
                if state['timing'] is None:
                    continue
                received, timing, revision = state['timing']
                age = (now-received)*1000
                if not 0 <= age <= MAX_AGE_MS:
                    continue
                names = [list(parts.values()) for (rev, _), parts in state['names'].items() if rev == revision]
                candidates.append((timing, age, names))
        accepted = []
        for timing, age, rosters in candidates:
            for names in reversed(rosters):
                types = match_participants(frame, timing, names, age)
                if types:
                    accepted.append((types, timing, names, age))
                    break
        if len(accepted) != 1:
            return
        types, timing, names, age = accepted[0]
        frame['participant_type_evidence'] = {
            'format': 'ams2-udp-v1', 'age_ms': round(age, 2),
            'timing': base64.b64encode(timing).decode('ascii'),
            'names': [base64.b64encode(packet).decode('ascii') for packet in names],
        }
        for p in frame['participants']:
            if p['slot'] in types:
                p['participant_type'] = types[p['slot']]


def receive(stop, persist, spool_path, max_spool_mb, port=5606, source_ip=None, *, observe=None, archive=True):
    queue=Spool(spool_path) if archive else None
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.settimeout(.25)
    try:
        sock.bind(("",port))
        LOG.info("Listening for AMS2 UDP data on port %s%s",port," from "+source_ip if source_ip else "")
        while not stop.is_set():
            # Stop reading instead of silently acknowledging data after the queue budget is exhausted.
            if queue and queue.size_bytes() >= max_spool_mb*1024*1024:
                stop.wait(.5)
                continue
            try:
                data,address=sock.recvfrom(65535)
            except socket.timeout:
                continue
            if source_ip and address[0]!=source_ip:
                continue
            if observe:
                observe(data, address)
            if queue:
                frame=packet_frame(data,address)
                if frame:
                    persist(frame,queue)
    except OSError:
        LOG.warning("UDP port %s unavailable; shared-memory capture continues", port)
    finally:
        sock.close()
        if queue:
            queue.close()
