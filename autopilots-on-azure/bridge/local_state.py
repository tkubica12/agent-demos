from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path


_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


@contextmanager
def locked_file(path: Path):
    """Serialize a local-disk state transaction across threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _GUARD:
        lock = _LOCKS.setdefault(str(path.resolve()), threading.RLock())
    with lock, path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
