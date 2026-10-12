"""The Rivens section's Price check page: type in (or read off the screen) your riven's stats and see
what comparable rivens sell for, with a letter grade per stat."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..rivens import (
    POLARITIES,
    PriceCheck,
    RivenAttr,
    RivenCatalog,
    RivenDraft,
    RivenWeapon,
    attr_rolls,
    buffs_from_name,
    grade,
    grade_from_percent,
    grade_letter,
    grading_weapon,
    price_check,
    riven_grade,
    valid_names,
)
from .widgets import Stepper
from .riven_find import MAX_BUFFS, refill_combo, stat_combo, auction_table, fill_auction_table, grade_color


if TYPE_CHECKING:
    from .rivens import RivenSection

log = logging.getLogger(__name__)


class StatRow:
    """One stat line of the price-check form: stat picker + value."""

    def __init__(self, curse: bool, catalog: RivenCatalog) -> None:
        self.curse = curse
        self.catalog = catalog
        self.box = stat_combo([], [("No curse" if curse else "—", None)])
        self.value = QDoubleSpinBox(minimum=-1000.0, maximum=1000.0, decimals=2)
        self.value.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.value.setMinimumWidth(80)
        self.grade = QLabel()  # how well this stat rolled, e.g. "92%"
        self.grade.setMinimumWidth(48)

    def attr(self) -> Optional[RivenAttr]:
        slug = self.box.currentData()
        if not slug:
            return None
        value = self.value.value()
        stat = self.catalog.by_stat.get(slug)
        flipped = stat is not None and stat.positive_is_negative  # recoil: the curse is a plus
        if self.curse and value > 0 and not flipped:
            value = -value  # curses can be typed as plain numbers; most are a minus
        return RivenAttr(slug, value, not self.curse)


class PricePanel(QWidget):
    sell_requested = pyqtSignal(object, object)  # (RivenDraft, PriceCheck)

    def __init__(self, window: "RivenSection") -> None:
        super().__init__()
        self.window_ = window
        self.check: Optional[PriceCheck] = None
        self._request = 0
        self.shown_for, self.shown_disposition = "", None
        self.name = QLineEdit(placeholderText="Riven name, e.g. Igni-insitron: the buffs fill in from it")
        self.name_note = QLabel()
        self.name_note.setWordWrap(True)
        self.name_note.hide()
        self.rows = [StatRow(False, window.catalog) for _ in range(MAX_BUFFS)] + [StatRow(True, window.catalog)]
        self.mr = Stepper(8, 16, 8)
        self.rolls = Stepper(0, 9999, 0)
        self.rank = Stepper(0, 8, 0)
        self.polarity = QComboBox()
        for p in POLARITIES:
            self.polarity.addItem(p.title(), p)
        self.check_button = QPushButton("Check price", objectName="primary")
        self.sell_button = QPushButton("Sell this riven…")
        self.sell_button.setEnabled(False)
        self.result = QLabel(objectName="title")
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.detail = QLabel(objectName="muted")
        self.detail.setWordWrap(True)
        self.table = auction_table()

        form = QGridLayout()
        form.addWidget(QLabel("Name"), 0, 0)
        form.addWidget(self.name, 0, 1, 1, 3)
        form.addWidget(self.name_note, 1, 1, 1, 3)
        for i, row in enumerate(self.rows):
            form.addWidget(QLabel("Curse" if row.curse else f"Buff {i + 1}"), 2 + i, 0)
            form.addWidget(row.box, 2 + i, 1, 1, 2)
            form.addWidget(row.value, 2 + i, 3)
            form.addWidget(row.grade, 2 + i, 4)
        extras = QHBoxLayout()
        for label, widget in (("MR", self.mr), ("Rolls", self.rolls), ("Rank", self.rank), ("Polarity", self.polarity)):
            extras.addWidget(QLabel(label))
            extras.addWidget(widget)
        extras.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.sell_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(form)
        layout.addLayout(extras)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        layout.addWidget(self.detail)
        layout.addWidget(self.table, 1)

        self.check_button.clicked.connect(self.run_check)
        self.sell_button.clicked.connect(lambda: self.sell_requested.emit(self.draft(), self.check))
        self.name.textEdited.connect(self.fill_from_name)
        for row in self.rows[:MAX_BUFFS]:
            row.box.currentIndexChanged.connect(lambda _: self.check_name())
        for row in self.rows:
            row.box.currentIndexChanged.connect(lambda _: self.update_grades())
            row.value.valueChanged.connect(lambda _: self.update_grades())
        self.rank.valueChanged.connect(lambda _: self.update_grades())

    def my_grade(self) -> Optional[int]:
        draft = self.draft()
        weapon = grading_weapon(self.window_.catalog, draft)  # the Cycle screen's "fits in" strength
        return riven_grade(self.window_.catalog, weapon, draft.attrs, draft.rank) if weapon else None

    def update_grades(self) -> None:
        """Show how far up its random range each stat rolled (0% worst, 100% best)."""
        draft = self.draft()
        weapon = grading_weapon(self.window_.catalog, draft)
        rolls = attr_rolls(self.window_.catalog, weapon, draft.attrs, draft.rank) if weapon else []
        by_slug = {a.slug: r for a, r in zip(draft.attrs, rolls)}
        for row in self.rows:
            slug = row.box.currentData()
            roll = by_slug.get(slug) if slug else None
            if roll is None or row.value.value() == 0:
                row.grade.setText("")
            elif not 0.85 <= roll <= 1.15:
                row.grade.setText("?")  # outside what this riven can roll: a typo, or the wrong rank
                row.grade.setToolTip("This value is outside the range this stat can roll at this rank.")
                row.grade.setStyleSheet("color: #e6b450;")
            else:
                g = grade(roll)
                if row.curse:
                    g = 100 - g  # a smaller curse is the better roll
                row.grade.setText(grade_letter(roll, not row.curse))
                row.grade.setToolTip(f"How well this stat rolled: A+ best, B+ average, C worst "
                                     f"({g}% of the way from the worst possible roll to the best).")
                color = grade_color(g)
                row.grade.setStyleSheet(f"color: {color.name()};")

    # --- the name and the buffs must agree -------------------------------------------------

    def _note(self, text: str, warn: bool = False) -> None:
        self.name_note.setStyleSheet("color: #e6b450;" if warn else "color: #8fa3c2;")
        self.name_note.setText(text)
        self.name_note.setVisible(bool(text))

    def fill_from_name(self, text: str = "") -> None:
        """Pick the buffs the typed name stands for; values and the curse stay yours to enter."""
        name = self.name.text().strip()
        weapon = self.window_.weapon
        slots = buffs_from_name(self.window_.catalog, name, weapon) if name else None
        if slots is None:
            self.check_name()
            return
        catalog = self.window_.catalog
        undecided = []
        for i, row in enumerate(self.rows[:MAX_BUFFS]):
            candidates = slots[i] if i < len(slots) else [None]
            row.box.blockSignals(True)
            if row.box.currentData() not in candidates:
                row.box.setCurrentIndex(max(row.box.findData(candidates[0]) if len(candidates) == 1 else 0, 0))
                row.value.setValue(0.0)  # the old number belonged to the old stat
            row.box.blockSignals(False)
            if len(candidates) > 1 and row.box.currentData() not in candidates:
                undecided.append(" or ".join(catalog.stat_name(c) for c in candidates))
        if undecided:
            self._note(f"Buffs filled in from the name. Pick which it is: {'; '.join(undecided)}.")
        else:
            self._note("Buffs filled in from the name. Enter their values and the curse.")

    def check_name(self) -> None:
        """Warn when the name cannot belong to the chosen buffs (Warframe.Market refuses such a riven)."""
        name = self.name.text().strip().lower()
        positives = [row.box.currentData() for row in self.rows[:MAX_BUFFS] if row.box.currentData()]
        if not name:
            self._note("")
        elif positives and name not in valid_names(self.window_.catalog, positives):
            self._note("This name does not match the buffs picked below. Check both against the game.", warn=True)
        elif not positives and buffs_from_name(self.window_.catalog, name, self.window_.weapon) is None:
            self._note("No buffs this weapon can roll make that name. Check the spelling.", warn=True)
        elif self.name_note.text().startswith("This name") or self.name_note.text().startswith("No buffs"):
            self._note("")

    def set_weapon(self, weapon: RivenWeapon) -> None:
        self.shown_for, self.shown_disposition = "", None  # values typed for this weapon itself
        stats = self.window_.catalog.stats_for(weapon)
        for row in self.rows:
            allowed = [s for s in stats if not (s.positive_only if row.curse else s.negative_only)]
            refill_combo(row.box, allowed, [("No curse" if row.curse else "—", None)])
        self.check = None
        self._note("")
        self.result.setText("")
        self.detail.setText("Type the riven's name to fill in its buffs, then Check price.")
        self.table.setRowCount(0)
        self.sell_button.setEnabled(False)

    def draft(self) -> RivenDraft:
        weapon = self.window_.weapon
        attrs = tuple(a for a in (row.attr() for row in self.rows) if a is not None)
        return RivenDraft(weapon=weapon.slug if weapon else "", name=self.name.text().strip().lower(), attrs=attrs,
                          mr=self.mr.value(), rolls=self.rolls.value(), rank=self.rank.value(),
                          polarity=self.polarity.currentData(), shown_for=self.shown_for,
                          shown_disposition=self.shown_disposition)

    def load_draft(self, draft: RivenDraft) -> None:
        """Fill the form, e.g. from a riven read off the screen."""
        self.shown_for, self.shown_disposition = draft.shown_for, draft.shown_disposition
        self.name.setText(draft.name[:1].upper() + draft.name[1:])  # as the game writes it: "Sati-visicon"
        buffs = [a for a in draft.attrs if a.positive][:MAX_BUFFS]
        curse = [a for a in draft.attrs if not a.positive][:1]
        for row, attr in zip(self.rows, [*buffs, *[None] * (MAX_BUFFS - len(buffs)), *(curse or [None])]):
            index = row.box.findData(attr.slug if attr else None)
            row.box.setCurrentIndex(max(index, 0))
            row.value.setValue(attr.value if attr else 0.0)
        self.mr.setValue(draft.mr)
        self.rolls.setValue(draft.rolls)
        self.rank.setValue(draft.rank)
        self.polarity.setCurrentIndex(max(self.polarity.findData(draft.polarity), 0))
        self.check_name()

    def run_check(self) -> None:
        weapon = self.window_.weapon
        if weapon is None:
            self.detail.setText("Search for the riven's weapon first.")
            return
        draft = self.draft()
        mine = self.my_grade()
        self._request += 1
        request = self._request
        me = self.window_.account.ingame_name if self.window_.account.signed_in else None
        catalog = self.window_.catalog
        self.result.setText("")
        self.detail.setText("Checking listings…")

        def work() -> PriceCheck:
            auctions = self.window_.service.search(weapon.slug)
            return price_check(auctions, draft.positives, draft.negative, exclude_seller=me,
                               catalog=catalog, my_grade=mine)

        self.window_.run_async(work, lambda check: self._show(request, check),
                               lambda exc: self._failed(request, exc))

    def _failed(self, request: int, exc: Exception) -> None:
        if request == self._request:
            self.detail.setText(f"Could not load listings: {exc}")

    def _show(self, request: int, check: PriceCheck) -> None:
        if request != self._request:
            return
        self.check = check
        fill_auction_table(self.table, self.window_.catalog, list(check.comparables))
        self.sell_button.setEnabled(True)
        if check.estimate is None:
            self.result.setText("No listings to compare")
            self.detail.setText("Nobody is selling a riven for this weapon right now.")
            return
        mine = self.my_grade()
        self.result.setText(f"About {check.estimate:g} platinum" +
                            (f'  <span style="color:#8fa3c2; font-size:13px; font-weight:400">your roll: {grade_from_percent(mine)}</span>'
                             if mine is not None else ""))
        extra = f" {len(check.outliers)} extreme price(s) left out." if check.outliers else ""
        if check.exact and check.tier not in ("same stats and curse", "same stats, no curse", "same positive stats"):
            prices = ", ".join(f"{a.price}p" for a in check.exact[:5])
            extra += f" Only {len(check.exact)} with exactly your positive stats: {prices}."
        similar = " that rolled about as well as yours" if check.similar_rolls else ""
        self.detail.setText(
            f"Median of the cheapest {min(check.count, 5)} of {check.count} listings with {check.tier}{similar} "
            f"(range {check.low}-{check.high}p). Rivens have no trade history: these are asking prices.{extra}")
