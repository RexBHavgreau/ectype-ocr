# PyInstaller spec: one program folder holding the desktop app ("Ectype OCR.exe")
# and the command-line tool (ectype.exe), sharing their libraries.
#
#   ECTYPE_TESSERACT_DIR=<folder with tesseract(.exe), its DLLs and tessdata/>
#   ECTYPE_FONTS_DIR=<folder with EBGaramond-*.ttf>
#   pyinstaller packaging/ectype.spec --noconfirm --distpath dist --workpath build/pyi
#
# Both are optional: without them the app looks for Tesseract and fonts on the system.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

here = Path(SPECPATH)
root = here.parent

datas = [(str(root / name), ".") for name in ("LICENSE", "NOTICE", "CREDITS.md")]
# Licence files of the LGPL Qt bindings travel with them.
for dist in ("PySide6_Essentials", "shiboken6"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass
for env, target in (("ECTYPE_TESSERACT_DIR", "tesseract"), ("ECTYPE_FONTS_DIR", "fonts")):
    if os.environ.get(env):
        # Relative to where pyinstaller runs, not to this file (PyInstaller's default).
        datas.append((str(Path(os.environ[env]).resolve()), target))

hidden = collect_submodules("ectype")
excludes = ["tkinter", "matplotlib", "IPython", "pytest", "ots", "pandas", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"]


def analysis(script):
    return Analysis([str(here / script)], pathex=[str(root / "src")], datas=datas, hiddenimports=hidden,
                    excludes=excludes, noarchive=False)


gui_a = analysis("ectype_gui.py")
cli_a = analysis("ectype_cli.py")
gui_exe = EXE(PYZ(gui_a.pure), gui_a.scripts, [], exclude_binaries=True, name="Ectype OCR", console=False,
              upx=False)
cli_exe = EXE(PYZ(cli_a.pure), cli_a.scripts, [], exclude_binaries=True, name="ectype", console=True, upx=False)
COLLECT(gui_exe, cli_exe, gui_a.binaries, gui_a.datas, cli_a.binaries, cli_a.datas, name="Ectype OCR", upx=False)
