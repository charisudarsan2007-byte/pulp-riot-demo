"""JSON sanitising for numpy / pandas values."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return None if pd.isna(o) else str(o.date()) if (o.hour == 0 and o.minute == 0) else str(o)
    if o is pd.NaT:
        return None
    if isinstance(o, pd.DataFrame):
        return clean(o.to_dict("records"))
    if isinstance(o, pd.Series):
        return clean(o.tolist())
    if isinstance(o, str) and "—" in o:
        return o.replace(" — ", ", ").replace("—", "-")
    return o
