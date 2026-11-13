"""Lossless AMS2 upload envelope. Keep the server and recorder copies in sync."""
import json
import struct
import zlib

MEDIA_TYPE = "application/vnd.amsleague.capture-v2"
ENCODING = "ams2-v2"
MAGIC = b"AMS2CAP2"
PREFIX = struct.Struct("<8sI")
RECORD = struct.Struct("<H")
HEADER_LIMIT = 4096
MAX_PAYLOAD = 8 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024
MAX_FRAMES = 240
MAX_RAW_BYTES = 32 * 1024


def json_bytes(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def pack(header, records):
    """Encode metadata and raw memory, with an independent first frame per batch."""
    if not 1 <= len(records) <= MAX_FRAMES:
        raise ValueError("Invalid compact capture frame count")
    width = len(records[0][1])
    if not 1 <= width <= MAX_RAW_BYTES:
        raise ValueError("Invalid compact capture dimensions")
    metadata = json_bytes(dict(header, frame_count=len(records), raw_bytes=width))
    if len(metadata) > HEADER_LIMIT:
        raise ValueError("Capture header exceeds the size limit")
    frames = bytearray()
    previous = 0
    for extras, raw in records:
        if len(raw) != width:
            raise ValueError("All snapshots in a compact upload must have the same width")
        extra = json_bytes(extras)
        if len(extra) > 65535:
            raise ValueError("Compact frame metadata exceeds the size limit")
        frames.extend(RECORD.pack(len(extra)))
        frames.extend(extra)
        current = int.from_bytes(raw, "little")
        frames.extend((current ^ previous).to_bytes(width, "little"))
        previous = current
        if len(frames) > MAX_EXPANDED:
            raise ValueError("Expanded compact capture exceeds the size limit")
    payload = PREFIX.pack(MAGIC, len(metadata)) + metadata + zlib.compress(frames, level=3)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Compressed capture exceeds the size limit")
    return payload


def read_header(payload):
    if not PREFIX.size <= len(payload) <= MAX_PAYLOAD:
        raise ValueError("Invalid compact capture length")
    magic, size = PREFIX.unpack_from(payload)
    start = PREFIX.size
    end = start + size
    if magic != MAGIC or not 0 < size <= HEADER_LIMIT or end >= len(payload):
        raise ValueError("Invalid compact capture header")
    header = json.loads(payload[start:end])
    if not isinstance(header, dict) or type(header.get("schema_version")) is not int or header["schema_version"] != 2:
        raise ValueError("Unsupported compact capture version")
    for key in ("batch_id", "run_id", "recorder_id", "event_code"):
        value = header.get(key)
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isascii() or not value.isprintable():
            raise ValueError("Invalid compact capture " + key)
    for key, limit in (("frame_count", MAX_FRAMES), ("raw_bytes", MAX_RAW_BYTES)):
        value = header.get(key)
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError("Invalid compact capture " + key)
    return header, end


def unpack(payload):
    header, start = read_header(payload)
    stream = zlib.decompressobj()
    data = stream.decompress(memoryview(payload)[start:], MAX_EXPANDED + 1)
    if len(data) > MAX_EXPANDED or not stream.eof or stream.unused_data:
        raise ValueError("Invalid or oversized compact capture stream")
    width = header["raw_bytes"]
    offset = previous = 0
    records = []
    for _ in range(header["frame_count"]):
        if offset + RECORD.size > len(data):
            raise ValueError("Truncated compact frame header")
        size, = RECORD.unpack_from(data, offset)
        start = offset + RECORD.size
        end = start + size
        offset = end + width
        if offset > len(data):
            raise ValueError("Truncated compact frame")
        extras = json.loads(data[start:end])
        if not isinstance(extras, dict):
            raise ValueError("Invalid compact frame metadata")
        previous ^= int.from_bytes(data[end:offset], "little")
        records.append((extras, previous.to_bytes(width, "little")))
    if offset != len(data):
        raise ValueError("Unexpected trailing compact frames")
    return header, records
