import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from voidtrace import settings, theme


@pytest.fixture(autouse=True)
def own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # never touch the user's real settings
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_settings_round_trip(own_settings):
    assert settings.get("accent") is None
    settings.put("accent", "#ff6b6b")
    assert settings.get("accent") == "#ff6b6b"
    assert (own_settings / "VoidTrace" / "settings.json").exists()


def test_default_accent_is_the_logo_blue():
    assert theme.accent_hex() == theme.DEFAULT_ACCENT
    assert theme.apply("QPushButton { background: #4fa3ff; }") == "QPushButton { background: #4fa3ff; }"


def test_a_chosen_accent_replaces_the_default_everywhere():
    theme.set_accent("#ff6b6b")
    css = theme.apply("a { color: #4fa3ff; } a:hover { color: #74b8ff; } b { color: #06101f; }")
    assert "#4fa3ff" not in css and "#74b8ff" not in css
    assert "#ff6b6b" in css and theme.hover_hex() in css


def test_text_on_the_accent_stays_readable():
    theme.set_accent("#ffffff")
    assert theme.on_accent_hex() == theme.DEFAULT_ON_ACCENT   # dark text on a light accent
    theme.set_accent("#202060")
    assert theme.on_accent_hex() == "#ffffff"                 # white text on a dark one


def test_windows_follow_a_new_accent(qapp):
    button = QPushButton()
    theme.follow(button, "QPushButton { background: #4fa3ff; }")
    assert "#4fa3ff" in button.styleSheet()
    theme.set_accent("#5ccf8f")
    assert "#5ccf8f" in button.styleSheet()


def test_bad_colour_is_refused():
    with pytest.raises(ValueError):
        theme.set_accent("not a colour")


def test_disclaimer_says_no_responsibility_for_bans():
    from voidtrace import disclaimer

    text = disclaimer.SHORT.lower()
    assert "own risk" in text and "ban" in text and "no warranty" in text
    assert "not made, endorsed or supported by digital extremes" in text
