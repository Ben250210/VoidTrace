"""Posting and managing your own Warframe.Market orders from the market window.

  * SignInDialog    - email/username + password, sent only to Warframe.Market (see account.py)
  * PostOrderPanel  - top 5 sell/buy orders for one rank (like warframe.market's "Edit order"
                      panel) and a one-click Post with "match lowest", "lowest +/- offset" or a
                      custom price
  * MyOrdersPanel   - your live orders: reprice to the current lowest, or delete
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .account import Account, MarketError, MyOrder, NotSignedIn, TopOrder, suggested_price, top_orders
from .icons import platinum_icon

if TYPE_CHECKING:
    from .market_window import MarketWindow

BUTTON_STYLE = """
QPushButton { background: #2a2f45; border: 1px solid #3a4160; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background: #343a55; }
QPushButton#primary { background: #d9b45c; color: #14161f; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #e6c472; }
QPushButton#danger { color: #f08a6e; }
QSpinBox { background: #171a26; border: 1px solid #2a2f45; border-radius: 4px; padding: 4px 6px; }
QToolButton#step { background: #2a2f45; border: 1px solid #3a4160; border-radius: 4px; font-weight: 700;
                   min-width: 26px; min-height: 24px; }
QToolButton#step:hover { background: #3a4160; }
QToolButton#step:pressed { background: #d9b45c; color: #14161f; }
QRadioButton::indicator, QCheckBox::indicator { width: 14px; height: 14px; }
QRadioButton::indicator { border-radius: 8px; border: 2px solid #6b7190; background: #0f1119; }
QRadioButton::indicator:hover { border-color: #d9b45c; }
QRadioButton::indicator:checked { border: 2px solid #d9b45c;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                stop:0 #d9b45c, stop:0.5 #d9b45c, stop:0.6 #0f1119, stop:1 #0f1119); }
"""
TOP_COLUMNS = ["Player", "Rep", "Price", "Rank", "Qty"]
STATUS_DOT = {"ingame": QColor(124, 201, 156), "online": QColor(143, 163, 255)}


class Stepper(QWidget):
    """A number box with its own - and + buttons.

    The dark theme's QSpinBox styling made Qt misplace the built-in arrows' click areas: the up
    arrow drew in one place and listened in another, so it never responded. Typing and the mouse
    wheel still work on the box itself. Mirrors QSpinBox's value()/setValue()/valueChanged.
    """

    def __init__(self, minimum: int, maximum: int, value: int, step: int = 1) -> None:
        super().__init__()
        self.box = QSpinBox(minimum=minimum, maximum=maximum, value=value)
        self.box.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box.setMinimumWidth(64)
        self.minus = QToolButton(text="\u2212", objectName="step")
        self.plus = QToolButton(text="+", objectName="step")
        for button, delta in ((self.minus, -step), (self.plus, step)):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoRepeat(True)  # hold to keep stepping
            button.clicked.connect(lambda _=False, d=delta: self.box.setValue(self.box.value() + d))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.minus)
        layout.addWidget(self.box)
        layout.addWidget(self.plus)
        self.valueChanged = self.box.valueChanged

    def value(self) -> int:
        return self.box.value()

    def setValue(self, value: int) -> None:  # noqa: N802 (Qt naming)
        self.box.setValue(value)


def _table(columns: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setShowGrid(False)
    t.setIconSize(QSize(14, 14))
    header = t.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for c in range(1, len(columns)):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    return t


def _cell(text: str, right: bool = False, icon=None, color: Optional[QColor] = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if icon is not None:
        item.setIcon(QIcon(icon))
    if color is not None:
        item.setForeground(color)
    return item


class SignInDialog(QDialog):
    def __init__(self, window: "MarketWindow") -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle("Sign in to Warframe.Market")
        self.setStyleSheet(window.styleSheet() + BUTTON_STYLE)
        self.email = QLineEdit(placeholderText="Email or username")
        self.password = QLineEdit(placeholderText="Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.message = QLabel(objectName="muted")
        self.message.setWordWrap(True)
        self.message.setText("Your password goes only to warframe.market and is not saved. "
                             "VoidTrace keeps the session in Windows Credential Manager.")
        self.button = QPushButton("Sign in", objectName="primary")
        layout = QVBoxLayout(self)
        for w in (self.email, self.password, self.message, self.button):
            layout.addWidget(w)
        self.button.clicked.connect(self.submit)
        self.password.returnPressed.connect(self.submit)
        self.resize(380, 0)

    def submit(self) -> None:
        email, password = self.email.text().strip(), self.password.text()
        if not email or not password:
            self.message.setText("Enter your email (or username) and password.")
            return
        self.button.setEnabled(False)
        self.message.setText("Signing in…")
        self.window_.run_async(lambda: self.window_.account.sign_in(email, password),
                               self._done, self._failed)

    def _done(self, name: str) -> None:
        self.password.clear()
        self.window_.account_changed()
        self.accept()

    def _failed(self, exc: Exception) -> None:
        self.password.clear()
        self.button.setEnabled(True)
        self.message.setText(str(exc) if isinstance(exc, (MarketError, NotSignedIn)) else
                             "Could not reach Warframe.Market. Try again in a moment.")


class PostOrderPanel(QWidget):
    def __init__(self, window: "MarketWindow") -> None:
        super().__init__()
        self.window_ = window
        self.top: dict[str, list[TopOrder]] = {"sell": [], "buy": []}
        self._request = 0
        self.setStyleSheet(BUTTON_STYLE)

        self.side = QComboBox()
        self.side.addItem("Sell", "sell")
        self.side.addItem("Buy", "buy")
        self.rank = QComboBox()
        # Grow with "Maxed rank" instead of cutting it to "Maxed r" / "Ma...nk".
        self.rank.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.rank.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.quantity = Stepper(1, 999, 1)
        self.visible = QCheckBox("Visible")
        self.visible.setChecked(True)

        self.mode_match = QRadioButton("Match lowest")
        self.mode_offset = QRadioButton("Lowest +/-")
        self.mode_custom = QRadioButton("Custom")
        self.mode_match.setChecked(True)
        self.modes = QButtonGroup(self)
        for b in (self.mode_match, self.mode_offset, self.mode_custom):
            self.modes.addButton(b)
        self.offset = Stepper(-1000, 1000, -1)
        self.custom = Stepper(1, 100000, 10)
        self.price_label = QLabel()
        self.price_label.setObjectName("title")
        self.post = QPushButton("Post", objectName="primary")
        self.post.setCursor(Qt.CursorShape.PointingHandCursor)
        self.result = QLabel(objectName="toast")
        self.result.setWordWrap(True)

        self.sell_table = _table(TOP_COLUMNS)
        self.buy_table = _table(TOP_COLUMNS)
        for t in (self.sell_table, self.buy_table):
            t.horizontalHeaderItem(2).setIcon(QIcon(platinum_icon(14)))
            t.setMaximumHeight(190)

        form = QGridLayout()
        form.addWidget(QLabel("Order"), 0, 0)
        row0 = QHBoxLayout()
        for w in (self.side, self.rank, QLabel("Qty"), self.quantity, self.visible):
            row0.addWidget(w)
        row0.addStretch(1)
        form.addLayout(row0, 0, 1)
        form.addWidget(QLabel("Price"), 1, 0)
        row1 = QHBoxLayout()
        for w in (self.mode_match, self.mode_offset, self.offset, self.mode_custom, self.custom):
            row1.addWidget(w)
        row1.addStretch(1)
        form.addLayout(row1, 1, 1)
        post_row = QHBoxLayout()
        post_row.addWidget(self.price_label, 1)
        post_row.addWidget(self.post)

        layout = QVBoxLayout(self)
        self.sell_title = QLabel("Top 5 sell orders", objectName="muted")
        self.buy_title = QLabel("Top 5 buy orders", objectName="muted")
        layout.addWidget(self.sell_title)
        layout.addWidget(self.sell_table)
        layout.addWidget(self.buy_title)
        layout.addWidget(self.buy_table)
        layout.addLayout(form)
        layout.addLayout(post_row)
        layout.addWidget(self.result)
        layout.addStretch(1)

        self.side.currentIndexChanged.connect(lambda _: self.update_price())
        self.rank.currentIndexChanged.connect(lambda _: self.refresh())
        for w in (self.mode_match, self.mode_offset, self.mode_custom):
            w.toggled.connect(lambda _: self.update_price())
        self.offset.valueChanged.connect(lambda _: self.update_price())
        self.custom.valueChanged.connect(lambda _: self.update_price())
        self.post.clicked.connect(self.post_order)

    # --- item / rank -----------------------------------------------------------------

    def set_item(self, rank: Optional[int] = None) -> None:
        item = self.window_.item
        self.rank.blockSignals(True)
        self.rank.clear()
        if item is not None and item.max_rank is not None:
            for r in range(item.max_rank + 1):
                self.rank.addItem("Maxed rank" if r == item.max_rank else f"Rank {r}", r)
            # Default to max rank, like warframe.market; follow the rank chosen in the seller list.
            self.rank.setCurrentIndex(item.max_rank if rank is None else rank)
        self.rank.setVisible(item is not None and item.max_rank is not None)
        view = self.rank.view()
        view.setMinimumWidth(view.sizeHintForColumn(0) + 24)
        self.rank.blockSignals(False)
        self.result.setText("")
        self.refresh()

    def selected_rank(self) -> Optional[int]:
        item = self.window_.item
        return self.rank.currentData() if item is not None and item.max_rank is not None else None

    def refresh(self) -> None:
        item = self.window_.item
        if item is None:
            return
        self._request += 1
        request, rank = self._request, self.selected_rank()
        label = "" if rank is None else (" · maxed rank" if rank == item.max_rank else f" · rank {rank}")
        self.sell_title.setText(f"Top 5 sell orders{label}")
        self.buy_title.setText(f"Top 5 buy orders{label}")
        for t in (self.sell_table, self.buy_table):
            t.setRowCount(0)
        self.price_label.setText("Loading prices…")
        self.window_.run_async(lambda: top_orders(self.window_.sellers.client, item.slug, rank),
                               lambda top: self._on_top(request, top),
                               lambda exc: self._on_top_failed(request))

    def _on_top(self, request: int, top: dict) -> None:
        if request != self._request:
            return
        self.top = top
        for table, rows in ((self.sell_table, top["sell"]), (self.buy_table, top["buy"])):
            table.setRowCount(max(1, len(rows)))
            if not rows:
                table.setItem(0, 0, _cell("Orders not found", color=QColor(155, 160, 181)))
                continue
            for r, o in enumerate(rows):
                table.setItem(r, 0, _cell(f"●  {o.ingame_name}", color=STATUS_DOT.get(o.status)))
                table.setItem(r, 1, _cell(str(o.reputation), right=True))
                table.setItem(r, 2, _cell(str(o.platinum), right=True, icon=platinum_icon(14)))
                table.setItem(r, 3, _cell("" if o.rank is None else str(o.rank), right=True))
                table.setItem(r, 4, _cell(str(o.quantity), right=True))
        self.update_price()

    def _on_top_failed(self, request: int) -> None:
        if request == self._request:
            self.price_label.setText("Could not load prices")

    # --- price + post -----------------------------------------------------------------------

    def price(self) -> Optional[int]:
        if self.mode_custom.isChecked():
            return self.custom.value()
        offset = self.offset.value() if self.mode_offset.isChecked() else 0
        return suggested_price(self.top, self.side.currentData(), offset,
                               exclude=self.window_.account.ingame_name)

    def update_price(self) -> None:
        price = self.price()
        side = self.side.currentData()
        if price is None:
            nobody = "Nobody is selling" if side == "sell" else "Nobody is buying"
            self.price_label.setText(f"{nobody} right now: use a custom price")
        else:
            self.price_label.setText(f"{'Sell' if side == 'sell' else 'Buy'} for {price}p")
        signed_in = self.window_.account.signed_in
        self.post.setText("Post" if signed_in else "Sign in to post")
        self.post.setEnabled(price is not None or not signed_in)

    def post_order(self) -> None:
        if not self.window_.account.signed_in:
            self.window_.show_sign_in()
            return
        item, price = self.window_.item, self.price()
        if item is None or price is None or not item.id:
            return
        side, rank, qty, visible = self.side.currentData(), self.selected_rank(), self.quantity.value(), self.visible.isChecked()
        self.post.setEnabled(False)
        self.result.setText("Posting…")
        rank_text = "" if rank is None else f" (rank {rank})"

        def done(order: MyOrder) -> None:
            self.post.setEnabled(True)
            self.result.setText(f"Posted: {side} {item.name}{rank_text} for {order.platinum}p, quantity {order.quantity}.")
            self.window_.orders_panel.refresh()

        def failed(exc: Exception) -> None:
            self.post.setEnabled(True)
            if isinstance(exc, NotSignedIn):
                self.window_.account_changed()
            self.result.setText(str(exc) if isinstance(exc, (MarketError, NotSignedIn)) else
                                "Could not reach Warframe.Market. Nothing was posted.")

        self.window_.run_async(lambda: self.window_.account.create_order(item.id, side, price, qty, rank, visible),
                               done, failed)


class MyOrdersPanel(QWidget):
    COLUMNS = ["Item", "Type", "Rank", "Price", "Qty", "", ""]

    def __init__(self, window: "MarketWindow") -> None:
        super().__init__()
        self.window_ = window
        self.orders: list[MyOrder] = []
        self.setStyleSheet(BUTTON_STYLE)
        self.table = _table(self.COLUMNS)
        self.table.horizontalHeaderItem(3).setIcon(QIcon(platinum_icon(14)))
        self.status = QLabel(objectName="muted")
        self.reload = QPushButton("Refresh")
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.reload)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        self.reload.clicked.connect(self.refresh)

    def refresh(self) -> None:
        if not self.window_.account.signed_in:
            self.table.setRowCount(0)
            self.status.setText("Sign in to see your orders.")
            return
        self.status.setText("Loading your orders…")
        self.window_.run_async(self.window_.account.my_orders, self._show, self._failed)

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, NotSignedIn):
            self.window_.account_changed()
        self.status.setText(str(exc) if isinstance(exc, (MarketError, NotSignedIn)) else
                            "Could not reach Warframe.Market.")

    def _show(self, orders: list) -> None:
        self.orders = orders
        self.table.setRowCount(len(orders))
        for r, o in enumerate(orders):
            item = self.window_.by_id.get(o.item_id)
            name = item.name if item else o.item_id
            self.table.setItem(r, 0, _cell(name + ("" if o.visible else "  (hidden)")))
            self.table.setItem(r, 1, _cell(o.type.capitalize(),
                                           color=QColor(124, 201, 156) if o.type == "sell" else QColor(143, 163, 255)))
            self.table.setItem(r, 2, _cell("" if o.rank is None else str(o.rank), right=True))
            self.table.setItem(r, 3, _cell(str(o.platinum), right=True, icon=platinum_icon(14)))
            self.table.setItem(r, 4, _cell(str(o.quantity), right=True))
            match = QPushButton("Match lowest" if o.type == "sell" else "Match highest")
            match.clicked.connect(lambda _=False, order=o: self.reprice(order))
            delete = QPushButton("Delete", objectName="danger")
            delete.clicked.connect(lambda _=False, order=o: self.delete(order))
            self.table.setCellWidget(r, 5, match)
            self.table.setCellWidget(r, 6, delete)
        self.status.setText(f"{len(orders)} orders" if orders else "You have no orders.")

    def reprice(self, order: MyOrder) -> None:
        item = self.window_.by_id.get(order.item_id)
        if item is None:
            return
        me = self.window_.account.ingame_name
        self.status.setText(f"Repricing {item.name}…")

        def work():
            top = top_orders(self.window_.sellers.client, item.slug, order.rank)
            price = suggested_price(top, order.type, 0, exclude=me)
            if price is None or price == order.platinum:
                return order, False
            return self.window_.account.update_order(order.id, platinum=price), True

        def done(result) -> None:
            updated, changed = result
            self.status.setText(f"{item.name}: now {updated.platinum}p." if changed else
                                f"{item.name}: already at the best price ({updated.platinum}p).")
            self.refresh()

        self.window_.run_async(work, done, self._failed)

    def delete(self, order: MyOrder) -> None:
        item = self.window_.by_id.get(order.item_id)
        self.status.setText("Deleting…")
        self.window_.run_async(lambda: self.window_.account.delete_order(order.id),
                               lambda _: (self.status.setText(f"Deleted {item.name if item else 'order'}."),
                                          self.refresh()),
                               self._failed)
