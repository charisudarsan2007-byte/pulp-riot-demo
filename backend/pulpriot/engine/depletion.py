"""M4b weekly depot-depletion detector: the early-warning signal for GT and MT, which have no outlet data.

    implied_outflow[w] = opening + receipts + transfers_in - transfers_out - write-offs - closing
    zeta[w]            = (log(y + k) - log(expected + k)) / sigma_w
    S_up, S_dn         = one-sided CUSUMs (k = depletion_k, h = depletion_h)

`expected` is the phased monthly baseline for that depot-week (M2, or the M6 launch curve for a new flavor).
A censored week (cover below three days or a logged stockout) never feeds the DOWN update; its outflow is a lower bound.
Pass-through from quick commerce to GT/MT is estimated only after eight overlapping weeks and used only if its 80%
interval excludes zero; otherwise the manual slider (default 0, ASSUMED) applies."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from ..data.loader import Snapshot
from ..settings import Params
from .censor import censor_prob_from_cover, stockout_days
from .common import robust_sigma


def _monday(ts: pd.Series) -> pd.Series:
    return ts - pd.to_timedelta(ts.dt.weekday, unit="D")


def depot_weekly(snap: Snapshot, params: Params) -> pd.DataFrame:
    """One row per (week, depot, flavor) for every week whose closing report has arrived."""
    st = snap.stock_weekly
    if st.empty:
        return pd.DataFrame()
    # A depot that filed a report on a date but lists no lots for a flavor holds zero of it: fill the grid with zeros.
    # (Only flavors the network has ever stocked, and only depots that reported on that date.)
    reported = st[["stock_date", "depot_id"]].drop_duplicates()
    grid = reported.merge(pd.DataFrame({"flavor_id": sorted(st["flavor_id"].unique())}), how="cross")
    have = st.groupby(["stock_date", "depot_id", "flavor_id"], as_index=False)["qty_bottles"].sum()
    close = grid.merge(have, how="left", on=["stock_date", "depot_id", "flavor_id"]).fillna({"qty_bottles": 0.0})
    close = close.rename(columns={"stock_date": "week_end", "qty_bottles": "closing"})
    close = close.sort_values(["depot_id", "flavor_id", "week_end"])
    close["opening"] = close.groupby(["depot_id", "flavor_id"])["closing"].shift(1)
    close["week_start"] = close.groupby(["depot_id", "flavor_id"])["week_end"].shift(1)
    gap = (close["week_end"] - close["week_start"]).dt.days
    close = close[(gap == 7)].copy()
    key = ["week_start", "depot_id", "flavor_id"]
    rc = snap.receipts_weekly.groupby(["week_start", "depot_id", "flavor_id", "source_type"], as_index=False)["bottles_received"].sum()
    rec = rc.pivot_table(index=key, columns="source_type", values="bottles_received", aggfunc="sum", fill_value=0).reset_index()
    for c in ("plant", "transfer"):
        if c not in rec:
            rec[c] = 0
    rec["receipts"] = rec["plant"] + rec["transfer"]
    rec = rec.rename(columns={"transfer": "transfers_in"})[key + ["receipts", "transfers_in"]]
    df = close.merge(rec, how="left", on=key)
    tl = snap.transfer_log
    if len(tl):
        t = tl.copy()
        t["week_start"] = _monday(t["dispatch_date"])
        to = t.groupby(["week_start", "from_depot", "flavor_id"], as_index=False)["qty_bottles"].sum().rename(
            columns={"from_depot": "depot_id", "qty_bottles": "transfers_out"})
        df = df.merge(to, how="left", on=key)
    else:
        df["transfers_out"] = 0
    wo = snap.writeoff_discount
    wo = wo[wo["type"] == "write_off"].copy()
    if len(wo):
        wo["week_start"] = _monday(wo["txn_date"])
        w = wo.groupby(["week_start", "depot_id", "flavor_id"], as_index=False)["bottles"].sum().rename(columns={"bottles": "writeoff"})
        df = df.merge(w, how="left", on=key)
    else:
        df["writeoff"] = 0
    dsp = snap.dispatch_weekly.groupby(["week_start", "depot_id", "flavor_id", "channel_id"], as_index=False)["bottles_dispatched"].sum()
    dp = dsp.pivot_table(index=key, columns="channel_id", values="bottles_dispatched", aggfunc="sum", fill_value=0).reset_index()
    dp = dp.rename(columns={"GT": "disp_GT", "MT": "disp_MT", "QC": "disp_QC"})
    df = df.merge(dp, how="left", on=key)
    for c in ("receipts", "transfers_in", "transfers_out", "writeoff", "disp_GT", "disp_MT", "disp_QC"):
        df[c] = df.get(c, 0)
        df[c] = df[c].fillna(0.0)
    df["dispatch"] = df["disp_GT"] + df["disp_MT"] + df["disp_QC"]
    df["implied_outflow"] = df["opening"] + df["receipts"] - df["transfers_out"] - df["writeoff"] - df["closing"]
    df["balance_gap"] = (df["implied_outflow"] - df["dispatch"]).abs() > np.maximum(2000.0, 0.05 * df["dispatch"].clip(lower=1))
    df = df.sort_values(["depot_id", "flavor_id", "week_start"]).reset_index(drop=True)
    # censoring probability from days of cover (spec M1) and the stockout log
    out = df.groupby(["depot_id", "flavor_id"])["implied_outflow"].transform(lambda s: s.clip(lower=0).rolling(4, min_periods=1).mean() / 7.0)
    df["recent_daily_outflow"] = out
    df["cover_days"] = np.where(out > 0, df["closing"] / out.replace(0, np.nan), np.inf)
    df["p"] = [censor_prob_from_cover(c, params) for c in df["cover_days"]]
    sd = stockout_days(snap)
    if len(sd):
        sd = sd[sd["channel_id"].isin(["GT", "MT"])].copy()
        sd["week_start"] = _monday(sd["date"])
        flagged = sd.groupby(["week_start", "depot_id", "flavor_id"]).size().rename("so_days").reset_index()
        df = df.merge(flagged, how="left", on=["week_start", "depot_id", "flavor_id"])
        df["so_days"] = df["so_days"].fillna(0)
        df.loc[df["so_days"] >= 2, "p"] = 1.0
    else:
        df["so_days"] = 0
    df["censored"] = df["p"] >= 0.5
    df["available_on"] = df["week_end"] + pd.Timedelta(days=1)
    return df


@dataclass
class DepletionResult:
    table: pd.DataFrame           # per week: y, expected, zeta, S_up, S_dn, state, alert fields
    last: dict


def run_depletion(rows: pd.DataFrame, expected: np.ndarray, params: Params) -> DepletionResult:
    """CUSUM over the weekly implied outflow of one (depot, flavor). `expected` aligns with rows (NaN = unavailable)."""
    kappa = params["kappa"]
    k, h, hw = params["depletion_k"], params["depletion_h"], params["cusum_h_watch"]
    y = rows["implied_outflow"].clip(lower=0).to_numpy(dtype=float)
    cens = rows["censored"].to_numpy()
    n = len(rows)
    zeta = np.full(n, np.nan)
    S_up = np.zeros(n)
    S_dn = np.zeros(n)
    sigma = np.full(n, np.nan)
    state = ["NORMAL"] * n
    res = np.full(n, np.nan)
    su = sd = 0.0
    st = "NORMAL"
    for i in range(n):
        if not np.isfinite(expected[i]) or expected[i] < 0:
            S_up[i], S_dn[i], state[i] = su, sd, st
            continue
        res[i] = np.log(y[i] + kappa) - np.log(expected[i] + kappa)
        prev = res[:i][np.isfinite(res[:i])][-26:]
        sg = robust_sigma(prev, 0.08) if len(prev) >= 5 else 0.15
        sg = max(sg, 0.08)
        sigma[i] = sg
        zeta[i] = res[i] / sg
        if not (cens[i] and zeta[i] < 0):                     # a censored week never drives a downward update
            su = max(0.0, su + zeta[i] - k)
            sd = max(0.0, sd - zeta[i] - k) if not cens[i] else sd
        S_up[i], S_dn[i] = su, sd
        if su >= h:
            st = "ALERT_UP"
        elif su >= hw:
            st = "WATCH_UP"
        elif sd >= h:
            st = "ALERT_DOWN"
        elif sd >= hw:
            st = "WATCH_DOWN"
        else:
            st = "NORMAL"
        state[i] = st
    t = rows.copy()
    t["expected"] = expected
    t["zeta"] = zeta
    t["sigma_w"] = sigma
    t["S_up"] = S_up
    t["S_dn"] = S_dn
    t["state"] = state
    t["ratio"] = np.where(expected > 0, y / np.maximum(expected, 1), np.nan)
    last = t.iloc[-1].to_dict() if n else {}
    if n:
        running_down = bool(np.isfinite(last["cover_days"]) and last["cover_days"] < 7 and last["S_up"] >= hw)
        last["running_down_faster_than_plan"] = running_down
    return DepletionResult(t, last)


def depletion_message(flavor: str, depot: str, last: dict, names: tuple[str, str]) -> str:
    f, d = names
    if last.get("running_down_faster_than_plan"):
        return (f"{d} is running down {f} faster than plan: about {last['cover_days']:.1f} days of cover, "
                f"outflow {last['ratio']:.1f}x the phased monthly expectation.")
    if last["state"].endswith("UP"):
        return f"{d} is shipping out {f} at {last['ratio']:.1f}x the phased expectation for {int(last['S_up'] > 0)} or more weeks."
    if last["state"].endswith("DOWN"):
        return f"{d} is shipping out {f} at {last['ratio']:.1f}x the phased expectation; watch for overstock."
    return f"{d} {f}: outflow in line with expectation."


# ---------------------------------------------------------------------------------------------------------
# QC -> GT/MT pass-through
# ---------------------------------------------------------------------------------------------------------
def estimate_pass_through(weekly: pd.DataFrame, min_weeks: int = 8, max_lag: int = 3) -> dict:
    """Regress log(y_GTMT / expected) on lagged log R_QC (lags 0..3 weeks), region-pooled, non-negative coefficients.

    `weekly` columns: region, week, y_ratio_log (GT/MT outflow vs expectation, uncensored weeks only), logR (QC)."""
    if weekly.empty or weekly["week"].nunique() < min_weeks:
        return {"status": "INSUFFICIENT_OVERLAP", "weeks": int(weekly["week"].nunique()) if len(weekly) else 0,
                "rho_hat": None, "se": None, "used": False}
    rows_x, rows_y = [], []
    for reg, g in weekly.groupby("region"):
        g = g.sort_values("week").set_index("week")
        for wk in g.index:
            xs = []
            for L in range(max_lag + 1):
                v = g["logR"].get(wk - pd.Timedelta(days=7 * L), np.nan)
                xs.append(v)
            if np.all(np.isfinite(xs)) and np.isfinite(g.at[wk, "y_ratio_log"]):
                rows_x.append(xs)
                rows_y.append(g.at[wk, "y_ratio_log"])
    if len(rows_y) < min_weeks:
        return {"status": "INSUFFICIENT_OVERLAP", "weeks": len(rows_y), "rho_hat": None, "se": None, "used": False}
    X, y = np.array(rows_x), np.array(rows_y)
    coef, _ = nnls(X, y)
    rho = float(coef.sum())
    resid = y - X @ coef
    dof = max(len(y) - X.shape[1], 1)
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.pinv(X.T @ X)
        se = float(np.sqrt(np.ones(X.shape[1]) @ cov @ np.ones(X.shape[1])))
    except np.linalg.LinAlgError:
        se = np.inf
    lo, hi = rho - 1.2816 * se, rho + 1.2816 * se
    used = lo > 0
    return {"status": "ESTIMATED", "weeks": len(y), "rho_hat": rho, "se": se, "interval80": [lo, hi], "used": bool(used),
            "coef_by_lag": [float(c) for c in coef]}
