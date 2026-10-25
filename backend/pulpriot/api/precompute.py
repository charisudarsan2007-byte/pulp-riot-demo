"""Disk cache for the Control tower payload, filled ahead of time by background worker processes.

The Control tower is the landing page and the slowest to compute cold (about 10 s per as-of date). Worker processes
compute it for every replay date, starting at the default date and moving outward, and store the finished JSON. Any
decision (or a reset) bumps the version so stored payloads that carry the old approval state are never served.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from ..settings import cache_dir

ECON = "margin"


def _root() -> Path:
    p = cache_dir() / "control_tower"
    p.mkdir(parents=True, exist_ok=True)
    return p


def version() -> int:
    f = _root() / "version.txt"
    try:
        return int(f.read_text())
    except Exception:                                                        # noqa: BLE001
        return 0


def bump() -> None:
    (_root() / "version.txt").write_text(str(version() + 1))


def path_for(day, econ: str = ECON) -> Path:
    d = _root() / f"v{version()}"
    d.mkdir(exist_ok=True)
    return d / f"{pd.Timestamp(day).date()}_{econ}.json"


def read(day, econ: str = ECON) -> bytes | None:
    p = path_for(day, econ)
    try:
        return p.read_bytes() if p.exists() else None
    except OSError:
        return None


def write(day, body: bytes, econ: str = ECON) -> None:
    p = path_for(day, econ)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, p)


def _lower_priority() -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)   # below normal
    except Exception:                                                        # noqa: BLE001
        pass


def _work(day_s: str) -> str:
    """Runs in a worker process."""
    from .service import get_service
    from .util import clean
    from . import views_control as VC
    day = pd.Timestamp(day_s)
    if read(day) is None:
        v0 = version()
        body = json.dumps(clean(VC.control_tower(get_service(), day, ECON))).encode()
        if version() == v0:
            write(day, body)
    return day_s


def run_all(default_day, lo, hi, workers: int = 4) -> None:
    days = list(pd.date_range(lo, hi))
    days.sort(key=lambda d: abs((d - pd.Timestamp(default_day)).days))
    with ProcessPoolExecutor(max_workers=workers, initializer=_lower_priority) as ex:
        for _ in ex.map(_work, [str(d.date()) for d in days if d != pd.Timestamp(default_day)]):
            pass
