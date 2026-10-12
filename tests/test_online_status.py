import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from voidtrace.online_status import INGAME_FOR_S, RENEW_AFTER_S, StatusClient, StatusError, StatusKeeper


class FakeSocket:
    """Answers like Warframe.Market did in the live check."""

    def __init__(self, current="invisible", accept_token=True, accept_status=True):
        self.current = current
        self.accept_token = accept_token
        self.accept_status = accept_status
        self.sent = []
        self.inbox = [{"route": "@wfm|event/reports/online", "payload": {"total": 1}}]
        self.closed = False

    def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg["route"] == "@wfm|cmd/auth/signIn":
            if self.accept_token:
                self.inbox += [{"route": "@wfm|cmd/auth/signIn:ok"},
                               {"route": "@wfm|event/status/set", "payload": {"status": self.current}}]
            else:
                self.inbox.append({"route": "@wfm|cmd/auth/signIn:error", "payload": {"error": "bad"}})
        elif msg["route"] == "@wfm|cmd/status/set":
            if self.accept_status:
                self.current = msg["payload"]["status"]
                self.inbox.append({"route": "@wfm|cmd/status/set:ok", "payload": {"status": self.current}})
            else:
                self.inbox.append({"route": "@wfm|cmd/status/set:error", "payload": {"error": "no"}})

    def recv(self):
        if not self.inbox:
            raise TimeoutError("nothing to read")
        return json.dumps(self.inbox.pop(0))

    def close(self):
        self.closed = True


def test_client_signs_in_sets_and_returns_the_old_status():
    sock = FakeSocket("online")
    before = StatusClient(lambda: sock).set("tok", "ingame", INGAME_FOR_S)
    assert before == "online" and sock.current == "ingame" and sock.closed
    assert sock.sent[0]["payload"] == {"token": "tok"}
    assert sock.sent[1]["route"] == "@wfm|cmd/status/set"
    assert sock.sent[1]["payload"] == {"status": "ingame", "duration": INGAME_FOR_S}


def test_client_errors():
    with pytest.raises(StatusError):
        StatusClient(lambda: FakeSocket(accept_token=False)).set("tok", "ingame")
    with pytest.raises(StatusError):
        StatusClient(lambda: FakeSocket(accept_status=False)).set("tok", "ingame")
    with pytest.raises(ValueError):
        StatusClient(lambda: FakeSocket()).set("tok", "away")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_keeper(sock, running, enabled=True, token="tok"):
    clock = Clock()
    keeper = StatusKeeper(lambda: token, lambda: running[0], lambda: enabled, StatusClient(lambda: sock), clock)
    keeper.executor.submit = lambda fn, *a: fn(*a)  # run right away
    return keeper, clock


def run(qapp, keeper):
    keeper.tick()
    qapp.processEvents()


def test_game_start_and_stop(qapp):
    sock, running = FakeSocket("online"), [False]
    keeper, clock = make_keeper(sock, running)
    run(qapp, keeper)
    assert sock.sent == []  # game not running: nothing to do
    running[0] = True
    run(qapp, keeper)
    assert sock.current == "ingame" and keeper.restore_to == "online"
    count = len(sock.sent)
    run(qapp, keeper)
    assert len(sock.sent) == count  # already in game: no repeat
    clock.t += RENEW_AFTER_S
    run(qapp, keeper)
    assert len(sock.sent) == count + 2 and sock.current == "ingame"  # renewed
    running[0] = False
    run(qapp, keeper)
    assert sock.current == "online"  # back to what it was


def test_was_already_in_game_goes_invisible_after(qapp):
    sock, running = FakeSocket("ingame"), [True]
    keeper, _ = make_keeper(sock, running)
    run(qapp, keeper)
    running[0] = False
    run(qapp, keeper)
    assert sock.current == "invisible"


def test_off_or_signed_out_does_nothing(qapp):
    sock = FakeSocket("invisible")
    keeper, _ = make_keeper(sock, [True], enabled=False)
    run(qapp, keeper)
    keeper2, _ = make_keeper(sock, [True], token=None)
    run(qapp, keeper2)
    assert sock.sent == []


def test_turning_it_off_while_playing_puts_status_back(qapp):
    sock, running = FakeSocket("invisible"), [True]
    keeper, _ = make_keeper(sock, running)
    run(qapp, keeper)
    keeper.is_enabled = lambda: False
    run(qapp, keeper)
    assert sock.current == "invisible"


def test_failures_back_off(qapp):
    sock, running = FakeSocket(accept_status=False), [True]
    keeper, clock = make_keeper(sock, running)
    run(qapp, keeper)
    assert keeper.shown is None and keeper.retry_at == clock.t + 5
    sent = len(sock.sent)
    run(qapp, keeper)
    assert len(sock.sent) == sent  # waiting out the backoff
    clock.t += 5
    run(qapp, keeper)
    assert keeper.retry_at == clock.t + 15


def test_quit_puts_status_back(qapp):
    sock, running = FakeSocket("online"), [True]
    keeper, _ = make_keeper(sock, running)
    run(qapp, keeper)
    keeper.stop()
    assert sock.current == "online"
