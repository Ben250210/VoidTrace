import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace import app_support


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_logging_goes_to_a_file(appdata):
    path = app_support.setup_logging()
    logging.getLogger("voidtrace").info("hello from the test")
    for h in logging.getLogger().handlers:
        h.flush()
    assert path == appdata / "VoidTrace" / "voidtrace.log"
    assert "hello from the test" in path.read_text(encoding="utf-8")
    for h in list(logging.getLogger().handlers):  # don't leak our file handler into other tests
        logging.getLogger().removeHandler(h)
        h.close()


def test_only_one_copy_can_hold_the_lock(appdata, qapp):
    first = app_support.acquire_single_instance()
    assert first is not None
    assert app_support.acquire_single_instance() is None   # second copy is refused
    first.unlock()
    again = app_support.acquire_single_instance()
    assert again is not None
    again.unlock()


def test_self_test_reports_each_check_without_stopping(monkeypatch):
    from voidtrace import cv_cleaner

    monkeypatch.setattr(cv_cleaner, "find_tesseract", lambda: None)
    monkeypatch.setattr("voidtrace.items.load_items", lambda: (_ for _ in ()).throw(ConnectionError("offline")))
    results = app_support.self_test(game_log_status="live game log in use by this VoidTrace")
    by_name = {name: (ok, detail) for name, ok, detail in results}
    assert by_name["Tesseract OCR"] == (False, "Tesseract OCR not found")
    assert by_name["Warframe.Market"] == (False, "offline")
    assert by_name["Game log"] == (True, "live game log in use by this VoidTrace")
    text = app_support.format_results(results)
    assert "FAIL  Tesseract OCR" in text and "OK    Game log" in text and "voidtrace.log" in text


def test_self_test_passes_through_a_log_fallback_as_failure():
    results = app_support.self_test(game_log_status="FAIL: using the slower log file")
    assert ("Game log", False, "using the slower log file") in results


def test_bundled_tesseract_is_found_next_to_the_exe(tmp_path, monkeypatch):
    import sys

    from voidtrace import cv_cleaner

    exe = tmp_path / "VoidTrace.exe"
    exe.write_text("")
    (tmp_path / "tesseract").mkdir()
    (tmp_path / "tesseract" / "tesseract.exe").write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    assert cv_cleaner.find_tesseract() == str(tmp_path / "tesseract" / "tesseract.exe")
