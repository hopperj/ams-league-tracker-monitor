import copy
import socket
import struct
import threading
import time
import unittest

from ams_recorder.udp import ParticipantCache, receive
from ams_recorder.udp_participants import match_participants, types_from_evidence


def field(count=3):
    participants = [dict(slot=i, name=f'Driver {i}', car='GT3', active=True,
                         world_position=[100 + 20*i, 1, 200 + 30*i], speed_mps=32) for i in range(count)]
    return dict(decoded=True, consistent=True, replay=False, game_state=2, phase='race',
                session={'track': 'Interlagos'}, participants=participants,
                published=dict(mVersion=14, mViewedParticipantIndex=0, mNumParticipants=count, mCarName='GT3',
                               mParticipantInfo=[dict(mName=p['name'], mIsActive=True) for p in participants]))


def packets(frame, ams2=True, human_slots=(0, 2)):
    participants = frame['participants']
    part_count = (len(participants) + 15) // 16
    roster = []
    for part in range(part_count):
        data = bytearray(1136)
        struct.pack_into('<IIBBBBI', data, 0, 10+part, 7, part+1, part_count, 2, 2, 1234)
        for slot, p in enumerate(participants[part*16:(part+1)*16]):
            encoded = p['name'].encode()
            data[16+slot*64:16+slot*64+len(encoded)] = encoded
            struct.pack_into('<H', data, 1104+slot*2, 100+p['slot'])
        roster.append(bytes(data))
    # The two documented timing layouts use different positions for the
    # participant array and the car-index field.
    size, version, start, active, car = (1063, 1, 31, 16, 20) if ams2 else (1059, 3, 33, 14, 18)
    data = bytearray(size)
    struct.pack_into('<IIBBBBBI', data, 0, 25, 8, 1, 1, 3, version, len(participants), 1234)
    for i, p in enumerate(participants):
        offset = start + i*32
        struct.pack_into('<3h', data, offset, *p['world_position'])
        data[offset+active] = 0x80 | (i+1)
        struct.pack_into('<H', data, offset+car, 19 | (0x8000 if p['slot'] in human_slots else 0))
        struct.pack_into('<H', data, offset+30, 100+p['slot'])
    struct.pack_into('<H', data, start+1024, 100)
    return bytes(data), roster


class UDPParticipantTest(unittest.TestCase):
    def test_both_timing_layouts_distinguish_local_human_remote_human_and_ai(self):
        for ams2 in (True, False):
            with self.subTest(ams2=ams2):
                snapshot = field()
                timing, roster = packets(snapshot, ams2=ams2)
                self.assertEqual(match_participants(snapshot, timing, roster, 0), {0:'human',1:'ai',2:'human'})

    def test_packets_must_match_roster_generation_positions_and_local_player(self):
        snapshot = field()
        timing, roster = packets(snapshot)
        other = copy.deepcopy(snapshot)
        other['participants'][0]['world_position'][0] += 1000
        self.assertEqual(match_participants(other, timing, roster, 0), {})
        self.assertEqual(match_participants(snapshot, timing, roster, 751), {})
        self.assertEqual(match_participants(snapshot, timing[:-1], roster, 0), {})
        stale = bytearray(roster[0]);struct.pack_into('<I', stale, 12, 1233)
        self.assertEqual(match_participants(snapshot, timing, [bytes(stale)], 0), {})
        inverted, names = packets(snapshot, human_slots=(1,2))
        self.assertEqual(match_participants(snapshot, inverted, names, 0), {})

    def test_multipart_roster_requires_all_fragments_and_ignores_duplicates(self):
        snapshot = field(21)
        timing, roster = packets(snapshot, human_slots=(0,20))
        self.assertEqual(match_participants(snapshot, timing, roster[:1], 0), {})
        types = match_participants(snapshot, timing, list(reversed(roster)), 0)
        self.assertEqual(len(types), 21)
        self.assertEqual(types[20], 'human')
        self.assertEqual(types[19], 'ai')
        self.assertEqual(match_participants(snapshot, timing, [roster[0],roster[0]], 0), {})

    def test_live_udp_listener_attaches_evidence_to_shared_memory_without_archiving_every_packet(self):
        snapshot = field()
        timing, roster = packets(snapshot)
        cache = ParticipantCache()
        stop = threading.Event()
        seen = threading.Event()
        archived = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserved:
            reserved.bind(('127.0.0.1',0))
            port = reserved.getsockname()[1]
        def observe(data, address):
            cache.receive(data,address)
            if data[10] == 3:
                seen.set()
        def unexpected_archive(*_):
            archived.append(True)
        receiver = threading.Thread(target=receive, args=(stop,unexpected_archive,None,1,port,'127.0.0.1'),
                                    kwargs={'observe':observe,'archive':False},daemon=True)
        receiver.start()
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                deadline = time.monotonic()+2
                while time.monotonic() < deadline and not seen.is_set():
                    for packet in [*roster,timing]:
                        sender.sendto(packet,('127.0.0.1',port))
                    seen.wait(.03)
            self.assertTrue(seen.is_set())
            cache.annotate(snapshot)
            self.assertEqual([p['participant_type'] for p in snapshot['participants']],['human','ai','human'])
            self.assertEqual(types_from_evidence(snapshot),{0:'human',1:'ai',2:'human'})
        finally:
            stop.set()
            receiver.join(2)
        self.assertFalse(receiver.is_alive())
        self.assertEqual(archived, [])
