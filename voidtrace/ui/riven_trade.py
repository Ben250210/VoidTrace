"""Selling rivens from the Rivens section (signed in to Warframe.Market):

  * SellPanel       - post a riven auction, filled in from the price check, with one click
  * MyRivensPanel   - your open riven auctions: show/hide, match the cheapest comparable, close
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..account import MarketError, NotSignedIn
from ..icons import eye_icon, platinum_icon
from ..rivens import PriceCheck, RivenAuction, RivenDraft, price_check, valid_names
from .widgets import Stepper, make_cell, make_table
from .widgets import BUFF_COLOR, CURSE_COLOR, MUTED

if TYPE_CHECKING:
    from .rivens import RivenSection


def _error_text(exc: Exception) -> str:
    if isinstance(exc, (MarketError, NotSignedIn, ValueError)):
        return str(exc)
    return "Could not reach Warframe.Market. Try again in a moment."


class SellPanel(QWidget):
    def __init__(self, window: "RivenSection") -> None:
        super().__init__()
        self.window_ = window
        self.draft: Optional[RivenDraft] = None
        self.summary = QLabel("Fill in your riven on the Price check tab, then press “Sell this riven…”.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.name_warning = QLabel()
        self.name_warning.setStyleSheet("color: #e6b450;")  # amber: Post would fail
        self.name_warning.setWordWrap(True)
        self.buyout = Stepper(1, 100000, 100)
        self.direct = QCheckBox("Direct sale (no bidding)")
        self.direct.setChecked(True)
        self.starting = Stepper(1, 100000, 100)
        self.reputation = Stepper(0, 10000, 0)
        self.visible = QCheckBox("Visible on Warframe.Market")
        self.visible.setChecked(True)
        self.note = QLineEdit(placeholderText="Note for buyers (optional)")
        self.post_button = QPushButton("Post riven", objectName="primary")
        self.post_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.result = QLabel(objectName="toast")
        self.result.setWordWrap(True)

        form = QGridLayout()
        form.addWidget(QLabel("Buyout"), 0, 0)
        form.addWidget(self.buyout, 0, 1)
        form.addWidget(self.direct, 0, 2)
        form.addWidget(QLabel("Starting bid"), 1, 0)
        form.addWidget(self.starting, 1, 1)
        form.addWidget(QLabel("Min. reputation"), 2, 0)
        form.addWidget(self.reputation, 2, 1)
        form.addWidget(self.visible, 2, 2)
        form.setColumnStretch(3, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.post_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(self.summary)
        layout.addWidget(self.name_warning)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addStretch(1)

        self.direct.toggled.connect(self._direct_changed)
        self.buyout.valueChanged.connect(self._buyout_changed)
        self.post_button.clicked.connect(self.post)
        self._direct_changed(True)
        self.update_state()

    def load(self, draft: RivenDraft, check: Optional[PriceCheck]) -> None:
        self.draft = draft
        self.result.setText("")
        if check is not None and check.estimate is not None:
            self.buyout.setValue(max(1, round(check.estimate)))
        catalog = self.window_.catalog
        weapon = catalog.by_weapon.get(draft.weapon)
        title = f"{weapon.name if weapon else draft.weapon} {draft.name[:1].upper()}{draft.name[1:]}".strip()
        lines = "<br>".join(
            f'<span style="color:{"#7cc99c" if a.positive else "#f06e5a"}">{catalog.format_attr(a)}</span>'
            for a in draft.attrs)
        self.summary.setText(f'<b>{title}</b> &nbsp;<span style="color:#8fa3c2">MR {draft.mr} · '
                             f'{draft.rolls} rolls · rank {draft.rank} · {draft.polarity}</span><br>{lines}')
        names = valid_names(catalog, draft.positives)
        if not draft.name:
            self.name_warning.setText("Enter the riven's name on the Price check tab.")
        elif names and draft.name.lower() not in names:
            self.name_warning.setText(
                f"“{draft.name}” does not match these buffs, and Warframe.Market will refuse it. "
                f"Names for these buffs: {', '.join(sorted(names))}.")
        else:
            self.name_warning.setText("")
        self.update_state()

    def update_state(self) -> None:
        signed_in = self.window_.account.signed_in
        self.post_button.setText("Post riven" if signed_in else "Sign in to post")
        self.post_button.setEnabled(self.draft is not None or not signed_in)

    def _direct_changed(self, direct: bool) -> None:
        self.starting.setEnabled(not direct)
        if direct:
            self.starting.setValue(self.buyout.value())

    def _buyout_changed(self, value: int) -> None:
        if self.direct.isChecked() or self.starting.value() > value:
            self.starting.setValue(value)

    def post(self) -> None:
        if not self.window_.account.signed_in:
            self.window_.show_sign_in()
            return
        if self.draft is None:
            return
        draft, buyout = self.draft, self.buyout.value()
        starting = buyout if self.direct.isChecked() else self.starting.value()
        reputation, visible, note = self.reputation.value(), self.visible.isChecked(), self.note.text().strip()
        self.post_button.setEnabled(False)
        self.result.setText("Posting…")
        self.window_.run_async(
            lambda: self.window_.account.create_riven_auction(draft, buyout, starting, reputation, visible, note),
            self._posted, self._failed)

    def _posted(self, auction: RivenAuction) -> None:
        self.update_state()
        state = "" if auction.visible else " (hidden)"
        self.result.setText(f"Posted {self.window_.catalog.riven_title(auction)} for {auction.buyout}p{state}. "
                            "It is in My rivens.")
        self.window_.my_rivens.refresh()

    def _failed(self, exc: Exception) -> None:
        self.update_state()
        if isinstance(exc, NotSignedIn):
            self.window_.account_changed()
        self.result.setText(_error_text(exc))


class MyRivensPanel(QWidget):
    COLUMNS = ["", "Riven", "Buffs", "Curse", "Price", "", ""]
    EYE, RIVEN, BUFFS, CURSE, PRICE, MATCH, CLOSE = range(7)
    SORTABLE = {EYE, RIVEN, PRICE}
    HEADER_TEXT = {EYE: "", RIVEN: "Riven", PRICE: "Price"}

    def __init__(self, window: "RivenSection") -> None:
        super().__init__()
        self.window_ = window
        self.auctions: list[RivenAuction] = []
        self.shown: list[RivenAuction] = []
        self.note = ""
        self.table = make_table(self.COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.EYE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.RIVEN, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.BUFFS, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(self.PRICE).setIcon(QIcon(platinum_icon(14)))
        self.table.horizontalHeaderItem(self.EYE).setIcon(QIcon(eye_icon(14, True)))
        self.status = QLabel(objectName="muted")
        self.state_filter = QComboBox()
        for label, value in (("All rivens", None), ("Visible", True), ("Hidden", False)):
            self.state_filter.addItem(label, value)
        self.state_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.state_filter.currentIndexChanged.connect(lambda _: self._render())
        self.reload = QPushButton("Refresh")
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.state_filter)
        top.addWidget(self.reload)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        self.reload.clicked.connect(self.refresh)
        self.sort_col, self.sort_desc = self.EYE, False
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self.sort_by)
        self._update_header()

    def refresh(self, note: str = "") -> None:
        """Reload your auctions. `note` (what just happened) stays in the status line after the reload,
        instead of being replaced by "Loading..." and the count."""
        self.note = note
        if not self.window_.account.signed_in:
            self.auctions = []
            self._render()
            self.status.setText("Sign in to see your riven auctions.")
            return
        self.status.setText(f"{note}  Refreshing…" if note else "Loading your rivens…")
        self.window_.run_async(self.window_.account.my_auctions, self._show, self._failed)

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, NotSignedIn):
            self.window_.account_changed()
        self.status.setText(_error_text(exc))

    def _show(self, auctions: list[RivenAuction]) -> None:
        self.auctions = auctions
        self._render()

    def sort_by(self, column: int) -> None:
        if column not in self.SORTABLE:
            return
        if column == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = column, column == self.PRICE  # prices start highest first
        self._update_header()
        self._render()

    def _update_header(self) -> None:
        for col, text in self.HEADER_TEXT.items():
            arrow = (" ▼" if self.sort_desc else " ▲") if col == self.sort_col else ""
            self.table.horizontalHeaderItem(col).setText(text + arrow)
            self.table.horizontalHeaderItem(col).setToolTip("Click to sort")

    def sorted_auctions(self, auctions: list[RivenAuction]) -> list[RivenAuction]:
        title = self.window_.catalog.riven_title
        ordered = sorted(auctions, key=lambda a: (not a.visible, title(a).casefold()))
        key = {self.EYE: lambda a: not a.visible, self.RIVEN: lambda a: title(a).casefold(),
               self.PRICE: lambda a: a.price or 0}[self.sort_col]
        return sorted(ordered, key=key, reverse=self.sort_desc)

    def _button(self, text: str, name: str = "small") -> QPushButton:
        b = QPushButton(text, objectName=name)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _render(self) -> None:
        state = self.state_filter.currentData()
        self.shown = self.sorted_auctions([a for a in self.auctions if state is None or a.visible == state])
        # Hide the old row widgets before re-rendering, or a re-sort leaves old eyes drawn underneath.
        for r in range(self.table.rowCount()):
            for c in (self.EYE, self.MATCH, self.CLOSE):
                old = self.table.cellWidget(r, c)
                if old is not None:
                    old.hide()
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.shown))
        catalog = self.window_.catalog
        widths = {self.MATCH: 0, self.CLOSE: 0}
        for r, a in enumerate(self.shown):
            eye = QToolButton(objectName="eye")
            eye.setIcon(QIcon(eye_icon(18, a.visible)))
            eye.setIconSize(QSize(18, 18))
            eye.setCursor(Qt.CursorShape.PointingHandCursor)
            eye.setToolTip("Visible on Warframe.Market. Click to hide." if a.visible else
                           "Hidden from Warframe.Market. Click to show.")
            eye.clicked.connect(lambda _=False, auction=a: self.toggle_visible(auction))
            self.table.setCellWidget(r, self.EYE, eye)
            self.table.setItem(r, self.RIVEN, make_cell(catalog.riven_title(a), color=None if a.visible else MUTED))
            self.table.setItem(r, self.BUFFS, make_cell("\n".join(catalog.format_attr(x) for x in a.attrs if x.positive),
                                                    color=BUFF_COLOR))
            self.table.setItem(r, self.CURSE, make_cell("\n".join(catalog.format_attr(x) for x in a.attrs if not x.positive)
                                                    or "none", color=CURSE_COLOR if a.negative else MUTED))
            self.table.setItem(r, self.PRICE, make_cell(str(a.price), right=True, icon=platinum_icon(14)))
            match = self._button("Match lowest")
            match.clicked.connect(lambda _=False, auction=a: self.reprice(auction))
            close = self._button("Close", "danger")
            close.clicked.connect(lambda _=False, auction=a: self.close_auction(auction))
            self.table.setCellWidget(r, self.MATCH, match)
            self.table.setCellWidget(r, self.CLOSE, close)
            for col, button in ((self.MATCH, match), (self.CLOSE, close)):
                widths[col] = max(widths[col], button.sizeHint().width())
        header = self.table.horizontalHeader()
        for col, width in widths.items():
            if width:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                self.table.setColumnWidth(col, width + 12)
        self.table.setColumnWidth(self.EYE, 40)
        self.table.resizeRowsToContents()
        hidden = sum(not a.visible for a in self.auctions)
        total = f"{len(self.auctions)} rivens" + (f", {hidden} hidden" if hidden else "")
        if not self.auctions:
            text = "You have no riven auctions." if self.window_.account.signed_in else self.status.text()
        elif len(self.shown) != len(self.auctions):
            text = f"Showing {len(self.shown)} of {total}"
        else:
            text = total
        self.status.setText(f"{self.note}  {text}" if self.note else text)

    def toggle_visible(self, auction: RivenAuction) -> None:
        self.status.setText(f"{'Hiding' if auction.visible else 'Showing'} "
                            f"{self.window_.catalog.riven_title(auction)}…")
        self.window_.run_async(lambda: self.window_.account.update_auction(auction, visible=not auction.visible),
                               lambda _: self.refresh(), self._failed)

    def reprice(self, auction: RivenAuction) -> None:
        """Set the buyout to the cheapest comparable listing (your own listings are skipped)."""
        title = self.window_.catalog.riven_title(auction)
        me = self.window_.account.ingame_name
        self.status.setText(f"Repricing {title}…")

        def work():
            listings = self.window_.service.search(auction.weapon)
            check = price_check(listings, list(auction.positives), auction.negative, exclude_seller=me)
            if check.low is None or check.low == auction.buyout:
                return auction, False, check
            return self.window_.account.update_auction(auction, buyout_price=check.low), True, check

        def done(result) -> None:
            updated, changed, check = result
            if check.low is None:
                self.refresh(f"{title}: nothing comparable is listed, price unchanged.")
            elif changed:
                self.refresh(f"{title}: now {updated.buyout}p, the cheapest listing with {check.tier}.")
            else:
                self.refresh(f"{title}: already at the cheapest comparable price ({updated.buyout}p).")

        self.window_.run_async(work, done, self._failed)

    def close_auction(self, auction: RivenAuction) -> None:
        title = self.window_.catalog.riven_title(auction)
        self.status.setText(f"Closing {title}…")
        self.window_.run_async(lambda: self.window_.account.close_auction(auction.id),
                               lambda _: self.refresh(f"Closed {title}."),
                               self._failed)
