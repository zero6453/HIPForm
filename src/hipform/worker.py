"""Internal supervised CLI process; parent loss must stop native computation."""

import os
import sys
import threading

from .cli import main


def main_supervised():
    def watch_parent():
        # The API holds stdin open. EOF also arrives when it is killed abruptly.
        # Raw reads avoid holding a buffered I/O lock during interpreter shutdown.
        while os.read(sys.stdin.fileno(), 1):
            pass
        os._exit(130)

    threading.Thread(target=watch_parent, daemon=True, name="parent-watch").start()
    return main()


if __name__ == "__main__":
    raise SystemExit(main_supervised())
