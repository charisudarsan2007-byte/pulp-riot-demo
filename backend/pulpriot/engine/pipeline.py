"""EngineRun: everything the demand engine knows at one as-of moment.

Built lazily from a `Snapshot` (rows that had arrived by the as-of timestamp, never later). Heavy parts are cached on
the instance; the service layer caches instances per as-of day."""
from __future__ import annotations

from functools import cached_property

import numpy as np
import pandas as pd

from ..data.loader import Dataset, FLAVORS, asof_ts
from ..settings import MODEL_VERSION, Params
from . import events as EV
from .launch import LaunchModel, build_launch_model
from .monthly import (MonthlyFrame, NodeForecaster, aggregate_frame, all_channel_depot_frame, daily_seasonal,
                      monthly_frame, seasonal_profile)
from .depletion import DepletionResult, depot_weekly, run_depletion
from .qc_daily import QCSeries, build_qc_series, pooled_weekday_multipliers


class EngineRun:
    def __init__(self, ds: Dataset, asof_day, params: Params | None = None, similarity_override: dict | None = None,
                 launch_scale: float = 1.0):
        self.ds = ds
        self.params = params or ds.params
        self.day = pd.Timestamp(asof_day).normalize()
        self.ts = asof_ts(self.day)
        self.snap = ds.asof(self.ts)
        self.similarity_override = similarity_override
        self.launch_scale = launch_scale
        self._det: dict[tuple[str, str], EV.Detection] = {}

    # ---- inputs ---------------------------------------------------------------------------------------
    @cached_property
    def mf(self) -> MonthlyFrame:
        return monthly_frame(self.snap, self.params)

    @cached_property
    def series(self) -> dict[tuple[str, str], QCSeries]:
        return build_qc_series(self.snap, self.ds.depot_region())

    @cached_property
    def weekday_mult(self) -> np.ndarray:
        return pooled_weekday_multipliers(self.series, self.params, exclude={"DP"})

    def launch_flavors(self) -> list[str]:
        out = []
        for fl in FLAVORS:
            row = self.ds.dims["flavor"].set_index("flavor_id").loc[fl]
            ld = pd.to_datetime(row["launch_date"], errors="coerce")
            if pd.notna(ld) and (self.day - ld).days < 183:
                out.append(fl)
        return out

    @cached_property
    def launch_models(self) -> dict[str, LaunchModel]:
        out = {}
        for fl in self.launch_flavors():
            m = build_launch_model(self.ds, self.snap, fl, self.params, self.similarity_override, self.launch_scale)
            if m is not None:
                out[fl] = m
        return out

    # ---- seasonality ----------------------------------------------------------------------------------
    def gamma(self, flavor: str, channel: str = "QC") -> tuple[np.ndarray, str]:
        if flavor in self.launch_models:
            # launch series borrow their analogs' seasonality (spec M3)
            gs = [seasonal_profile(self.mf, a["id"], channel, self.params)[0] for a in self.launch_models[flavor].analogs]
            g = np.mean(gs, axis=0)
            return g - g.mean(), "ANALOG"
        return seasonal_profile(self.mf, flavor, channel, self.params)

    # ---- detection ------------------------------------------------------------------------------------
    def detection(self, flavor: str, region: str = "ALL") -> EV.Detection | None:
        key = (flavor, region)
        if key in self._det:
            return self._det[key]
        s = self.series.get(key)
        if s is None:
            return None
        g, _ = self.gamma(flavor, "QC")
        sd = daily_seasonal(g, s.dates)
        launch = self._launch_baseline(flavor, region, s)
        det = EV.run_detector(s, sd, self.params, launch)
        self._det[key] = det
        return det

    def _launch_baseline(self, flavor: str, region: str, s: QCSeries) -> EV.LaunchBaseline | None:
        lm = self.launch_models.get(flavor)
        if lm is None:
            return None
        depots = [d for d, r in self.ds.depot_region().items() if region == "ALL" or r == region]
        prior = lm.prior_daily(depots, "QC", s.dates)
        wm = self.weekday_mult[s.dates.dayofweek.to_numpy()]
        start = int(np.searchsorted(s.dates.values, np.datetime64(lm.launch_date)))
        if start >= len(s.dates):
            return None
        unc = s.cens < 0.5

        def shift_fn(i: int) -> float:
            upto = (s.dates[i] - pd.Timedelta(days=1)) if i < len(s.dates) else s.dates[-1]
            obs, idx = lm.weekly_totals(s.dates, s.q, unc, upto)
            if len(obs) == 0:
                return 0.0
            pri = np.array([prior[(s.dates >= lm.launch_date + pd.Timedelta(days=7 * w)) &
                                  (s.dates <= lm.launch_date + pd.Timedelta(days=7 * w + 6))].sum() for w in idx])
            return lm.shift(obs, pri)[0]

        return EV.LaunchBaseline(prior, wm, shift_fn, start, int(self.params["launch_age_days"]))

    # ---- convenience ----------------------------------------------------------------------------------
    @property
    def meta(self) -> dict:
        return {"asof": str(self.day.date()), "model_version": MODEL_VERSION, "config_hash": self.params.config_hash,
                "source_hash": self.ds.source_hash, "provenance": "SYNTHETIC", "arrival_cutoff": str(self.ts)}


    # ---- monthly nodes ----------------------------------------------------------------------------------
    @cached_property
    def nf_depot(self) -> NodeForecaster:
        return NodeForecaster(all_channel_depot_frame(self.mf), self.params)

    @cached_property
    def nf_region(self) -> NodeForecaster:
        return NodeForecaster(aggregate_frame(self.mf, self.ds.depot_region()), self.params)

    @cached_property
    def nf_nat(self) -> NodeForecaster:
        return NodeForecaster(aggregate_frame(self.mf, None), self.params)

    def _forecaster(self, scope: str) -> NodeForecaster:
        if scope == "ALL":
            return self.nf_nat
        return self.nf_region if scope in self.ds.regions else self.nf_depot

    def scope_depots(self, scope: str) -> list[str]:
        if scope == "ALL":
            return self.ds.depots
        if scope in self.ds.regions:
            return [d for d, r in self.ds.depot_region().items() if r == scope]
        return [scope]

    # ---- launch ------------------------------------------------------------------------------------------
    def freeze_date(self, flavor: str) -> pd.Timestamp | None:
        """First day the national QC detector left NORMAL for this flavor: weeks from then on are not baseline."""
        det = self.detection(flavor, "ALL")
        if det is None or det.df.empty:
            return None
        m = det.df[det.df["state"].isin(["WATCH", "ONSET", "PEAK", "DECAY", "SUSTAINED"])]
        return None if m.empty else pd.Timestamp(m.iloc[0]["date"])

    def launch_shift(self, flavor: str, channel: str) -> tuple[float, float]:
        """(shift, sd_post) for the whole-network launch curve of one channel, from complete uncensored weeks before any event."""
        key = ("shift", flavor, channel)
        if key in self._det:
            return self._det[key]                                  # type: ignore[return-value]
        lm = self.launch_models[flavor]
        fz = self.freeze_date(flavor)
        last_data = self.snap.date - pd.Timedelta(days=1)
        upto = min(last_data, fz - pd.Timedelta(days=1)) if fz is not None else last_data
        depots = self.ds.depots
        prior_sd = 1.0 / np.sqrt(1.0 / lm.sigma_p ** 2)
        if channel == "QC":
            s = self.series.get((flavor, "ALL"))
            if s is None:
                out = (0.0, prior_sd)
            else:
                prior = lm.prior_daily(depots, "QC", s.dates)
                obs, idx = lm.weekly_totals(s.dates, s.q, s.cens < 0.5, upto)
                pri = np.array([prior[(s.dates >= lm.launch_date + pd.Timedelta(days=7 * w)) &
                                      (s.dates <= lm.launch_date + pd.Timedelta(days=7 * w + 6))].sum() for w in idx])
                out = lm.shift(obs, pri) if len(obs) else (0.0, prior_sd)
        else:
            dsp = self.snap.dispatch_weekly
            dsp = dsp[(dsp.flavor_id == flavor) & (dsp.channel_id == channel)]
            wk = dsp.groupby("week_start")["bottles_dispatched"].sum()
            wkd = self.weekly
            cens_w = {}
            if len(wkd):
                w2 = wkd[wkd.flavor_id == flavor]
                tot = w2.groupby("week_start")["dispatch"].sum()
                cen = w2[w2.censored].groupby("week_start")["dispatch"].sum()
                cens_w = (cen.reindex(tot.index).fillna(0.0) / tot.clip(lower=1)).to_dict()
            obs, pri = [], []
            w = 0
            while True:
                a = lm.launch_date + pd.Timedelta(days=7 * w)
                if a + pd.Timedelta(days=6) > upto:
                    break
                if a in wk.index and cens_w.get(a, 0.0) < 0.3:
                    days = pd.date_range(a, periods=7)
                    obs.append(float(wk[a]))
                    pri.append(float(lm.prior_daily(depots, channel, days).sum()))
                w += 1
            out = lm.shift(np.array(obs), np.array(pri)) if obs else (0.0, prior_sd)
        self._det[key] = out                                       # type: ignore[assignment]
        return out

    def monthly_node(self, flavor: str, scope: str, channel: str, month) -> dict | None:
        """Monthly p50 (units) for a base node. Launch flavors come from M6, others from the M2 primary path."""
        month = pd.Timestamp(month)
        month = pd.Timestamp(year=month.year, month=month.month, day=1)
        memo = self.__dict__.setdefault("_mn_memo", {})
        mk = (flavor, scope, channel, month)
        if mk not in memo:
            memo[mk] = self._monthly_node(flavor, scope, channel, month)
        return memo[mk]

    def _monthly_node(self, flavor: str, scope: str, channel: str, month) -> dict | None:
        if flavor in self.launch_models:
            lm = self.launch_models[flavor]
            days = pd.date_range(month, periods=month.days_in_month)
            chans = ["GT", "MT", "QC"] if channel == "ALL" else [channel]
            tot = 0.0
            for c in chans:
                sh, _ = self.launch_shift(flavor, c)
                tot += float(lm.prior_daily(self.scope_depots(scope), c, days).sum() * np.exp(sh))
            return {"p50": tot, "method": "COLD_START_ANALOG", "rho": 1.0, "rho_source": "NA", "extrapolating": True}
        r = self._forecaster(scope).month(flavor, scope, channel, month)
        if r is not None:
            r = dict(r, extrapolating=False)
        return r

    # ---- QC baseline -------------------------------------------------------------------------------------
    def qc_baseline_future(self, flavor: str, region: str, dates: pd.DatetimeIndex) -> np.ndarray:
        """Baseline daily QC units (no event) for future dates, from the frozen/current level."""
        det = self.detection(flavor, region)
        kappa = self.params["kappa"]
        if det is None or det.df.empty:
            return np.zeros(len(dates))
        lm = self.launch_models.get(flavor)
        if lm is not None and det.launch_mode_end:
            wm = self.weekday_mult[dates.dayofweek.to_numpy()]
            return lm.prior_daily(self.scope_depots(region), "QC", dates) * np.exp(det.shift_last) * wm
        last = det.df.iloc[-1]
        base = det.a_level if last["state"] == "NORMAL" else det.a_frozen
        g, _ = self.gamma(flavor, "QC")
        s = daily_seasonal(g, dates)
        w = det.weekday[dates.dayofweek.to_numpy()]
        return np.maximum(np.exp(base + s + w) - kappa, 0.0)

    # ---- weekly depot layer -----------------------------------------------------------------------------
    @cached_property
    def weekly(self) -> pd.DataFrame:
        return depot_weekly(self.snap, self.params)

    def expected_weekly(self, rows: pd.DataFrame, flavor: str, depot: str) -> np.ndarray:
        """Phased monthly expectation for each week in `rows` (all channels)."""
        out = np.full(len(rows), np.nan)
        lm = self.launch_models.get(flavor)
        for i, ws in enumerate(rows["week_start"]):
            days = pd.date_range(ws, periods=7)
            if lm is not None:
                out[i] = sum(float(lm.prior_daily([depot], c, days).sum() * np.exp(self.launch_shift(flavor, c)[0]))
                             for c in ("GT", "MT", "QC"))
                continue
            tot = 0.0
            ok = True
            for (yr, mo), grp in pd.Series(days).groupby([days.year, days.month]):
                m0 = pd.Timestamp(year=yr, month=mo, day=1)
                node = self.monthly_node(flavor, depot, "ALL", m0)
                if node is None:
                    ok = False
                    break
                tot += node["p50"] / m0.days_in_month * len(grp)
            if ok:
                out[i] = tot
        return out

    def depletion(self, flavor: str, depot: str) -> DepletionResult | None:
        key = ("dep", flavor, depot)
        if key in self._det:
            return self._det[key]                                  # type: ignore[return-value]
        w = self.weekly
        if w.empty:
            return None
        rows = w[(w.flavor_id == flavor) & (w.depot_id == depot)].sort_values("week_start").reset_index(drop=True)
        lm = self.launch_models.get(flavor)
        if lm is not None:
            rows = rows[rows["week_end"] > lm.launch_date].reset_index(drop=True)
        if rows.empty:
            return None
        exp = self.expected_weekly(rows, flavor, depot)
        res = run_depletion(rows, exp, self.params)
        self._det[key] = res                                       # type: ignore[assignment]
        return res
