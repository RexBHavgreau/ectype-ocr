"""Entry point of the packaged command-line tool (ectype.exe)."""

import multiprocessing
import sys

from ectype.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
