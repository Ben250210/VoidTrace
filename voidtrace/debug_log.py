"""Real-time log source: Warframe's debug output instead of the buffered EE.log file.

Warframe writes EE.log in chunks, so a line can reach the file many seconds after it happened (the
second live test saw "Got rewards" arrive together with the screen's close, 15 s later). The game also
sends each log line through the Windows debug-output channel (OutputDebugString). Any process can
listen to that channel; this is what WFInfo (WFInfo/LogCapture.cs) and Sysinternals DebugView do.

Protocol: a listener owns the shared memory "DBWIN_BUFFER" (4096 bytes) and two auto-reset events.
It signals DBWIN_BUFFER_READY; a process calling OutputDebugString writes its PID (4 bytes) and the
NUL-terminated text into the buffer and signals DBWIN_DATA_READY. Only one listener can own the channel
per session, so this fails cleanly when DebugView or WFInfo is already running.

Read-only with respect to the game: it only receives text the game broadcasts.
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Iterator, Optional

log = logging.getLogger(__name__)

BUFFER_SIZE = 4096
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
PAGE_READWRITE = 0x04
FILE_MAP_READ = 0x0004
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PID_REFRESH_S = 5.0


class DebugChannelBusy(RuntimeError):
    """Another debug listener (DebugView, WFInfo, a debugger) already owns the channel."""


def parse_message(raw: bytes) -> tuple[int, str]:
    """Split one DBWIN_BUFFER snapshot into (pid, text). Text stops at the first NUL."""
    pid = struct.unpack_from("<I", raw, 0)[0]
    body = raw[4:]
    end = body.find(b"\0")
    if end != -1:
        body = body[:end]
    return pid, body.decode("utf-8", errors="replace")


def find_warframe_pid() -> Optional[int]:
    import psutil

    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if name.startswith("warframe") and name.endswith(".exe"):
            return proc.info["pid"]
    return None


class _Win32Channel:
    """Owns the DBWIN objects. Windows only."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("debug-output capture is Windows only")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateFileMappingW.restype = wintypes.HANDLE
        k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD, wintypes.LPCWSTR]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k32.SetEvent.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k32 = k32
        self.handles: list[int] = []
        self.view = None

        self.buffer_ready = self._event("DBWIN_BUFFER_READY")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.close()
            raise DebugChannelBusy("another program (DebugView, WFInfo, a debugger) is reading debug output")
        self.data_ready = self._event("DBWIN_DATA_READY")
        mapping = k32.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, BUFFER_SIZE, "DBWIN_BUFFER")
        if not mapping:
            self.close()
            raise OSError(ctypes.get_last_error(), "CreateFileMapping(DBWIN_BUFFER) failed")
        self.handles.append(mapping)
        self.view = k32.MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, BUFFER_SIZE)
        if not self.view:
            self.close()
            raise OSError(ctypes.get_last_error(), "MapViewOfFile(DBWIN_BUFFER) failed")
        self.k32.SetEvent(self.buffer_ready)

    def _event(self, name: str) -> int:
        handle = self.k32.CreateEventW(None, False, False, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), f"CreateEvent({name}) failed")
        self.handles.append(handle)
        return handle

    def wait(self, timeout_ms: int) -> Optional[bytes]:
        """One message, or None on timeout. Releases the buffer for the next writer after reading."""
        if self.k32.WaitForSingleObject(self.data_ready, timeout_ms) != WAIT_OBJECT_0:
            return None
        raw = ctypes.string_at(self.view, BUFFER_SIZE)
        self.k32.SetEvent(self.buffer_ready)
        return raw

    def close(self) -> None:
        if self.view:
            self.k32.UnmapViewOfFile(self.view)
            self.view = None
        for handle in self.handles:
            self.k32.CloseHandle(handle)
        self.handles = []


class DebugLogSource:
    """Line source with the same shape as LogTailer: poll() and lines(stop_event)."""

    blocks = True  # poll() waits up to poll_interval itself; callers need not sleep

    def __init__(
        self,
        poll_interval: float = 0.05,
        pid_finder: Callable[[], Optional[int]] = find_warframe_pid,
        channel_factory: Callable[[], object] = _Win32Channel,
    ) -> None:
        self.poll_interval = poll_interval
        self._pid_finder = pid_finder
        self._channel = channel_factory()
        self._pid: Optional[int] = None
        self._pid_checked = 0.0
        self._partial = ""
        self.messages_seen = 0

    def _warframe_pid(self) -> Optional[int]:
        now = time.monotonic()
        if self._pid is None or now - self._pid_checked > PID_REFRESH_S:
            self._pid = self._pid_finder()
            self._pid_checked = now
        return self._pid

    def poll(self) -> list[str]:
        """Drain messages that arrive within one poll interval; return complete Warframe lines."""
        out: list[str] = []
        deadline = time.monotonic() + self.poll_interval
        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            raw = self._channel.wait(remaining_ms)
            if raw is None:
                break
            pid, text = parse_message(raw)
            if pid != self._warframe_pid():
                continue
            self.messages_seen += 1
            data = self._partial + text
            *lines, self._partial = data.split("\n")
            out.extend(line.rstrip("\r") for line in lines)
        return out

    def lines(self, stop_event: Optional[threading.Event] = None) -> Iterator[str]:
        while stop_event is None or not stop_event.is_set():
            yield from self.poll()

    def close(self) -> None:
        self._channel.close()
