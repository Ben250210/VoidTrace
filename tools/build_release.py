"""Build the Windows release: a folder with VoidTrace.exe (no Python needed) plus a bundled Tesseract,
zipped for a GitHub Release.

    python tools/build_release.py                    # -> dist/
    python tools/build_release.py --out dist-rivens  # a test build beside the released one

Needs: pip install pyinstaller, and Tesseract installed at the default path (only the parts OCR needs
are copied: tesseract.exe, its DLLs, English and orientation data, about 15 MB).
Output: <out>/VoidTrace/ and <out>/VoidTrace-<version>-windows.zip (out defaults to dist)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voidtrace import __version__, disclaimer  # noqa: E402

TESSERACT_DIR = Path(os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "VoidTrace"


def set_output(folder: Path) -> None:
    """Build into `folder` instead of dist/ (the folder and its VoidTrace/ app dir)."""
    global DIST, APP
    DIST = folder if folder.is_absolute() else ROOT / folder
    APP = DIST / "VoidTrace"

HOW_TO_USE = f"""VoidTrace {__version__}
=================

Prices for Void relic rewards, right on the reward screen, plus a Warframe.Market window.
Read-only towards the game: it reads Warframe's log and takes screenshots. Nothing is injected.

START
  1. Double-click VoidTrace.exe. A blue crystal icon appears in the system tray (bottom right).
     If Windows says "Windows protected your PC", click "More info" then "Run anyway"
     (the app is not code-signed; the source is public on GitHub).
  2. In Warframe, set Display Mode to Borderless Fullscreen.
  3. Open a relic. Prices appear under the reward names. Click a price card to see sellers.

MARKET
  Ctrl+Shift+M (or click the tray icon) opens the market window: search any item, click a seller
  to copy the whisper, paste it in game chat. Sign in to Warframe.Market there to post and manage
  your own orders (your password is not saved). While signed in, a finished trade lowers or removes
  the matching order by itself, with an Undo button (tray > "Update orders after trades" to turn off).
  The Trades tab lists every trade and the plat you made today, this week and overall.
  "Alert me..." on an item: a tray message (click = copy whisper) when someone sells it at or under
  your price. Alerts tab to delete them.

RIVENS
  Ctrl+Shift+R (or tray > Rivens) opens the riven window. With a riven open in the game's Item
  Details view, Ctrl+Shift+R reads it off the screen and prices it. Find rivens to buy (filter by
  stats, curse, clean or rolled), price-check yours, and sell or manage your riven auctions.

SOMETHING WRONG?
  Right-click the tray icon > "Run self-test". It checks OCR, the internet connection and the game log.
  Right-click > "Open log folder" shows voidtrace.log; send that file when reporting a problem.
  WFInfo / Overwolf / AlecaFrame use the same game-log channel: run one of them at a time.

QUIT
  Right-click the tray icon > Quit VoidTrace.

RELICS
  Tray > Relics: a relic's average plat and ducat value per refinement (solo or squad of 4), its
  rewards, and the best relics you can farm now.

ACCENT COLOUR
  Right-click the tray icon > Accent colour: pick a preset or any colour.

""" + disclaimer.text_for_file() + "\n"


def make_icon() -> Path:
    """Multi-size .ico from the VoidTrace logo (Explorer, taskbar and Alt+Tab pick different sizes)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PyQt6.QtWidgets import QApplication

    from voidtrace.icons import voidtrace_logo

    app = QApplication.instance() or QApplication([])  # noqa: F841 (needed to paint)
    BUILD.mkdir(exist_ok=True)
    png = BUILD / "logo-256.png"
    if not voidtrace_logo(256).toImage().save(str(png), "PNG"):
        raise SystemExit("could not render the logo")
    path = BUILD / "voidtrace.ico"
    Image.open(png).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def run_pyinstaller(icon: Path) -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "main_app.py"),
        "--name", "VoidTrace", "--windowed", "--onedir", "--noconfirm", "--clean",
        "--icon", str(icon),
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        "--hidden-import", "keyring.backends.Windows",
        # Riven base values (tools/update_riven_bases.py), read next to voidtrace/rivens.py.
        "--add-data", f"{ROOT / 'voidtrace' / 'data'}{os.pathsep}voidtrace/data",
    ]
    # Optional extras of our dependencies that VoidTrace never uses (pytesseract's pandas output,
    # keyring's Linux backends, ...). Excluding them cuts the download by about 100 MB.
    for module in ("pytest", "tkinter", "pandas", "pyarrow", "cryptography", "secretstorage", "jeepney",
                   "matplotlib", "scipy", "IPython", "PIL.ImageQt"):
        cmd += ["--exclude-module", module]
    subprocess.run(cmd, check=True, cwd=ROOT)


def copy_tesseract() -> None:
    if not (TESSERACT_DIR / "tesseract.exe").exists():
        raise SystemExit(f"Tesseract not found at {TESSERACT_DIR} (set TESSERACT_DIR)")
    target = APP / "tesseract"
    if target.exists():
        shutil.rmtree(target)
    (target / "tessdata").mkdir(parents=True)
    shutil.copy2(TESSERACT_DIR / "tesseract.exe", target)
    for dll in TESSERACT_DIR.glob("*.dll"):
        shutil.copy2(dll, target)
    for name in ("eng.traineddata",):  # orientation detection (osd) is never used
        shutil.copy2(TESSERACT_DIR / "tessdata" / name, target / "tessdata")
    for folder in ("configs", "tessconfigs"):
        if (TESSERACT_DIR / "tessdata" / folder).exists():
            shutil.copytree(TESSERACT_DIR / "tessdata" / folder, target / "tessdata" / folder)
    # Tesseract is Apache-2.0: ship its license with it.
    for lic in ("LICENSE", "LICENSE.txt", "doc/LICENSE"):
        if (TESSERACT_DIR / lic).exists():
            shutil.copy2(TESSERACT_DIR / lic, target / "LICENSE-tesseract.txt")
            break
    else:
        (target / "LICENSE-tesseract.txt").write_text(
            "Tesseract OCR, Apache License 2.0: https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE\n",
            encoding="utf-8")


# Files PyInstaller pulls in that VoidTrace never loads: video codecs, software OpenGL, PDF, QML.
PRUNE = [
    "_internal/cv2/opencv_videoio_ffmpeg*.dll",
    "_internal/PyQt6/Qt6/bin/opengl32sw.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Pdf.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Qml*.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Quick*.dll",
    "_internal/PyQt6/Qt6/plugins/imageformats/qpdf.dll",
]


def prune() -> float:
    freed = 0.0
    for pattern in PRUNE:
        for f in APP.glob(pattern):
            freed += f.stat().st_size / 1e6
            f.unlink()
    return freed


def write_zip() -> Path:
    (APP / "How to use.txt").write_text(HOW_TO_USE, encoding="utf-8")
    zip_path = DIST / f"VoidTrace-{__version__}-windows.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in APP.rglob("*"):
            z.write(f, Path("VoidTrace") / f.relative_to(APP))
    return zip_path


def folder_size(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the VoidTrace Windows release")
    parser.add_argument("--out", type=Path, default=Path("dist"), help="output folder (default: dist)")
    set_output(parser.parse_args().out)
    icon = make_icon()
    run_pyinstaller(icon)
    copy_tesseract()
    print(f"pruned {prune():.0f} MB of unused libraries")
    zip_path = write_zip()
    print(f"\nbuilt {APP} ({folder_size(APP):.0f} MB)")
    print(f"zip   {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")
