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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..account import MarketError, MyOrder, NotSignedIn, TopOrder, suggested_price, top_orders
from ..icons import eye_icon, platinum_icon
from .widgets import BUTTON_STYLE, Stepper, make_cell, make_table

if TYPE_CHECKING:
    from .market import MarketWindow

TOP_COLUMNS = ["Player", "Rep", "Price", "Rank", "Qty"]
STATUS_DOT = {"ingame": QColor(124, 201, 156), "online": QColor(120, 185, 255)}


class SignInDialog(QDialog):
    def __init__(self, window: "MarketWindow") -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle("Sign in to Warframe.Market")
        self.setStyleSheet(window.styleSheet() + theme.apply(BUTTON_STYLE))
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
        theme.follow(self, BUTTON_STYLE)

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

        self.sell_table = make_table(TOP_COLUMNS)
        self.buy_table = make_table(TOP_COLUMNS)
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
                table.setItem(0, 0, make_cell("Orders not found", color=QColor(143, 163, 194)))
                continue
            for r, o in enumerate(rows):
                table.setItem(r, 0, make_cell(f"●  {o.ingame_name}", color=STATUS_DOT.get(o.status)))
                table.setItem(r, 1, make_cell(str(o.reputation), right=True))
                table.setItem(r, 2, make_cell(str(o.platinum), right=True, icon=platinum_icon(14)))
                table.setItem(r, 3, make_cell("" if o.rank is None else str(o.rank), right=True))
                table.setItem(r, 4, make_cell(str(o.quantity), right=True))
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
    COLUMNS = ["", "Item", "Type", "Rank", "Price", "Qty", "", ""]
    EYE, ITEM, TYPE, RANK, PRICE, QTY, MATCH, DELETE = range(8)

    def __init__(self, window: "MarketWindow") -> None:
        super().__init__()
        self.window_ = window
        self.orders: list[MyOrder] = []
        self.shown: list[MyOrder] = []
        theme.follow(self, BUTTON_STYLE)
        self.table = make_table(self.COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.EYE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.ITEM, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(self.PRICE).setIcon(QIcon(platinum_icon(14)))
        self.table.horizontalHeaderItem(self.EYE).setIcon(QIcon(eye_icon(14, True)))
        self.status = QLabel(objectName="muted")
        self.state_filter = QComboBox()
        for label, value in (("All orders", None), ("Visible", True), ("Hidden", False)):
            self.state_filter.addItem(label, value)
        self.type_filter = QComboBox()
        for label, value in (("Sell and buy", None), ("Sell", "sell"), ("Buy", "buy")):
            self.type_filter.addItem(label, value)
        for box in (self.state_filter, self.type_filter):
            box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            box.currentIndexChanged.connect(lambda _: self._render())
        self.reload = QPushButton("Refresh")
        self.reload.setCursor(Qt.CursorShape.PointingHandCursor)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.state_filter)
        top.addWidget(self.type_filter)
        top.addWidget(self.reload)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        self.reload.clicked.connect(self.refresh)
        # Click a column header to sort by it; click again to flip. Default: visible orders first.
        self.sort_col, self.sort_desc = self.EYE, False
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self.sort_by)
        self._update_header()

    def refresh(self) -> None:
        if not self.window_.account.signed_in:
            self.orders = []
            self._render()
            self.status.setText("Sign in to see your orders.")
            return
        self.status.setText("Loading your orders\u2026")
        self.window_.run_async(self.window_.account.my_orders, self._show, self._failed)

    def _failed(self, exc: Exception) -> None:
        if isinstance(exc, NotSignedIn):
            self.window_.account_changed()
        self.status.setText(str(exc) if isinstance(exc, (MarketError, NotSignedIn)) else
                            "Could not reach Warframe.Market.")

    def _show(self, orders: list) -> None:
        self.orders = orders
        self._render()

    SORTABLE = {EYE, ITEM, TYPE, RANK, PRICE, QTY}
    HEADER_TEXT = {EYE: "", ITEM: "Item", TYPE: "Type", RANK: "Rank", PRICE: "Price", QTY: "Qty"}

    def sort_by(self, column: int) -> None:
        if column not in self.SORTABLE:
            return
        if column == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            # Numbers start highest first (price, rank, quantity); names and state start A-Z / visible first.
            self.sort_col, self.sort_desc = column, column in (self.PRICE, self.RANK, self.QTY)
        self._update_header()
        self._render()

    def _update_header(self) -> None:
        for col, text in self.HEADER_TEXT.items():
            arrow = (" \u25bc" if self.sort_desc else " \u25b2") if col == self.sort_col else ""
            self.table.horizontalHeaderItem(col).setText(text + arrow)
            self.table.horizontalHeaderItem(col).setToolTip("Click to sort")

    def sort_key(self, order: MyOrder):
        item = self.window_.by_id.get(order.item_id)
        name = (item.name if item else order.item_id).casefold()
        value = {
            self.EYE: 0 if order.visible else 1,
            self.ITEM: name,
            self.TYPE: order.type,
            self.RANK: -1 if order.rank is None else order.rank,
            self.PRICE: order.platinum,
            self.QTY: order.quantity,
        }[self.sort_col]
        return value

    def sorted_orders(self, orders: list[MyOrder]) -> list[MyOrder]:
        """Sort by the chosen column; ties keep visible orders first, then go by name."""
        def tie(o: MyOrder):
            item = self.window_.by_id.get(o.item_id)
            return (0 if o.visible else 1, (item.name if item else o.item_id).casefold())
        ordered = sorted(orders, key=tie)  # stable sorts: the tie order survives the main sort
        return sorted(ordered, key=self.sort_key, reverse=self.sort_desc)

    def filtered(self) -> list[MyOrder]:
        state, kind = self.state_filter.currentData(), self.type_filter.currentData()
        return [o for o in self.orders
                if (state is None or o.visible == state) and (kind is None or o.type == kind)]

    def _small_button(self, text: str, name: str = "small") -> QPushButton:
        b = QPushButton(text, objectName=name)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _render(self) -> None:
        self.shown = self.sorted_orders(self.filtered())
        # Drop the old rows (and their eye/button widgets) first: replacing widgets in place left
        # the previous row's eye drawn under the new one after a re-sort.
        for r in range(self.table.rowCount()):
            for c in (self.EYE, self.MATCH, self.DELETE):
                old = self.table.cellWidget(r, c)
                if old is not None:
                    old.hide()
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.shown))
        widths = {self.MATCH: 0, self.DELETE: 0}
        row_h = 0
        for r, o in enumerate(self.shown):
            item = self.window_.by_id.get(o.item_id)
            eye = QToolButton(objectName="eye")
            eye.setIcon(QIcon(eye_icon(18, o.visible)))
            eye.setIconSize(QSize(18, 18))
            eye.setCursor(Qt.CursorShape.PointingHandCursor)
            eye.setToolTip("Visible on Warframe.Market. Click to hide." if o.visible else
                           "Hidden from Warframe.Market. Click to show.")
            eye.clicked.connect(lambda _=False, order=o: self.toggle_visible(order))
            self.table.setCellWidget(r, self.EYE, eye)
            self.table.setItem(r, self.ITEM, make_cell(item.name if item else o.item_id,
                                                   color=None if o.visible else QColor(143, 163, 194)))
            self.table.setItem(r, self.TYPE, make_cell(o.type.capitalize(),
                                                   color=QColor(124, 201, 156) if o.type == "sell" else QColor(120, 185, 255)))
            self.table.setItem(r, self.RANK, make_cell("" if o.rank is None else str(o.rank), right=True))
            self.table.setItem(r, self.PRICE, make_cell(str(o.platinum), right=True, icon=platinum_icon(14)))
            self.table.setItem(r, self.QTY, make_cell(str(o.quantity), right=True))
            match = self._small_button("Match lowest" if o.type == "sell" else "Match highest")
            match.clicked.connect(lambda _=False, order=o: self.reprice(order))
            delete = self._small_button("Delete", "danger")
            delete.clicked.connect(lambda _=False, order=o: self.delete(order))
            self.table.setCellWidget(r, self.MATCH, match)
            self.table.setCellWidget(r, self.DELETE, delete)
            for col, button in ((self.MATCH, match), (self.DELETE, delete)):
                widths[col] = max(widths[col], button.sizeHint().width())
            row_h = max(row_h, match.sizeHint().height(), eye.sizeHint().height())
        # Size columns and rows to the buttons: the table only measures cell text, so buttons
        # placed in cells were clipped ("Match lowes", "Delet").
        header = self.table.horizontalHeader()
        for col, width in widths.items():
            if width:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                self.table.setColumnWidth(col, width + 12)
        self.table.setColumnWidth(self.EYE, 40)
        if row_h:
            self.table.verticalHeader().setDefaultSectionSize(row_h + 10)
        hidden = sum(not o.visible for o in self.orders)
        total = f"{len(self.orders)} orders" + (f", {hidden} hidden" if hidden else "")
        if not self.orders:
            self.status.setText("You have no orders." if self.window_.account.signed_in else self.status.text())
        elif len(self.shown) != len(self.orders):
            self.status.setText(f"Showing {len(self.shown)} of {total}")
        else:
            self.status.setText(total)

    def toggle_visible(self, order: MyOrder) -> None:
        item = self.window_.by_id.get(order.item_id)
        name = item.name if item else "order"
        self.status.setText(f"{'Hiding' if order.visible else 'Showing'} {name}\u2026")
        self.window_.run_async(lambda: self.window_.account.update_order(order.id, visible=not order.visible),
                               lambda _: self.refresh(), self._failed)

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
