"""Entry point of the packaged desktop app (Ectype OCR.exe)."""

import multiprocessing
import sys

from ectype.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
