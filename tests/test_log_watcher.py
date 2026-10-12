import subprocess
import sys
import time
from pathlib import Path

import pytest

from voidtrace.log_watcher import (
    DebouncedFilter,
    LogTailer,
    LogWatcher,
    RewardScreenEvent,
    default_log_path,
    is_reward_screen_line,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "live_2026-10-10_EE_excerpt.log"  # redacted real log


# --- line classification -------------------------------------------------

@pytest.mark.parametrize(
    "line",
    [
        "7.512 Script [Info]: ProjectionRewardChoice.lua: Pause countdown done",
        "12.400 Script [Info]: ProjectionRewardChoice.lua: Got rewards",
    ],
)
def test_trigger_lines_match(line):
    assert is_reward_screen_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "0.101 Sys [Info]: Loading ui/Menu.swf",
        "1.300 Net [Info]: ping 48ms",
        "",
        "Pause countdown started",
        "Got rewardsList is not a trigger",
        "Net [Info]: DeleteSessionCallback(1, {\"rewardSeed\":7554481910643471609})",
    ],
)
def test_noise_lines_rejected(line):
    assert not is_reward_screen_line(line)


def test_default_log_path_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_log_path() == tmp_path / "Warframe" / "EE.log"


# --- debounce ------------------------------------------------------------

def test_debounce_collapses_repeats_within_window():
    filt = DebouncedFilter(debounce=5.0)
    line = "Got rewards"
    assert filt.accept(line, now=0.0)
    assert not filt.accept(line, now=1.0)
    assert not filt.accept(line, now=4.9)


def test_debounce_window_measured_from_last_accepted_event():
    filt = DebouncedFilter(debounce=5.0)
    line = "Pause countdown done"
    assert filt.accept(line, now=0.0)
    assert not filt.accept(line, now=3.0)  # suppressed, must NOT extend the window
    assert filt.accept(line, now=5.0)      # 5s after the accepted event


def test_debounce_ignores_noise_without_consuming_window():
    filt = DebouncedFilter(debounce=5.0)
    assert not filt.accept("Net ping", now=0.0)
    assert filt.accept("Pause countdown done", now=0.1)


# --- tailer ----------------------------------------------------------------

def _append(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(text)


def test_start_at_eof_ignores_existing_content(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("old Pause countdown done\n", encoding="utf-8")
    tailer = LogTailer(log)
    assert tailer.poll() == []
    _append(log, "new Got rewards\n")
    assert tailer.poll() == ["new Got rewards"]
    tailer.close()


def test_reads_from_start_when_start_at_eof_disabled(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("first\nsecond\n", encoding="utf-8")
    tailer = LogTailer(log, start_at_eof=False)
    assert tailer.poll() == ["first", "second"]
    tailer.close()


def test_partial_line_is_held_until_newline(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    tailer = LogTailer(log)
    assert tailer.poll() == []
    _append(log, "Pause countdown do")
    assert tailer.poll() == []
    _append(log, "ne\n")
    assert tailer.poll() == ["Pause countdown done"]
    tailer.close()


def test_crlf_line_endings_are_stripped(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    tailer = LogTailer(log)
    tailer.poll()
    _append(log, "Pause countdown done\r\n")
    assert tailer.poll() == ["Pause countdown done"]
    tailer.close()


def test_truncation_reopens_from_start(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    tailer = LogTailer(log)
    tailer.poll()
    _append(log, "line one\nline two\n")
    assert tailer.poll() == ["line one", "line two"]

    # Game relaunch: file recreated with new, shorter content.
    log.write_text("fresh\n", encoding="utf-8")
    assert tailer.poll() == ["fresh"]
    tailer.close()


def test_missing_file_waits_then_attaches(tmp_path):
    log = tmp_path / "EE.log"
    tailer = LogTailer(log, start_at_eof=False)
    assert tailer.poll() == []
    log.write_text("Pause countdown done\n", encoding="utf-8")
    assert tailer.poll() == ["Pause countdown done"]
    tailer.close()


# --- watcher thread --------------------------------------------------------

def test_watcher_emits_one_event_for_duplicate_trigger(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    events: list[RewardScreenEvent] = []
    watcher = LogWatcher(log, on_event=events.append, debounce=5.0, poll_interval=0.01)
    watcher.start()
    try:
        time.sleep(0.05)
        _append(log, "noise line\nGot rewards\nGot rewards\n")
        _wait_for(lambda: watcher.lines_seen >= 3)
    finally:
        watcher.stop()
    assert len(events) == 1
    assert events[0].raw_line == "Got rewards"


def test_callback_exception_does_not_kill_watcher(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    calls = []

    def bad_callback(event):
        calls.append(event)
        raise RuntimeError("consumer bug")

    watcher = LogWatcher(log, on_event=bad_callback, debounce=0.0, poll_interval=0.01)
    watcher.start()
    try:
        time.sleep(0.05)
        _append(log, "Pause countdown done\n")
        _wait_for(lambda: len(calls) == 1)
        _append(log, "Pause countdown done\n")
        _wait_for(lambda: len(calls) == 2)
    finally:
        watcher.stop()
    assert len(calls) == 2


def test_cli_replay_reports_events(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "stages" / "1_log_watcher.py"), "--replay", str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "1 events" in result.stdout  # one real reward screen


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def test_watcher_emits_close_event_without_debounce(tmp_path):
    log = tmp_path / "EE.log"
    log.write_text("", encoding="utf-8")
    events: list[RewardScreenEvent] = []
    watcher = LogWatcher(log, on_event=events.append, debounce=5.0, poll_interval=0.01)
    watcher.start()
    try:
        time.sleep(0.05)
        _append(log, "330.225 Script [Info]: ProjectionRewardChoice.lua: Got rewards\n")
        _wait_for(lambda: watcher.lines_seen >= 1)
        _append(log, "345.225 Script [Info]: ProjectionRewardChoice.lua: Selection countdown done\n"
                     "345.225 Script [Info]: ProjectionRewardChoice.lua: Relic reward screen shut down\n")
        _wait_for(lambda: watcher.lines_seen >= 3)
    finally:
        watcher.stop()
    assert [e.kind for e in events] == ["open", "close"]


class ListSource:
    """Line source that hands out prepared batches, like a chunked EE.log write."""

    def __init__(self, batches):
        self.batches = list(batches)

    def poll(self):
        return self.batches.pop(0) if self.batches else []

    def close(self):
        pass


def test_open_and_close_in_one_batch_is_stale():
    # Second live test: EE.log delivered the open and the close together, 15 s late.
    events: list[RewardScreenEvent] = []
    watcher = LogWatcher(None, on_event=events.append, source=ListSource([]))
    watcher.handle_batch([
        "2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards",
        "2607.725 Script [Info]: ProjectionRewardChoice.lua: Relic reward screen shut down",
    ])
    assert [e.kind for e in events] == ["close"]
    assert watcher.stale_skipped == 1


def test_trigger_far_behind_newest_line_is_stale():
    events: list[RewardScreenEvent] = []
    watcher = LogWatcher(None, on_event=events.append, source=ListSource([]))
    watcher.handle_batch([
        "2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards",
        "2603.100 Sys [Info]: something else, 10 s later",
    ])
    assert events == [] and watcher.stale_skipped == 1


def test_fresh_trigger_is_emitted_from_a_line_source():
    events: list[RewardScreenEvent] = []
    source = ListSource([["2592.723 Script [Info]: ProjectionRewardChoice.lua: Got rewards",
                          "2592.727 Script [Info]: ProjectionsCountdown.lua: Initialize timer nil\t15"]])
    watcher = LogWatcher(None, on_event=events.append, source=source, poll_interval=0.01)
    watcher.start()
    try:
        _wait_for(lambda: watcher.lines_seen >= 2)
    finally:
        watcher.stop()
    assert [e.kind for e in events] == ["open"]


def test_close_line_is_not_an_open_trigger():
    from voidtrace.log_watcher import is_reward_screen_close

    line = "541.721 Script [Info]: ProjectionRewardChoice.lua: Relic reward screen shut down"
    assert is_reward_screen_close(line) and not is_reward_screen_line(line)


def test_real_log_excerpt_yields_one_open_and_one_close():
    """Redacted EE.log lines from the first live relic run (2026-10-10)."""
    from voidtrace.log_watcher import is_reward_screen_close

    lines = (ROOT / "tests" / "fixtures" / "live_2026-10-10_EE_excerpt.log").read_text(encoding="utf-8").splitlines()
    filt = DebouncedFilter(debounce=5.0)
    opens = [line for line in lines if filt.accept(line, now=float(line.split()[0]))]
    closes = [line for line in lines if is_reward_screen_close(line)]
    assert [l.split(": ", 1)[1] for l in opens] == ["ProjectionRewardChoice.lua: Got rewards"]
    assert len(closes) == 1
    assert len(lines) > 50  # the rest is noise the filter must reject
