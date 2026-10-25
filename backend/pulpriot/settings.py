"""Parameter loading, configuration hash and the shared model version string."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

MODEL_VERSION = "pulpriot-engine-1.1"
ROOT = Path(__file__).resolve().parents[1]            # backend/
PROJECT_ROOT = ROOT.parent                            # repository root
PARAMS_PATH = Path(__file__).parent / "config" / "params.yaml"
WORKBOOK_NAME = "Pulp_Riot_Synthetic_Dataset.xlsx"


def workbook_path() -> Path:
    env = os.environ.get("PULPRIOT_WORKBOOK")
    return Path(env) if env else PROJECT_ROOT / WORKBOOK_NAME


def db_path() -> Path:
    env = os.environ.get("PULPRIOT_DB")
    return Path(env) if env else ROOT / ".cache" / "pulpriot.sqlite"


def cache_dir() -> Path:
    p = ROOT / ".cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class Params:
    """Flat view of params.yaml with source tags kept alongside the values."""
    values: dict[str, Any]
    sources: dict[str, str]

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def with_overrides(self, **kw: Any) -> "Params":
        v = dict(self.values)
        v.update(kw)
        s = dict(self.sources)
        for k in kw:
            s[k] = "ASSUMED"
        return Params(v, s)

    @property
    def config_hash(self) -> str:
        blob = json.dumps(self.values, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def table(self) -> list[dict[str, Any]]:
        return [{"key": k, "value": v, "source": self.sources[k]} for k, v in self.values.items()]


@lru_cache(maxsize=1)
def load_params() -> Params:
    raw = yaml.safe_load(PARAMS_PATH.read_text(encoding="utf-8"))
    return Params({k: v["value"] for k, v in raw.items()}, {k: v["source"] for k, v in raw.items()})
