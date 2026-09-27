"""Run-off triangle construction (monthly or quarterly granularity).

Raw claims -> long (origin x development lag) frame -> incremental matrix ->
cumulative triangle.  Ports build_long_triangle / long_to_incr_matrix /
incr_to_cum from app.R, plus the small helpers every reserving method shares
(latest diagonal, % developed).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PERIODS = ("month", "quarter")
_MONTHS_PER_PERIOD = {"month": 1, "quarter": 3}


def _check_period(period: str) -> int:
    if period not in _MONTHS_PER_PERIOD:
        raise ValueError(f"period must be one of {PERIODS}, got {period!r}")
    return _MONTHS_PER_PERIOD[period]


def period_index(dates, period: str) -> pd.Series:
    """Integer index of the calendar month / quarter each date falls in."""
    step = _check_period(period)
    d = pd.to_datetime(pd.Series(dates))
    months = d.dt.year.astype("int64") * 12 + (d.dt.month.astype("int64") - 1)
    return months // step


def period_start(index, period: str) -> pd.DatetimeIndex:
    """Inverse of period_index: first day of each month / quarter."""
    step = _check_period(period)
    months = np.asarray(index, dtype="int64") * step
    parts = pd.DataFrame({"year": months // 12, "month": months % 12 + 1, "day": 1})
    return pd.DatetimeIndex(pd.to_datetime(parts))


def floor_period(dates, period: str) -> pd.DatetimeIndex:
    """lubridate::floor_date(x, "month" | "quarter")."""
    return period_start(period_index(dates, period), period)


def date_labels(dates) -> list[str]:
    """Origin labels exactly as R prints a Date: 'YYYY-MM-DD'."""
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates]


# ---------------------------------------------------------------------------
# Triangle construction
# ---------------------------------------------------------------------------

def build_long_triangle(df: pd.DataFrame, period: str = "quarter") -> pd.DataFrame:
    """Aggregate claims into incremental amounts per origin period x dev lag.

    DevLag = whole periods between the origin period and the payment period,
    plus one (so a claim paid in its own origin period sits at lag 1).
    """
    o_idx = period_index(df["OriginDate"], period).to_numpy()
    p_idx = period_index(df["PaymentDate"], period).to_numpy()
    long = pd.DataFrame({
        "OriginIndex": o_idx,
        "DevLag": p_idx - o_idx + 1,
        "Incremental": pd.to_numeric(df["ClaimAmount"]).to_numpy(dtype=float),
    })
    long = long[long["DevLag"] >= 1]
    out = (long.groupby(["OriginIndex", "DevLag"], as_index=False)["Incremental"]
               .sum()
               .sort_values(["OriginIndex", "DevLag"], kind="stable")
               .reset_index(drop=True))
    out.insert(0, "OriginPeriod", period_start(out["OriginIndex"], period))
    return out


def long_to_incr_matrix(long_df: pd.DataFrame, period: str = "quarter") -> pd.DataFrame:
    """Long frame -> wide incremental matrix (rows = origins, cols = Dev_1..Dev_n).

    The grid is COMPLETE: every origin period between the first and last one
    and every lag from 1 to the maximum observed lag gets a row / column, with
    NaN where no payment was recorded.  (The R app pivoted only the lags that
    happened to occur, so a lag with no payments anywhere - e.g. lag 28 for
    SubCat A monthly - silently shifted later payments into the wrong
    development column.)
    """
    first, last = int(long_df["OriginIndex"].min()), int(long_df["OriginIndex"].max())
    n_org = last - first + 1
    n_dev = int(long_df["DevLag"].max())
    mat = np.full((n_org, n_dev), np.nan)
    rows = long_df["OriginIndex"].to_numpy(dtype="int64") - first
    cols = long_df["DevLag"].to_numpy(dtype="int64") - 1
    mat[rows, cols] = long_df["Incremental"].to_numpy(dtype=float)
    origins = period_start(np.arange(first, last + 1), period)
    return pd.DataFrame(mat, index=pd.Index(date_labels(origins), name="Origin"),
                        columns=[f"Dev_{k}" for k in range(1, n_dev + 1)])


def incr_to_cum(incr: np.ndarray) -> np.ndarray:
    """Incremental matrix -> cumulative run-off triangle.

    Row i (0-based) is observed for its first (n_org - i) development periods
    (capped at n_dev); missing increments inside that region count as zero and
    everything beyond the leading diagonal is NaN (the unobserved future).
    """
    incr = np.asarray(incr, dtype=float)
    n_org, n_dev = incr.shape
    cum = np.full((n_org, n_dev), np.nan)
    for i in range(n_org):
        avail = min(n_org - i, n_dev)
        vals = np.nan_to_num(incr[i, :avail], nan=0.0)
        cum[i, :avail] = np.cumsum(vals)
    return cum


@dataclass(frozen=True)
class Triangles:
    incremental: pd.DataFrame   # index = origin labels, columns = Dev_k
    cumulative: pd.DataFrame
    origin_dates: pd.DatetimeIndex

    @property
    def origins(self) -> list[str]:
        return list(self.cumulative.index)

    @property
    def cum(self) -> np.ndarray:
        return self.cumulative.to_numpy(dtype=float)


def build_triangles(df: pd.DataFrame, period: str = "quarter") -> Triangles:
    long = build_long_triangle(df, period)
    incr = long_to_incr_matrix(long, period)
    cum = pd.DataFrame(incr_to_cum(incr.to_numpy()), index=incr.index, columns=incr.columns)
    return Triangles(incremental=incr, cumulative=cum,
                     origin_dates=pd.DatetimeIndex(pd.to_datetime(incr.index)))


def triangle_frame(tri: pd.DataFrame) -> pd.DataFrame:
    """Matrix with origin index -> frame with an 'Origin' column (as displayed / exported)."""
    out = tri.reset_index()
    out["Origin"] = out["Origin"].astype(str)
    return out


# ---------------------------------------------------------------------------
# Helpers shared by every method
# ---------------------------------------------------------------------------

def latest_positions(cum: np.ndarray) -> np.ndarray:
    """Number of observed development periods per origin (R: sum(!is.na(row)))."""
    return np.sum(~np.isnan(np.asarray(cum, dtype=float)), axis=1)


def latest_values(cum: np.ndarray) -> np.ndarray:
    """Latest observed cumulative per origin (last non-NaN cell of each row)."""
    cum = np.asarray(cum, dtype=float)
    out = np.full(cum.shape[0], np.nan)
    for i, row in enumerate(cum):
        obs = row[~np.isnan(row)]
        if obs.size:
            out[i] = obs[-1]
    return out


def pct_developed(cum: np.ndarray, f: np.ndarray) -> np.ndarray:
    """% developed per origin = 1 / product of the remaining link ratios.

    Mirrors app.R exactly: uses link ratios f[pos .. n_dev-1] (1-based), i.e. it
    ignores any tail factor stored in position n_dev.
    """
    cum = np.asarray(cum, dtype=float)
    f = np.asarray(f, dtype=float)
    n_dev = cum.shape[1]
    out = np.empty(cum.shape[0])
    for i, pos in enumerate(latest_positions(cum)):
        if pos >= n_dev:
            out[i] = 1.0
        else:
            start = max(int(pos), 1) - 1          # R silently drops index 0
            out[i] = 1.0 / np.prod(f[start:n_dev - 1])
    return out
