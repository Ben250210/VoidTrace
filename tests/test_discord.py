import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from tests.test_account import MemoryKeyring
from voidtrace import settings
from voidtrace.discord import DiscordNotifier, is_webhook
from voidtrace.whispers import WhisperReader

URL = "https://discord.com/api/webhooks/123456789/abcDEF_-xyz"


@pytest.fixture(autouse=True)
def own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # never touch the user's real settings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakePost:
    def __init__(self, *statuses):
        self.statuses = list(statuses) or [204]
        self.calls = []

    def __call__(self, url, json, timeout):
        self.calls.append((url, json))
        status = self.statuses.pop(0) if self.statuses else 204
        return SimpleNamespace(status_code=status, json=lambda: {"retry_after": 0.01})


def notifier(post, url=URL):
    n = DiscordNotifier(keyring=MemoryKeyring(), post=post, sleep=lambda s: None)
    n.executor.submit = lambda fn, *a: fn(*a)  # run right away in tests
    if url:
        n.set_url(url)
    return n


def test_webhook_links():
    assert is_webhook(URL)
    assert is_webhook("https://discordapp.com/api/webhooks/1/x")
    assert is_webhook("https://ptb.discord.com/api/webhooks/1/x-y_z")
    assert not is_webhook("https://evil.example/api/webhooks/1/x")
    assert not is_webhook("http://discord.com/api/webhooks/1/x")
    assert not is_webhook("")


def test_send_posts_one_embed(qapp):
    post = FakePost()
    notifier(post).send("whispers", "New conversation", "with **Tenno**")
    ((url, body),) = post.calls
    assert url == URL
    assert body["username"] == "VoidTrace"
    assert body["embeds"][0]["title"] == "New conversation"
    assert body["embeds"][0]["description"] == "with **Tenno**"
    assert isinstance(body["embeds"][0]["color"], int)


def test_turned_off_kind_or_no_link_sends_nothing(qapp):
    post = FakePost()
    n = notifier(post)
    settings.put("discord_trades", False)
    n.send("trades", "Sold", "x")
    notifier(post, url=None).send("alerts", "Alert", "x")
    assert post.calls == []


def test_rate_limit_waits_and_retries_once(qapp):
    post = FakePost(429, 204)
    notifier(post).send("alerts", "Alert", "x")
    assert len(post.calls) == 2


def test_deleted_webhook_is_reported(qapp):
    post = FakePost(404)
    n = notifier(post)
    n.send("alerts", "Alert", "x")
    assert "webhook" in n.last_error.lower()


def test_link_lives_in_the_keyring_not_settings(qapp, tmp_path):
    n = notifier(FakePost())
    assert n.url() == URL
    assert URL not in (tmp_path / "VoidTrace" / "settings.json").read_text(encoding="utf-8") \
        if (tmp_path / "VoidTrace" / "settings.json").exists() else True
    n.set_url(None)
    assert n.url() is None
    with pytest.raises(ValueError):
        n.set_url("https://example.com/not-a-webhook")


# --- whispers -------------------------------------------------------------------------------------

LOGIN = """\
82.218 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: C5b4d14832183db8be609a693 to index 1
82.219 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: A5e752c649b904327c227e41d to index 2
82.220 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: Q_EN_AS to index 3
87.793 Script [Info]: ChatRedux.lua: CHATREDUX - SessionID: 6acc71ab44e2c1c26002aea2
87.793 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: S6acc71ab44e2c1c26002aea2 to index 1
88.100 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: OldFriend to index 6
""".splitlines()


def read(lines):
    reader = WhisperReader()
    return [name for name in map(reader.feed, lines) if name]


def test_login_tabs_are_not_new_whispers():
    assert read(LOGIN) == []


def test_new_whisper_tab_is_announced_once():
    later = ["400.500 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: Kosmo_99 to index 7",
             "420.000 Script [Info]: ChatRedux.lua: ChatRedux::AddTab: Adding tab with channel name: Kosmo_99 to index 7"]
    assert read(LOGIN + later) == ["Kosmo_99"]


def test_settings_panel_saves_and_removes_the_link(qapp):
    from voidtrace.ui.discord import DiscordDialog

    post = FakePost()
    n = notifier(post, url=None)
    dialog = DiscordDialog(None, n)
    dialog.link.setText("nope")
    dialog.save()
    assert n.url() is None and "discord.com/api/webhooks" in dialog.status.text()
    dialog.link.setText(URL)
    dialog.boxes["trades"].setChecked(False)
    dialog.save()
    assert n.url() == URL and settings.get("discord_trades") is False
    dialog.send_test()
    assert post.calls and post.calls[-1][1]["embeds"][0]["title"] == "VoidTrace is connected"
    dialog.remove()
    assert n.url() is None
