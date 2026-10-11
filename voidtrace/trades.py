"""Trades seen in the game log, and the Warframe.Market order changes they call for.

When you accept a trade, Warframe writes the confirmation dialog to its log, over several lines:

    342.423 Script [Info]: Dialog.lua: Dialog::CreateOkCancel(description=Are you sure you want to accept this trade? You are offering:
    Hollow Point (RARE RANK 5)

    and will receive from SomePlayer the following:
    Platinum x 10, title= leftItem=/Menu/Confirm_Item_Ok, rightItem=/Menu/Confirm_Item_Cancel)
    ...
    347.379 Script [Info]: Dialog.lua: Dialog::CreateOk(description=The trade was successful!, ...)

`TradeReader` turns that into a `Trade` once the success line arrives (a cancelled trade never
writes it). `plan_changes` then finds your matching orders: what you gave lowers your sell orders,
what you received lowers your buy orders. An order traded out completely is deleted; `undo` puts
everything back. Nothing is changed in the game: this only reads its log.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from .account import Account, MyOrder
from .items import CatalogItem
from .log_watcher import game_time

log = logging.getLogger(__name__)

TRADE_START = "Are you sure you want to accept this trade? You are offering:"
TRADE_DONE = "The trade was successful!"
DIALOG_END = ", title="
RECEIVE_RE = re.compile(r"^and will receive from (?P<who>.+?) the following:\s*$")
ITEM_RE = re.compile(r"^(?P<name>.+?)(?: \((?P<tag>[^()]*)\))?(?: x ?(?P<qty>\d+))?$")
RANK_RE = re.compile(r"\bRANK (\d+)\b")
LOG_LINE_RE = re.compile(r"^\d+\.\d+ \w+ \[")  # "342.423 Script [Info]: ..." (a new log line)
PLATINUM = "platinum"
# A confirmation that is not followed by "successful" within this many game seconds is dropped.
PENDING_TIMEOUT_S = 120.0
MAX_DIALOG_LINES = 30  # a trade holds at most 6 items a side


@dataclass(frozen=True)
class TradeItem:
    name: str
    quantity: int = 1
    rank: Optional[int] = None


@dataclass(frozen=True)
class Trade:
    partner: str
    gave: tuple[TradeItem, ...]
    got: tuple[TradeItem, ...]

    @property
    def plat_given(self) -> int:
        return sum(i.quantity for i in self.gave if i.name.casefold() == PLATINUM)

    @property
    def plat_got(self) -> int:
        return sum(i.quantity for i in self.got if i.name.casefold() == PLATINUM)


def parse_item(line: str) -> Optional[TradeItem]:
    """One item line of the dialog: "Hollow Point (RARE RANK 5)", "Forma Blueprint x 3", "Platinum x 10"."""
    line = line.strip()
    match = ITEM_RE.match(line)
    if not line or match is None:
        return None
    rank = RANK_RE.search(match["tag"] or "")
    return TradeItem(match["name"].strip(), int(match["qty"] or 1), int(rank.group(1)) if rank else None)


class TradeReader:
    """Feed it every log line; it returns a Trade on the line that says the trade went through."""

    def __init__(self) -> None:
        self._collecting = False
        self._lines = 0
        self._side: list[TradeItem] = []
        self._gave: list[TradeItem] = []
        self._got: list[TradeItem] = []
        self._partner = ""
        self._pending: Optional[Trade] = None
        self._pending_at: Optional[float] = None

    def feed(self, line: str) -> Optional[Trade]:
        if TRADE_START in line:
            self._collecting, self._partner, self._lines = True, "", 0
            self._gave, self._got = [], []
            self._side = self._gave
            self._pending = None
            self._pending_at = game_time(line)
            rest = line.split(TRADE_START, 1)[1]
            if rest.strip():
                self._take(rest)
            return None
        if self._collecting:
            self._lines += 1
            # A new log line, or far too many lines: the dialog text ended without its closing part.
            if LOG_LINE_RE.match(line) or self._lines > MAX_DIALOG_LINES:
                self._collecting = False
                log.warning("trade dialog in the log was cut short; ignoring it")
            else:
                self._take(line)
            return None
        if self._pending is not None:
            stamp = game_time(line)
            if stamp is not None and self._pending_at is not None and stamp - self._pending_at > PENDING_TIMEOUT_S:
                self._pending = None
            elif TRADE_DONE in line:
                trade, self._pending = self._pending, None
                return trade
        return None

    def _take(self, text: str) -> None:
        ended = DIALOG_END in text
        text = text.split(DIALOG_END, 1)[0]
        receive = RECEIVE_RE.match(text.strip())
        if receive:
            self._partner, self._side = receive["who"], self._got
        else:
            item = parse_item(text)
            if item is not None:
                self._side.append(item)
        if ended:
            self._collecting = False
            if self._partner:
                self._pending = Trade(self._partner, tuple(self._gave), tuple(self._got))


# --- matching your orders ----------------------------------------------------------------------

def item_finder(catalog: Sequence[CatalogItem]) -> Callable[[str], Optional[CatalogItem]]:
    """Trade-window name -> Warframe.Market item. Exact name, else the name + " Blueprint"
    (prime parts are listed as "... Systems Blueprint" on the market)."""
    by_name = {item.name.casefold(): item for item in catalog if item.id}

    def find(name: str) -> Optional[CatalogItem]:
        key = name.casefold().strip()
        return by_name.get(key) or by_name.get(key + " blueprint")

    return find


@dataclass(frozen=True)
class OrderChange:
    order: MyOrder
    item_name: str
    traded: int

    @property
    def new_quantity(self) -> int:
        return max(0, self.order.quantity - self.traded)

    @property
    def deletes(self) -> bool:
        return self.new_quantity == 0

    def describe(self) -> str:
        after = "order removed" if self.deletes else f"order {self.order.quantity} → {self.new_quantity}"
        return f"{self.item_name} ×{self.traded}: {after}"


def _best_order(orders: Iterable[MyOrder], item: CatalogItem, side: str, rank: Optional[int],
                taken: set[str]) -> Optional[MyOrder]:
    candidates = [o for o in orders if o.item_id == item.id and o.type == side and o.id not in taken]
    if rank is not None:
        same = [o for o in candidates if o.rank == rank]
        if same:
            return same[0]
        candidates = [o for o in candidates if o.rank is None]
    return candidates[0] if candidates else None


def plan_changes(trade: Trade, orders: Sequence[MyOrder],
                 find: Callable[[str], Optional[CatalogItem]]) -> list[OrderChange]:
    """Items you gave lower your sell orders; items you received lower your buy orders."""
    changes: list[OrderChange] = []
    taken: set[str] = set()
    for side, items in (("sell", trade.gave), ("buy", trade.got)):
        for traded in items:
            if traded.name.casefold() == PLATINUM:
                continue
            item = find(traded.name)
            if item is None:
                log.info("trade: %r is not a Warframe.Market item; no order to update", traded.name)
                continue
            order = _best_order(orders, item, side, traded.rank, taken)
            if order is None:
                continue
            taken.add(order.id)
            changes.append(OrderChange(order, item.name, min(traded.quantity, order.quantity)))
    return changes


def apply_changes(account: Account, changes: Sequence[OrderChange]) -> list[OrderChange]:
    """Make the changes on Warframe.Market; returns the ones that went through."""
    done = []
    for change in changes:
        try:
            if change.deletes:
                account.delete_order(change.order.id)
            else:
                account.update_order(change.order.id, quantity=change.new_quantity)
        except Exception as exc:
            log.warning("trade: could not update the %s order: %s", change.item_name, exc)
            continue
        done.append(change)
    return done


def undo(account: Account, changes: Sequence[OrderChange]) -> None:
    """Put the orders back as they were (a deleted order is posted again with the same details)."""
    for change in changes:
        o = change.order
        if change.deletes:
            account.create_order(o.item_id, o.type, o.platinum, o.quantity, o.rank, o.visible)
        else:
            account.update_order(o.id, quantity=o.quantity)


def summary(trade: Trade, changes: Sequence[OrderChange]) -> str:
    """Toast text: what happened in the trade, then one line per order changed."""
    gave = ", ".join(f"{i.name} ×{i.quantity}" for i in trade.gave if i.name.casefold() != PLATINUM)
    got = ", ".join(f"{i.name} ×{i.quantity}" for i in trade.got if i.name.casefold() != PLATINUM)
    if gave and not got:
        head = f"Sold {gave} to {trade.partner}" + (f" for {trade.plat_got}p" if trade.plat_got else "")
    elif got and not gave:
        head = f"Bought {got} from {trade.partner}" + (f" for {trade.plat_given}p" if trade.plat_given else "")
    else:
        head = f"Traded with {trade.partner}"
    return "\n".join([head] + [c.describe() for c in changes])
