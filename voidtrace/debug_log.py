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
import queue
import struct
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Iterable, Iterator, Optional

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


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def running_processes() -> Iterator[tuple[int, str]]:
    """(pid, exe file name) of every process this user may look at, from Windows itself
    (EnumProcesses + QueryFullProcessImageNameW). Processes it may not open are skipped."""
    if sys.platform != "win32":
        return
    psapi, kernel32 = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    pids = (wintypes.DWORD * 4096)()
    used = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
        return
    name = ctypes.create_unicode_buffer(1024)
    for pid in pids[: used.value // ctypes.sizeof(wintypes.DWORD)]:
        try:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except (OSError, ctypes.ArgumentError):
            continue
        if not handle:
            continue
        try:
            size = wintypes.DWORD(len(name))
            if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                yield int(pid), name.value.rsplit("\\", 1)[-1]
        finally:
            kernel32.CloseHandle(handle)


def find_warframe_pid(processes: Optional[Callable[[], Iterable[tuple[int, str]]]] = None) -> Optional[int]:
    """The process id of the running game (Warframe.x64.exe), or None."""
    for pid, name in (processes or running_processes)():
        name = name.lower()
        if name.startswith("warframe") and name.endswith(".exe"):
            return pid
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
    """Line source with the same shape as LogTailer: poll() and lines(stop_event).

    Every program that writes debug output (Warframe included) waits after each message until the
    listener has taken it. So a dedicated thread does nothing but take messages and queue them;
    parsing and everything else happen in poll(). Without it the game froze while VoidTrace was
    busy (handling a batch, starting up)."""

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
        self._queue: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._collector = threading.Thread(target=self._collect, name="VoidTrace-DebugOutput", daemon=True)
        self._collector.start()

    def _collect(self) -> None:
        while not self._stop.is_set():
            raw = self._channel.wait(200)
            if raw is not None:
                self._queue.put(raw)

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
            try:
                raw = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
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
        self._stop.set()
        self._collector.join(1.0)
        self._channel.close()
