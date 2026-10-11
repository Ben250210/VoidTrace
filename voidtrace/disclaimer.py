"""The use-at-your-own-risk notice: shown once on first launch (the user must accept it), in the tray
menu, in "How to use.txt" and, in long form, in DISCLAIMER.md and the README."""

TITLE = "VoidTrace: use at your own risk"

SHORT = """VoidTrace is a free, unofficial fan project. It is not made, endorsed or supported by Digital \
Extremes (Warframe) or by Warframe.Market.

It only reads: Warframe's log file and debug output, and what is on your screen. It never changes game \
files, reads or writes game memory, injects into the game, or presses keys or clicks for you. Even so, \
whether any third-party tool is allowed is up to Digital Extremes, and their rules can change at any time.

You use VoidTrace entirely at your own risk. It comes with no warranty of any kind. Its authors and \
contributors are not responsible for anything that happens to your Warframe or Warframe.Market account \
(including warnings, suspensions or bans), for lost platinum, items or trades, for wrong prices, or for \
any other loss or damage from using it.

Following the Warframe EULA and Terms of Service, and Warframe.Market's rules, is your responsibility. \
Prices and riven grades are estimates: always check before you buy or sell."""


def text_for_file() -> str:
    """Plain text for "How to use.txt"."""
    return f"{TITLE.upper()}\n\n" + "\n\n".join("  " + para for para in SHORT.split("\n\n"))
