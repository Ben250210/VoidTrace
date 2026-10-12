"""Notice new whisper conversations from the game log.

Warframe logs `ChatRedux::AddTab: Adding tab with channel name: <name>` when a chat tab opens. Its
own tabs have ids or region codes (C5b4d..., S6acc..., Q_EN_AS); any other name is a player you are
now talking to. Right after login the game rebuilds the tabs you already had, so tabs added within
LOGIN_GRACE_S of the session line are skipped. Each name is announced once per VoidTrace run.
The message text itself is not in the log.
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .log_watcher import game_time

ADD_TAB_RE = re.compile(r"ChatRedux::AddTab: Adding tab with channel name: (\S+) to index")
SYSTEM_TAB_RE = re.compile(r"^(?:[A-Z][0-9a-f]{24}|[A-Z]_[A-Z]{2}_[A-Z]{2})$")
SESSION_MARK = "CHATREDUX - SessionID:"
LOGIN_GRACE_S = 15.0


class WhisperReader:
    def __init__(self) -> None:
        self.session_at: Optional[float] = None
        self.seen: set[str] = set()

    def feed(self, line: str) -> Optional[str]:
        """The player's name when this line opens a new whisper conversation, else None."""
        if SESSION_MARK in line:
            self.session_at = game_time(line)
            return None
        match = ADD_TAB_RE.search(line)
        if not match or SYSTEM_TAB_RE.match(match.group(1)):
            return None
        name, stamp = match.group(1), game_time(line)
        if self.session_at is None or (stamp is not None and stamp - self.session_at < LOGIN_GRACE_S):
            self.seen.add(name)  # a tab restored at login, not a new whisper
            return None
        if name in self.seen:
            return None
        self.seen.add(name)
        return name


class WhisperWatcher(QObject):
    """`feed` runs on the log thread; `new_conversation` arrives on the UI thread."""

    new_conversation = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.reader = WhisperReader()

    def feed(self, line: str) -> None:
        name = self.reader.feed(line)
        if name:
            self.new_conversation.emit(name)
