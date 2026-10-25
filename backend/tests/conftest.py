import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.mkdtemp(prefix="pulpriot_test_")
os.environ["PULPRIOT_DB"] = str(Path(_tmp) / "test.sqlite")

from pulpriot.api.service import get_service      # noqa: E402
from pulpriot.data.loader import get_dataset      # noqa: E402


@pytest.fixture(scope="session")
def ds():
    return get_dataset()


@pytest.fixture(scope="session")
def svc():
    return get_service()


@pytest.fixture(scope="session")
def run_spike(svc):
    """Replay day inside the Dragon Passion surge."""
    return svc.run("2026-03-24")


@pytest.fixture(scope="session")
def run_calm(svc):
    return svc.run("2026-03-10")
