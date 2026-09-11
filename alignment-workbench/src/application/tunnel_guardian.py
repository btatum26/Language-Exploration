"""Standalone SSH owner: EOF on its parent pipe also cleans up after a parent crash.

This helper intentionally imports only the standard library so it can be launched
by file path without depending on application import paths or database settings.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading


def supervise(command: list[str]) -> int:
    parent_closed = threading.Event()

    def watch_parent() -> None:
        try:
            while os.read(0, 1):
                pass
        finally:
            parent_closed.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        while not parent_closed.wait(0.05):
            result = child.poll()
            if result is not None:
                return result
        return 0
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)


if __name__ == "__main__":
    raise SystemExit(supervise(sys.argv[1:]))
