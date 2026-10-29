# PyInstaller spec: the Kraken engine (ectype-kraken.exe), an optional download beside the app,
# holding Kraken and PyTorch (CPU). The app finds it in the user's folder (kraken.py).
#
#   pip install torch --index-url https://download.pytorch.org/whl/cpu
#   pip install kraken pyinstaller
#   pyinstaller packaging/ectype-kraken.spec --noconfirm --distpath dist-kraken --workpath build/pyi-kraken

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

here = Path(SPECPATH)
root = here.parent

datas = [(str(root / name), ".") for name in ("LICENSE", "NOTICE", "CREDITS.md")]
binaries, hidden = [], ["ectype.kraken_worker"]
# Kraken and the packages it loads by name or reads data files from.
for package in ("kraken", "torchvision", "htrmopo", "lightning", "lightning_fabric", "torchmetrics", "coremltools", "safetensors",
                "shapely", "sickle"):
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas, binaries, hidden = datas + d, binaries + b, hidden + h
# Versions read through importlib.metadata (Lightning also checks for optional packages, such as
# rich for its progress bar, by their metadata).
for dist in ("kraken", "torch", "torchvision", "lightning", "lightning-utilities", "torchmetrics", "htrmopo",
             "safetensors", "rich", "click", "numpy", "pillow", "scipy", "scikit-image", "scikit-learn", "shapely",
             "pyarrow", "jsonschema", "jinja2", "requests", "protobuf", "coremltools", "packaging", "regex",
             "platformdirs", "iso639-lang", "threadpoolctl", "pyyaml", "fsspec", "tqdm"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

a = Analysis([str(here / "ectype_kraken.py")], pathex=[str(root / "src")], datas=datas, binaries=binaries,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "IPython", "pytest", "PySide6", "ectype.gui"],
             noarchive=False)
# UTF-8 mode: htrmopo reads its data files without naming an encoding, which fails in Windows'
# own code page.
exe = EXE(PYZ(a.pure), a.scripts, [("X utf8_mode=1", None, "OPTION")], exclude_binaries=True, name="ectype-kraken",
          console=True, upx=False)
COLLECT(exe, a.binaries, a.datas, name="Ectype OCR Kraken", upx=False)
