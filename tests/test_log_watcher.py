import os
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
FIXTURE = ROOT / "tests" / "fixtures" / "sample_EE.log"


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


def test_fixture_replay_finds_expected_trigger_count():
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    filt = DebouncedFilter(debounce=0.0)
    hits = [line for line in lines if filt.accept(line, now=0.0)]
    assert len(hits) == 3  # one "Pause countdown done" + two "Got rewards"


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
        [sys.executable, str(ROOT / "1_log_watcher.py"), "--replay", str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "3 events" in result.stdout


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")
