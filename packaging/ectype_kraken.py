"""Entry point of the packaged Kraken engine (ectype-kraken.exe), an optional download."""

import multiprocessing
import sys

from ectype.kraken_worker import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
