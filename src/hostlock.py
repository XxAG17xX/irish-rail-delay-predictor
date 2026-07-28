"""
hostlock.py — one script at a time against api.irishrail.ie.

The 2 requests/second politeness budget is per HOST, not per script. Running
poll_live.py and harvest_codes.py together silently doubles the rate against a free
service that publishes no rate limit and offers no support. Every script here has carried
a docstring warning about it since the beginning; a warning nobody reads is not a control.

This is the control. Each script takes the lock at startup and refuses to run if another
holds it.

Staleness is decided by heartbeat, not by asking whether a PID is alive. Process liveness
checks are awkward and unreliable across platforms, and a PID can be reused. Instead the
holder touches the lock file periodically; a lock whose heartbeat has not moved for
`stale_after` seconds is treated as abandoned and can be taken over. The default of 15
minutes is comfortably longer than any single poll cycle or backfill progress interval, so
a live holder is never mistaken for a dead one.

Usage:

    with hostlock.acquire("poll_live"):
        while True:
            ...
            hostlock.heartbeat()
"""

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = REPO_ROOT / "data" / ".irishrail-api.lock"
STALE_AFTER = 900  # seconds without a heartbeat before a lock is considered abandoned

_held: Path | None = None


class LockHeld(Exception):
    """Another script holds the API lock."""


def _read(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".lock.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"name": name, "pid": os.getpid(),
                   "acquired": datetime.now().isoformat(timespec="seconds"),
                   "heartbeat": time.time()}, f)
    os.replace(tmp, path)


def heartbeat() -> None:
    """Refresh the held lock. Safe to call often; a no-op if this process holds nothing."""
    if _held is None:
        return
    info = _read(_held)
    if info and info.get("pid") == os.getpid():
        info["heartbeat"] = time.time()
        tmp = _held.with_suffix(".lock.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(info, f)
            os.replace(tmp, _held)
        except OSError:
            pass  # a missed heartbeat is not worth killing a long run over


@contextmanager
def acquire(name: str, path: Path = LOCK_PATH, stale_after: int = STALE_AFTER,
            force: bool = False):
    """Take the API lock for the duration of the block. Raises LockHeld if busy."""
    global _held
    existing = _read(path)
    if existing and not force:
        age = time.time() - float(existing.get("heartbeat", 0))
        if age < stale_after:
            raise LockHeld(
                f"{existing.get('name', '?')} (pid {existing.get('pid', '?')}) has held "
                f"the API lock since {existing.get('acquired', '?')}, last heartbeat "
                f"{age:.0f}s ago.\n"
                f"  The 2 req/s budget is per host — running two collectors doubles it.\n"
                f"  Wait for it to finish, or pass --force-lock if you are certain it died."
            )
        print(f"  ! taking over a stale lock from {existing.get('name', '?')} "
              f"(no heartbeat for {age:.0f}s)")

    _write(path, name)
    _held = path
    try:
        yield
    finally:
        _held = None
        info = _read(path)
        if info and info.get("pid") == os.getpid():
            try:
                path.unlink()
            except OSError:
                pass
