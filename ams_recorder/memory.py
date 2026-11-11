"""Read-only Win32 mapping access. Importable on non-Windows for tests/replay."""
import ctypes as C
import os
import struct
import time

from .schema import SEQUENCE_OFFSET, STRUCT_BYTES, SUPPORTED_VERSION


class MemoryUnavailable(Exception):
    pass


class SnapshotBusy(MemoryUnavailable):
    """AMS2 is updating the shared-memory snapshot."""


class MemoryReader:
    def __init__(self, name="$pcars2$"):
        if os.name != "nt":
            raise RuntimeError("Live AMS2 capture requires Windows. Use --demo or --replay on this OS.")
        if C.sizeof(C.c_void_p) != 8:
            raise RuntimeError("Live capture requires 64-bit Python on Windows.")
        from ctypes import wintypes as W
        self.api = C.WinDLL("kernel32", use_last_error=True)
        self.api.OpenFileMappingW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
        self.api.OpenFileMappingW.restype = W.HANDLE
        self.api.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_size_t]
        self.api.MapViewOfFile.restype = C.c_void_p
        self.api.UnmapViewOfFile.argtypes = [C.c_void_p]
        self.api.UnmapViewOfFile.restype = W.BOOL
        self.api.CloseHandle.argtypes = [W.HANDLE]
        self.api.CloseHandle.restype = W.BOOL
        self.api.VirtualQuery.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
        self.api.VirtualQuery.restype = C.c_size_t
        self.handle = None
        self.address = None
        self.size = 0
        self.name = name

    def connect(self):
        class Region(C.Structure):
            _fields_ = [("base", C.c_void_p), ("allocation", C.c_void_p), ("allocation_protect", C.c_uint32),
                        ("partition", C.c_uint16), ("size", C.c_size_t), ("state", C.c_uint32),
                        ("protect", C.c_uint32), ("type", C.c_uint32)]
        self.handle = self.api.OpenFileMappingW(4, False, self.name)
        if not self.handle:
            raise MemoryUnavailable("Waiting for AMS2. Enable Shared Memory: Project CARS 2.")
        self.address = self.api.MapViewOfFile(self.handle, 4, 0, 0, 0)
        if not self.address:
            self.close()
            raise MemoryUnavailable("Unable to map AMS2 shared memory")
        region = Region()
        if not self.api.VirtualQuery(self.address, C.byref(region), C.sizeof(region)):
            self.close()
            raise MemoryUnavailable("Unable to determine mapping region size")
        self.size = int(region.size)
        if not 8 <= self.size <= 262144:
            self.close()
            raise MemoryUnavailable("Unexpected mapping region size")

    def read(self):
        if not self.address:
            self.connect()
        version = C.c_uint32.from_address(self.address).value
        if version != SUPPORTED_VERSION or self.size < STRUCT_BYTES:
            return C.string_at(self.address, self.size), False
        for _ in range(20):
            before = C.c_uint32.from_address(self.address + SEQUENCE_OFFSET).value
            if before & 1:
                time.sleep(0)
                continue
            raw = C.string_at(self.address, self.size)
            after = C.c_uint32.from_address(self.address + SEQUENCE_OFFSET).value
            if before == after and struct.unpack_from("<I", raw, SEQUENCE_OFFSET)[0] == before:
                return raw, True
        raise SnapshotBusy("No consistent snapshot available; retrying")

    def close(self):
        if self.address:
            self.api.UnmapViewOfFile(self.address)
        if self.handle:
            self.api.CloseHandle(self.handle)
        self.address = self.handle = None
