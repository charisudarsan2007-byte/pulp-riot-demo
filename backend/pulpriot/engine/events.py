"""M4 spike and drop detection plus the event scenario mixture.

Two one-sided CUSUMs run in parallel on the standardised residual against a frozen baseline:
    S_up[d] = max(0, S_up[d-1] + zeta[d] - k)        S_dn[d] = max(0, S_dn[d-1] - zeta[d] - k)
State flow: NORMAL -> WATCH -> ONSET -> PEAK -> DECAY | SUSTAINED -> REVERTED. A stockout-flagged day can never lower
the level, feed the DOWN CUSUM, or count as evidence of decay (red line 5). No half-life is fitted: the event size
over the next 7/14/28 days comes from a weighted mixture of F1 transient (2/4/8-day half-life), F2 plateau and
F3 saturating growth."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..settings import Params
from .common import rng_for, summarise
from .qc_daily import QCSeries, noise_sigma, weekday_effects

STATES = ["NORMAL", "WATCH", "ONSET", "PEAK", "DECAY", "SUSTAINED", "REVERTED"]
SCEN_NAMES = ["F1 transient (2d half-life)", "F1 transient (4d half-life)", "F1 transient (8d half-life)",
              "F2 plateau", "F3 saturating growth"]


# ---------------------------------------------------------------------------------------------------------
# pure helpers (unit-tested against the spec numbers)
# ---------------------------------------------------------------------------------------------------------
def cusum_step(S: float, zeta: float, k: float, up: bool = True) -> float:
    return max(0.0, S + (zeta if up else -zeta) - k)


def first_crossing(zetas: np.ndarray, k: float, h: float, up: bool = True) -> int | None:
    """1-based index of the first step at which the CUSUM reaches h, else None."""
    S = 0.0
    for i, z in enumerate(zetas, start=1):
        S = cusum_step(S, z, k, up)
        if S >= h:
            return i
    return None


def scenario_weights(logR: np.ndarray, models: list[np.ndarray], sigma_R: float, prior: np.ndarray,
                     temper: float, floor: float) -> np.ndarray:
    """u_k = prior_k exp(temper * loglik_k);  w_k = floor + (1 - K floor) * u_k / sum(u). Every w_k >= floor, sum = 1."""
    K = len(models)
    ll = np.array([float(np.sum(-0.5 * ((logR - np.log(m)) / sigma_R) ** 2 - np.log(sigma_R))) for m in models])
    ll = ll - ll.max()                                  # numerical stability; the ratio u_k / sum(u) is unchanged
    u = prior * np.exp(temper * ll)
    w_norm = u / u.sum()
    return floor + (1.0 - K * floor) * w_norm


def floor_weights(w_norm: np.ndarray, floor: float) -> np.ndarray:
    K = len(w_norm)
    return floor + (1.0 - K * floor) * np.asarray(w_norm, dtype=float)


def scenario_curves(R_p: float, R_max: float, j: np.ndarray, half_lives=(2, 4, 8), f3_half: float = 7.0) -> list[np.ndarray]:
    """R_j for j days after the peak: three F1 transients, F2 plateau, F3 saturating growth."""
    j = np.asarray(j, dtype=float)
    f1 = [1.0 + (R_p - 1.0) * 0.5 ** (j / tau) for tau in half_lives]
    f2 = np.full_like(j, R_p)
    f3 = R_p + (R_max - R_p) * (1.0 - 0.5 ** (j / f3_half))
    return f1 + [f2, f3]


def r_max_for(R_p: float, g: float, params: Params, onset: bool, up: bool, hidden_peak: bool = False) -> float:
    mult = params["scen_rmax_mult_hidden"] if (hidden_peak and up) else params["scen_rmax_mult"]
    if up:
        return min(R_p * np.exp(14.0 * g), mult * R_p) if onset else mult * R_p
    lim = max(R_p * np.exp(14.0 * g), R_p / mult) if onset else R_p / mult
    return lim


def prior_weights(params: Params) -> np.ndarray:
    return np.array([1 / 9, 1 / 9, 1 / 9, 1 / 3, 1 / 3])


# ---------------------------------------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------------------------------------
@dataclass
class LaunchBaseline:
    """Launch-aware baseline from M6. `prior` is the unshifted analog curve (units/day) for this series."""
    prior: np.ndarray
    weekday_mult: np.ndarray                 # per-day multiplier (mean 1)
    shift_fn: Callable[[int], float]         # day index -> shift using only weeks completed before that day
    start_idx: int                           # first index on/after launch
    age_days: int                            # launch_age_days; the series leaves launch mode at start_idx + age


@dataclass
class Detection:
    df: pd.DataFrame
    final: dict
    series: QCSeries
    s_daily: np.ndarray
    weekday: np.ndarray
    a_frozen: float
    a_level: float
    sigma_e: float
    launch_mode_end: bool
    shift_last: float = 0.0


def run_detector(series: QCSeries, s_daily: np.ndarray, params: Params, launch: LaunchBaseline | None = None
                 ) -> Detection:
    kappa = params["kappa"]
    k, h, h_w, h_lo = params["cusum_k"], params["cusum_h"], params["cusum_h_watch"], params["cusum_lower_h"]
    G = params["qc_level_gain"]
    guard_days = int(params["month_boundary_guard_days"])
    G_b = params["month_boundary_gain"]
    min_ratio = params["event_min_ratio"]
    eps = params["stage_eps"]
    T = len(series.q)
    dates = series.dates
    dows = dates.dayofweek.to_numpy()
    z = np.log(np.where(np.isfinite(series.q), series.q, np.nan) + kappa)
    cens = series.cens >= 0.5
    t0 = launch.start_idx if launch else 0
    burn = 7 if launch else 14                 # launch: the baseline is unanchored until the first launch week is observed

    resid_hist = np.full(T, np.nan)
    resid0 = np.full(T, np.nan)
    ctrl = np.zeros(T, dtype=bool)
    wk = np.zeros(7)

    state, direction = "NORMAL", None
    S_up = S_dn = 0.0
    a = float(np.nanmean(z[t0:t0 + 14] - s_daily[t0:t0 + 14])) if not launch else 0.0
    a_frozen = a
    quiet = 0
    watch_days = 0
    onset_i = peak_i = sustained_i = None
    peak_R = np.nan
    ostage_run = 0                          # consecutive g<eps (ONSET) days
    flat_run = 0
    revert_run = 0
    logR_hist: list[tuple[int, float]] = []  # (idx, log R) uncensored, within the event
    rows = []
    R_hist: list[float] = []
    shifted_events: list[dict] = []
    sig = params["sigma_e_floor"]
    in_launch_prev = False
    shift_frozen = 0.0
    sig_ctrl = params["sigma_e_floor_launch"] if launch else 0.10

    for i in range(t0, T):
        if not np.isfinite(z[i]):
            continue
        in_launch = bool(launch) and (i - launch.start_idx) < launch.age_days
        if launch and in_launch_prev and not in_launch:      # leaving launch mode: express the baseline as a level
            sh_ = launch.shift_fn(i) if state == "NORMAL" else shift_frozen
            a_launch = float(np.log(max(launch.prior[i] * np.exp(sh_) * launch.weekday_mult[i], 0) + kappa) - s_daily[i] - wk[dows[i]])
            a_frozen = a_launch                               # an open event keeps the launch baseline reached at this point
            if state == "NORMAL":
                a = a_launch
        in_launch_prev = in_launch
        w = wk[dows[i]]

        if in_launch:
            shift = launch.shift_fn(i) if state == "NORMAL" else shift_frozen
            pred = float(np.log(launch.prior[i] * np.exp(shift) * launch.weekday_mult[i] + kappa))
            if state == "NORMAL":
                shift_frozen = shift
            base_log = pred - s_daily[i] - w
            if state == "NORMAL":
                a = base_log                                  # keep a level on record so a freeze has something to freeze
        else:
            base = a if state == "NORMAL" else a_frozen
            pred = base + s_daily[i] + w
            base_log = base

        resid = z[i] - pred
        n_hist = max(i - t0, 0)
        sig = noise_sigma(resid_hist[t0:i], ctrl[t0:i], params, in_launch) if n_hist >= 5 else \
            (params["sigma_e_floor_launch"] if in_launch else 0.10)
        zeta = resid / sig
        censored_down = bool(cens[i]) and resid < 0

        # Month-boundary guard (tuned on this feed, see params): monthly volumes step at the month boundary, so for the
        # first few days of a month in NORMAL state the level re-anchors quickly and the CUSUMs hold.
        in_guard = (not in_launch) and state == "NORMAL" and dates[i].day <= guard_days and (i - t0) > 14
        in_burn = (i - t0) < burn
        if not censored_down and not in_guard and not in_burn:
            S_up = cusum_step(S_up, zeta, k, True)
            S_dn = cusum_step(S_dn, zeta, k, False)

        base_units = max(np.exp(pred) - kappa, 1.0)
        R = float((series.q[i]) / base_units)
        R_hist.append(R if not cens[i] else np.nan)

        # level update: only when not frozen, not censored-down, outside launch mode
        if state == "NORMAL" and not in_launch and not censored_down:
            a = a + (G_b if in_guard else G) * (z[i] - s_daily[i] - w - a)
        a_prev_for_freeze = a

        # ---- state machine -------------------------------------------------------------------------
        if i - t0 >= burn:
            if state == "NORMAL":
                if S_up >= h_w or S_dn >= h_w:
                    state, direction = "WATCH", "UP" if S_up >= S_dn else "DOWN"
                    a_frozen = a
                    quiet = 0
            r3 = np.nanmean(R_hist[-3:]) if len(R_hist) and np.isfinite(R_hist[-3:]).any() else 1.0
            gate_up, gate_dn = r3 >= min_ratio, r3 <= 1.0 / min_ratio
            if state in ("NORMAL", "WATCH"):
                if S_up >= h and gate_up and not cens[i]:
                    state, direction, onset_i = "ONSET", "UP", i
                    ostage_run = flat_run = revert_run = 0
                    logR_hist = []
                elif S_dn >= h_lo and gate_dn and not cens[i]:
                    state, direction, onset_i = "ONSET", "DOWN", i
                    ostage_run = flat_run = revert_run = 0
                    logR_hist = []
            if state == "WATCH":
                direction = "UP" if S_up >= S_dn else "DOWN"
                watch_days += 1
                quiet = quiet + 1 if (S_up == 0.0 and S_dn == 0.0) else 0
                if quiet >= params["unfreeze_days"]:
                    state, direction = "NORMAL", None
                    a = a_frozen
                    watch_days = 0
                elif watch_days >= params["sustained_rebase_days"]:
                    # a moderate shift (below the event gate) that persists is rebased, otherwise WATCH never ends
                    clean = [j for j in range(max(t0, i - 6), i + 1) if not cens[j] and np.isfinite(z[j])]
                    if len(clean) >= 3:
                        a = float(np.mean([z[j] - s_daily[j] - wk[dows[j]] for j in clean]))
                    state, direction = "NORMAL", None
                    S_up = S_dn = 0.0
                    watch_days = 0
            else:
                watch_days = 0
        # ---- stage logic for an open event ------------------------------------------------------------
        if state in ("ONSET", "PEAK", "DECAY", "SUSTAINED"):
            if not cens[i] and R > 0:
                logR_hist.append((i, float(np.log(R))))
            pts = logR_hist[-5:]
            g = np.nan
            if len(pts) >= 3:
                xs = np.array([p[0] for p in pts], dtype=float)
                ys = np.array([p[1] for p in pts])
                g = float(np.polyfit(xs, ys, 1)[0])
            gd = g if direction == "UP" else -g if np.isfinite(g) else np.nan
            if not cens[i] and np.isfinite(gd):
                if state == "ONSET":
                    ostage_run = ostage_run + 1 if gd < eps else 0
                    if ostage_run >= 3:
                        state, peak_i = "PEAK", i
                        last3 = [np.exp(p[1]) for p in logR_hist[-3:]]
                        peak_R = float(np.mean(last3))
                elif state in ("PEAK", "DECAY", "SUSTAINED"):
                    if state in ("PEAK", "DECAY") and gd < -eps:
                        state = "DECAY"
                        flat_run = 0
                    # persistent shift: flat for 7 days and clear of the revert band. The spec also requires R > 1.5;
                    # a smaller persistent shift must still be rebased or the event never closes.
                    if abs(g) < eps and abs(np.log(max(R, 1e-9))) > 2 * sig:
                        flat_run += 1
                        if flat_run >= 7 and state != "SUSTAINED":
                            state, sustained_i = "SUSTAINED", i
                    elif state != "SUSTAINED":
                        flat_run = 0
                    # A five-day slope on noisy daily data is rarely under stage_eps seven days running, so a level that
                    # has simply stopped moving also counts as flat: the last 7 uncensored days against the 7 before.
                    if state in ("PEAK", "DECAY") and len(logR_hist) >= 14:
                        m1 = float(np.mean([p_[1] for p_ in logR_hist[-7:]]))
                        m0 = float(np.mean([p_[1] for p_ in logR_hist[-14:-7]]))
                        if abs(m1 - m0) < 0.08 and abs(m1) > 2 * sig:
                            state, sustained_i = "SUSTAINED", i
            if not cens[i] and abs(np.log(max(R, 1e-9))) <= 2 * sig:
                revert_run += 1
            else:
                revert_run = 0 if not cens[i] else revert_run
            if revert_run >= 5 and state in ("PEAK", "DECAY", "SUSTAINED", "ONSET"):
                state_label = "REVERTED"
                clean = [j for j in range(max(t0, i - 4), i + 1) if not cens[j] and np.isfinite(z[j])]
                if clean:
                    a = float(np.mean([z[j] - s_daily[j] - wk[dows[j]] for j in clean]))
                rows.append(_row(i, dates, series, z, pred, resid, sig, zeta, S_up, S_dn, state_label, direction,
                                 R, g, base_units, a_frozen, a, in_launch, cens[i], onset_i, peak_i, peak_R))
                state, direction = "NORMAL", None
                S_up = S_dn = 0.0
                onset_i = peak_i = sustained_i = None
                peak_R = np.nan
                logR_hist = []
                revert_run = 0
                resid_hist[i] = resid
                ctrl[i] = False
                continue
            if state == "SUSTAINED" and sustained_i is not None and (i - sustained_i) >= params["sustained_rebase_days"]:
                clean = [j for j in range(max(t0, i - 6), i + 1) if not cens[j] and np.isfinite(z[j])]
                if len(clean) >= 3:
                    new_a = float(np.mean([z[j] - s_daily[j] - wk[dows[j]] for j in clean]))
                    shifted_events.append({"date": dates[i], "old_level": a_frozen, "new_level": new_a,
                                           "log_shift": new_a - a_frozen, "direction": direction})
                    rows.append(_row(i, dates, series, z, pred, resid, sig, zeta, S_up, S_dn, "SUSTAINED", direction,
                                     R, g, base_units, a_frozen, new_a, in_launch, cens[i], onset_i, peak_i, peak_R,
                                     closed="SUSTAINED_SHIFT"))
                    a = new_a
                    a_frozen = new_a
                    state, direction = "NORMAL", None
                    S_up = S_dn = 0.0
                    onset_i = peak_i = sustained_i = None
                    peak_R = np.nan
                    logR_hist = []
                    resid_hist[i] = resid
                    continue
        else:
            g = np.nan

        resid_hist[i] = resid
        resid0[i] = z[i] - (a if state == "NORMAL" else a_frozen) - s_daily[i] if not in_launch else resid
        ctrl[i] = (state == "NORMAL") and not cens[i] and S_up < 1.0 and S_dn < 1.0 and not in_burn   # in control: nothing accumulating
        if (i - t0) % 7 == 0 and (i - t0) >= params["weekday_min_days"] and not in_launch:
            wk = weekday_effects(resid0[t0:i + 1], dows[t0:i + 1], ctrl[t0:i + 1], params)
        rows.append(_row(i, dates, series, z, pred, resid, sig, zeta, S_up, S_dn, state, direction, R,
                         g if state in ("ONSET", "PEAK", "DECAY", "SUSTAINED") else np.nan, base_units, a_frozen, a,
                         in_launch, cens[i], onset_i, peak_i, peak_R, burn=in_burn))

    df = pd.DataFrame(rows)
    final = _final(df, series, params, shifted_events)
    shift_last = 0.0
    if launch:
        shift_last = shift_frozen if state != "NORMAL" else float(launch.shift_fn(T))
    return Detection(df, final, series, s_daily, wk, a_frozen, a, sig, bool(launch) and in_launch_prev, shift_last)


def _row(i, dates, series, z, pred, resid, sig, zeta, S_up, S_dn, state, direction, R, g, base_units, a_frozen, a,
         in_launch, cens_i, onset_i, peak_i, peak_R, closed=None, burn=False) -> dict:
    return {"burn": bool(burn), "idx": i, "date": dates[i], "available_on": dates[i] + pd.Timedelta(days=1), "q": float(series.q[i]),
            "cens": float(series.cens[i]), "pred_log": float(pred), "baseline_units": float(base_units),
            "resid": float(resid), "sigma_e": float(sig), "zeta": float(zeta), "S_up": float(S_up), "S_dn": float(S_dn),
            "state": state, "direction": direction, "R": float(R), "g": g, "a": float(a), "a_frozen": float(a_frozen),
            "launch_mode": bool(in_launch), "censored": bool(cens_i), "onset_idx": onset_i, "peak_idx": peak_i,
            "peak_R": peak_R, "closed": closed}


def tier_for(row: dict, params: Params, latched: str | None = None) -> str:
    """Ratio-based alert tiers: WATCH 1.5x, ALERT 2x, CRITICAL 3x (DOWN mirrors at 1/1.5, 1/2, 1/3)."""
    t1, t2, t3 = params["ratio_tiers"]
    R, st, d = row["R"], row["state"], row["direction"]
    order = {"NONE": 0, "WATCH": 1, "ALERT": 2, "CRITICAL": 3}
    tier = "NONE"
    if row.get("burn"):
        return "NONE"                         # the launch baseline is unanchored until the first launch week is observed
    open_ev = st in ("ONSET", "PEAK", "DECAY", "SUSTAINED")
    if d == "DOWN" or (R < 1 and not row["censored"]):
        if row["S_dn"] >= params["cusum_h_watch"] or (not row["censored"] and R <= 1 / t1):
            tier = "WATCH"
        if (open_ev and d == "DOWN") or (not row["censored"] and R <= 1 / t2):
            tier = "ALERT"
        if not row["censored"] and R <= 1 / t3:
            tier = "CRITICAL"
    else:
        if row["S_up"] >= params["cusum_h_watch"] or R >= t1:
            tier = "WATCH"
        if (open_ev and d == "UP") or R >= t2:
            tier = "ALERT"
        if R >= t3:
            tier = "CRITICAL"
    if latched and order[latched] > order[tier] and open_ev:
        tier = latched
    return tier


def _final(df: pd.DataFrame, series: QCSeries, params: Params, shifted: list) -> dict:
    """Summary of the last processed day, with tiers latched to the highest reached while an event stays open."""
    if df.empty:
        return {"state": "NORMAL", "direction": None, "tier": "NONE", "tiers": []}
    tiers, latched = [], None
    for r in df.to_dict("records"):
        if r["state"] in ("NORMAL", "REVERTED", "WATCH") and r["direction"] is None:
            latched = None
        t = tier_for(r, params, latched)
        if r["state"] in ("ONSET", "PEAK", "DECAY", "SUSTAINED"):
            latched = t if (latched is None or ["NONE", "WATCH", "ALERT", "CRITICAL"].index(t) >
                            ["NONE", "WATCH", "ALERT", "CRITICAL"].index(latched)) else latched
        tiers.append(t)
    df["tier"] = tiers
    last = df.iloc[-1].to_dict()
    return {"state": last["state"], "direction": last["direction"], "tier": last["tier"], "R": last["R"],
            "S_up": last["S_up"], "S_dn": last["S_dn"], "date": last["date"], "shifts": shifted}


def first_tier_dates(det: Detection) -> dict:
    df = det.df
    out = {}
    for t in ("WATCH", "ALERT", "CRITICAL"):
        m = df[df["tier"] == t] if "tier" in df else df.iloc[0:0]
        out[t] = None if m.empty else m.iloc[0]["available_on"]
    return out


# ---------------------------------------------------------------------------------------------------------
# event scenario mixture and impact
# ---------------------------------------------------------------------------------------------------------
@dataclass
class EventImpact:
    weights: np.ndarray
    scenario_names: list[str]
    shape_unknown: bool
    R_p: float
    r_basis: str
    extra: dict[int, dict[str, float]]           # horizon -> p10/p50/p90/mean (bottles above baseline, +ve for UP)
    revenue_at_risk: dict[int, dict[str, float]]
    daily_paths: np.ndarray                      # n x H event-adjusted demand paths (baseline x R x noise)
    R_paths: np.ndarray                          # n x H scenario ratio paths (no noise)
    base_daily: np.ndarray                       # 28 baseline units/day
    direction: str
    post_peak_days: int


def event_status(det: Detection, params: Params) -> dict:
    """What the event machinery says about the latest day."""
    df = det.df
    if df.empty:
        return {}
    last = df.iloc[-1]
    open_ev = last["state"] in ("ONSET", "PEAK", "DECAY", "SUSTAINED")
    return {"open": open_ev, "state": last["state"], "direction": last["direction"]}


def event_impact(det: Detection, params: Params, baseline_future: np.ndarray, rng: np.random.Generator,
                 n: int = 2000, price: float | None = None, margin: float | None = None, H: int = 28,
                 u: np.ndarray | None = None) -> EventImpact | None:
    """Scenario-mixture impact over the next 7/14/28 days for an open event. None if no event is open."""
    df = det.df
    if df.empty:
        return None
    last = df.iloc[-1]
    if last["state"] not in ("ONSET", "PEAK", "DECAY", "SUSTAINED"):
        return None
    up = last["direction"] == "UP"
    uncens = df[(~df["censored"]) & df["idx"].ge(last["onset_idx"] if pd.notna(last["onset_idx"]) else 0)]
    if uncens.empty:
        return None
    peak_idx = last["peak_idx"] if pd.notna(last["peak_idx"]) else None
    onset = peak_idx is None
    if onset:
        R_p = float(uncens["R"].tail(2).mean())
        r_basis = "latest 2 uncensored days"
        t0 = 0
    else:
        R_p = float(last["peak_R"])
        r_basis = "3-day mean at peak"
        t0 = int(last["idx"] - peak_idx)
    g = float(last["g"]) if np.isfinite(last["g"]) else 0.0
    # Hidden peak: an UP event that has not peaked while shelves are empty. Sales stop tracking demand on stocked-out days,
    # so the last uncensored ratio is a floor, not the peak. Use the growth seen on the last uncensored days (past data
    # only) and do not let the transient scenarios decay the forecast on evidence that stockouts hide.
    hidden = bool(up and last["state"] in ("ONSET", "PEAK") and len(df) >= 5 and float((df["cens"].tail(5) >= 0.5).mean()) >= 0.6)
    if hidden:
        pts = uncens.tail(5)
        if len(pts) >= 3:
            g = max(float(np.polyfit(pts["idx"].to_numpy(dtype=float), np.log(np.maximum(pts["R"].to_numpy(), 1e-6)), 1)[0]), 0.02)
        else:
            g = 0.02
    R_max = r_max_for(R_p, g, params, onset, up, hidden)
    sigma_e = max(float(det.sigma_e), 0.03)
    K = 5
    prior = prior_weights(params)
    # weights move only after event_min_post_days post-peak days
    post = df[(~df["censored"]) & (df["idx"] > (peak_idx if peak_idx is not None else 10 ** 9))] if peak_idx is not None else df.iloc[0:0]
    if params["scen_exclude_oos_days"] and not post.empty:
        post = post[post["cens"] < 0.5]
    shape_unknown = True
    w = floor_weights(prior, params["scen_floor"])
    if hidden:
        w = floor_weights(np.array([0.0, 0.0, 0.0, 0.4, 0.6]), params["scen_floor"])
    if peak_idx is not None and len(post) >= params["event_min_post_days"]:
        j = (post["idx"] - peak_idx).to_numpy()
        curves = scenario_curves(R_p, R_max, j)
        w = scenario_weights(np.log(np.maximum(post["R"].to_numpy(), 1e-6)), curves, max(sigma_e, 0.05), prior,
                             params["scen_temper"], params["scen_floor"])
        shape_unknown = False
    # simulate H days ahead (at least 28)
    H = max(int(H), 28)
    jj = t0 + np.arange(1, H + 1)
    curves_f = scenario_curves(R_p, R_max, jj, f3_half=params["scen_f3_half_life_hidden"] if hidden else 7.0)
    uu = rng.random(n) if u is None else np.asarray(u)[:n]       # shared uniforms keep one viral event coherent across regions
    ks = np.searchsorted(np.cumsum(w / w.sum()), uu).clip(0, K - 1)
    noise = np.exp(sigma_e * rng.standard_normal((n, H)) - sigma_e ** 2 / 2.0)
    Rmat = np.stack(curves_f)[ks]                                # n x H
    base = np.asarray(baseline_future[:H], dtype=float)
    paths = base[None, :] * Rmat * noise
    sign = 1.0 if up else -1.0
    extra = (Rmat * noise - 1.0) * base[None, :]
    price = price or params["price"]
    margin = margin or params["margin"]
    ex, rv = {}, {}
    for hz in (7, 14, 28):
        tot = extra[:, :hz].sum(axis=1)
        ex[hz] = summarise(tot)
        rv[hz] = {k2: v * price for k2, v in ex[hz].items()}
    return EventImpact(w, SCEN_NAMES, shape_unknown, R_p, r_basis, ex, rv, paths, Rmat, base, "UP" if up else "DOWN",
                       0 if peak_idx is None else t0)
