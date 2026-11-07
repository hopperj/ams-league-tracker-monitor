"""Optional lossless datagram archive. League projections use shared memory."""
import base64
import logging
import socket
import struct

from .spool import Spool

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


def receive(stop, persist, spool_path, max_spool_mb, port=5606, source_ip=None):
    queue=Spool(spool_path)
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.settimeout(.25)
    try:
        sock.bind(("",port))
        LOG.info("Archiving UDP datagrams on port %s%s",port," from "+source_ip if source_ip else "")
        while not stop.is_set():
            # Stop reading instead of silently acknowledging data after the queue budget is exhausted.
            if queue.size_bytes() >= max_spool_mb*1024*1024:
                stop.wait(.5)
                continue
            try:
                data,address=sock.recvfrom(65535)
            except socket.timeout:
                continue
            if source_ip and address[0]!=source_ip:
                continue
            frame=packet_frame(data,address)
            if frame:
                persist(frame,queue)
    except OSError:
        LOG.exception("UDP archiving stopped; shared-memory capture continues")
    finally:
        sock.close();queue.close()
