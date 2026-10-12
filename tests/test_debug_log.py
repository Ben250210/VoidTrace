import struct

from voidtrace.debug_log import BUFFER_SIZE, DebugLogSource, parse_message

WARFRAME_PID = 15780


def raw(pid: int, text: str, junk: bytes = b"\xff" * 40) -> bytes:
    """A DBWIN_BUFFER snapshot: PID, NUL-terminated text, then leftover bytes from earlier writers."""
    body = struct.pack("<I", pid) + text.encode("utf-8") + b"\0" + junk
    return body.ljust(BUFFER_SIZE, b"\0")


class FakeChannel:
    def __init__(self, messages):
        self.messages = list(messages)
        self.closed = False

    def wait(self, timeout_ms):
        return self.messages.pop(0) if self.messages else None

    def close(self):
        self.closed = True


def source(messages):
    return DebugLogSource(pid_finder=lambda: WARFRAME_PID, channel_factory=lambda: FakeChannel(messages))


def test_parse_message_stops_at_nul():
    pid, text = parse_message(raw(42, "hello\n"))
    assert pid == 42 and text == "hello\n"


def test_keeps_only_warframe_lines():
    src = source([
        raw(WARFRAME_PID, "2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards\n"),
        raw(999, "some other program\n"),
        raw(WARFRAME_PID, "2592.727 Script [Info]: ProjectionsCountdown.lua: Initialize timer nil\t15\n"),
    ])
    lines = src.poll()
    assert lines == [
        "2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards",
        "2592.727 Script [Info]: ProjectionsCountdown.lua: Initialize timer nil\t15",
    ]
    assert src.messages_seen == 2


def test_joins_a_line_split_across_messages():
    src = source([raw(WARFRAME_PID, "2592.723 Script [Info]: Projection"),
                  raw(WARFRAME_PID, "RewardChoice.lua: Got rewards\r\n")])
    assert src.poll() == ["2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards"]


def test_no_messages_returns_empty_and_close_releases_channel():
    channel = FakeChannel([])
    src = DebugLogSource(pid_finder=lambda: WARFRAME_PID, channel_factory=lambda: channel)
    assert src.poll() == []
    src.close()
    assert channel.closed


def test_feeds_log_watcher_end_to_end():
    from voidtrace.log_watcher import LogWatcher

    events = []
    src = source([raw(WARFRAME_PID, "2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards\n")])
    watcher = LogWatcher(None, on_event=events.append, source=src)
    watcher.handle_batch(src.poll())
    assert [e.kind for e in events] == ["open"]


# --- finding Warframe without psutil ---------------------------------------------------------------

from voidtrace.debug_log import find_warframe_pid  # noqa: E402


def test_find_pid_picks_warframe():
    assert find_warframe_pid(lambda: [(4, "System"), (77, "Warframe.x64.exe")]) == 77


def test_find_pid_none_when_not_running():
    assert find_warframe_pid(lambda: [(4, "System"), (9, "explorer.exe")]) is None


def test_find_pid_skips_unreadable_processes():
    assert find_warframe_pid(lambda: [(5, ""), (6, ""), (8, "WARFRAME.EXE")]) == 8


def test_find_pid_real_process_list_does_not_raise():
    result = find_warframe_pid()
    assert result is None or isinstance(result, int)
