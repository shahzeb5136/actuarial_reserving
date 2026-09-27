"""Derived views behind the additional charts (pure functions, no UI).

Everything here reshapes results the app already computes - nothing changes a
reserve - so the charts stay consistent with the tables (and the R parity tests).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .triangles import period_index, period_start

PRIOR_COLUMNS = {"benktander": "Benktander", "bf": "BF", "capecod": "CapeCod"}
PRIOR_LABELS = {"benktander": "Benktander", "bf": "Bornhuetter-Ferguson", "capecod": "Cape Cod"}


# ---------------------------------------------------------------------------
# Data checks
# ---------------------------------------------------------------------------

def claims_by_origin_month(df: pd.DataFrame, max_series: int = 7) -> pd.DataFrame:
    """Claim amount by origin month and sub-category (long: Month, SubCat, Amount).

    Beyond max_series sub-categories the smallest fold into "Other"."""
    sub = df["SubCat"].map(lambda s: "(blank)" if s is None else str(s))
    totals = df.groupby(sub)["ClaimAmount"].sum().sort_values(ascending=False)
    if len(totals) > max_series:
        sub = sub.where(sub.isin(totals.index[: max_series - 1]), "Other")
    month = period_start(period_index(df["OriginDate"], "month"), "month")
    return (pd.DataFrame({"Month": month, "SubCat": sub.to_numpy(),
                          "Amount": df["ClaimAmount"].to_numpy(dtype=float)})
              .groupby(["Month", "SubCat"], as_index=False)["Amount"].sum())


def payment_delay_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Share of paid dollars by whole months from origin month to payment month."""
    delay = (period_index(df["PaymentDate"], "month")
             - period_index(df["OriginDate"], "month")).to_numpy()
    by = (pd.Series(df["ClaimAmount"].to_numpy(dtype=float)).groupby(delay).sum()
            .reindex(np.arange(0, int(delay.max()) + 1), fill_value=0.0))
    share = by / by.sum()
    return pd.DataFrame({"Delay": by.index.to_numpy(), "Amount": by.to_numpy(),
                         "Share": share.to_numpy(), "CumShare": share.cumsum().to_numpy()})


def first_reaching(values, labels, target: float):
    """Label of the first position where values >= target (None if never)."""
    hit = np.flatnonzero(np.asarray(values, dtype=float) >= target - 1e-12)
    return labels[hit[0]] if hit.size else None


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------

def loss_ratios(res) -> pd.DataFrame | None:
    """Paid-to-date and blended-ultimate loss ratios per origin (needs matched premium)."""
    if res.exp_al is None or res.blend is None:
        return None
    d = res.blend.detail
    prem = np.asarray(res.exp_al.earned_premium, dtype=float)
    ok = np.isfinite(prem) & (prem > 0)
    if not ok.any():
        return None
    return pd.DataFrame({"Origin": d["Origin"][ok].to_numpy(), "Premium": prem[ok],
                         "Paid": d["Latest"][ok].to_numpy(), "Ultimate": d["Blended"][ok].to_numpy(),
                         "PaidLR": d["Latest"][ok].to_numpy() / prem[ok],
                         "UltimateLR": d["Blended"][ok].to_numpy() / prem[ok]})


# ---------------------------------------------------------------------------
# Triangles & development
# ---------------------------------------------------------------------------

def payment_pattern(cum: np.ndarray, ultimate: np.ndarray) -> np.ndarray:
    """Cumulative paid as a share of each origin's chain-ladder ultimate."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(cum, dtype=float) / np.asarray(ultimate, dtype=float)[:, None]


def observed_incremental(incremental: np.ndarray, cum: np.ndarray) -> np.ndarray:
    """Incremental paid on the observed triangle (0 where nothing was paid), NaN in the future."""
    inc = np.nan_to_num(np.asarray(incremental, dtype=float), nan=0.0)
    return np.where(np.isnan(np.asarray(cum, dtype=float)), np.nan, inc)


def development_pattern(f: np.ndarray) -> pd.DataFrame:
    """Expected share of ultimate paid by development period, from the selected
    link ratios (last element of f = tail factor): cumulative 1/CDF and the
    increment paid in each period."""
    f = np.asarray(f, dtype=float)
    link, tail = f[:-1], f[-1]
    cdf = np.array([np.prod(link[k:]) * tail for k in range(len(f))])
    labels = [str(k) for k in range(1, len(f) + 1)]
    pct = 1.0 / cdf
    if tail > 1:
        labels.append("Ult")
        pct = np.append(pct, 1.0)
    return pd.DataFrame({"Period": labels, "Cumulative": pct,
                         "Incremental": np.diff(pct, prepend=0.0)})


def link_ratio_deviation(cum: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Individual age-to-age factors per origin and their deviation from the selected factor."""
    cum = np.asarray(cum, dtype=float)
    x, y = cum[:, :-1], cum[:, 1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = np.where(np.isfinite(x) & np.isfinite(y) & (x > 0), y / x, np.nan)
        deviation = ratios / np.asarray(f, dtype=float)[: cum.shape[1] - 1][None, :] - 1
    return ratios, deviation


# ---------------------------------------------------------------------------
# Uncertainty
# ---------------------------------------------------------------------------

def cdf_points(sims: np.ndarray, n_points: int = 401) -> pd.DataFrame:
    """Empirical CDF of simulated totals, thinned to n_points for plotting."""
    p = np.linspace(0, 1, n_points)
    return pd.DataFrame({"IBNR": np.quantile(np.asarray(sims, dtype=float), p), "Prob": p})


def percentile_of(value: float, sims: np.ndarray) -> float:
    return float(np.mean(np.asarray(sims, dtype=float) <= value))


def origin_bands(boot) -> pd.DataFrame:
    """5/25/50/75/95th percentiles of simulated IBNR per origin."""
    q = np.quantile(boot.ibnr_by_origin, [0.05, 0.25, 0.5, 0.75, 0.95], axis=1)
    out = pd.DataFrame({"Origin": boot.origins, "Latest": boot.latest, "P5": q[0], "P25": q[1],
                        "P50": q[2], "P75": q[3], "P95": q[4]})
    return out[out["Latest"] != 0].reset_index(drop=True)


def risk_components(mack) -> pd.DataFrame:
    """Mack process and parameter standard errors of each origin's ultimate."""
    out = pd.DataFrame({"Origin": mack.origins, "Latest": mack.latest,
                        "Process": mack.process_risk[:, -1], "Parameter": mack.parameter_risk[:, -1],
                        "Total": mack.mack_se[:, -1]})
    return out[out["Latest"] != 0].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Blend sensitivity
# ---------------------------------------------------------------------------

def kappa_sensitivity(blend, kappas=None) -> pd.DataFrame:
    """Total blended IBNR for a grid of kappas under each stabilising prior
    (every other setting as in the run)."""
    kappas = np.round(np.arange(0.5, 3.0001, 0.05), 2) if kappas is None else np.asarray(kappas)
    d = blend.detail
    pct = np.clip(d["Pct.Dev"].to_numpy(dtype=float), 0, 1)
    cl, latest = d["CL"].to_numpy(dtype=float), d["Latest"].to_numpy(dtype=float)
    rows = []
    for prior, col in PRIOR_COLUMNS.items():
        prior_ult = d[col].to_numpy(dtype=float)
        for k in kappas:
            z = pct ** k
            rows.append((float(k), prior, float(np.sum(z * cl + (1 - z) * prior_ult) - latest.sum())))
    return pd.DataFrame(rows, columns=["Kappa", "Prior", "TotalIBNR"])


# ---------------------------------------------------------------------------
# Booked position vs alternatives
# ---------------------------------------------------------------------------

def method_totals(res) -> pd.DataFrame:
    """Total IBNR of every estimator (Label, Value, Kind; Kind 'recommended' marks the pick)."""
    if res.blend is not None:
        t = res.blend.totals
        kinds = ["recommended" if m.startswith("Blended") else "method" for m in t["Method"]]
        return pd.DataFrame({"Label": t["Method"], "Value": t["Total.IBNR"], "Kind": kinds})
    m = res.method_cmp
    kinds = ["recommended" if lbl.startswith("Mack") else "method" for lbl in m["Method"]]
    return pd.DataFrame({"Label": m["Method"], "Value": m["Total.IBNR"], "Kind": kinds})


def reserve_ladder(res, ar: pd.DataFrame | None) -> pd.DataFrame:
    """Headline reserve figures and the bootstrap stress levels, ascending."""
    cl = float(res.reserve_df.loc[res.reserve_df["Origin"] == "Total", "IBNR"].iloc[0])
    rows = []
    if res.blend is not None:
        rec = float(res.blend.totals.loc[res.blend.totals["Method"] == "Blended (recommended)",
                                         "Total.IBNR"].iloc[0])
        rows.append(("Recommended (blended)", rec, "recommended"))
        rows.append(("Chain Ladder", cl, "model"))
    else:
        rec = cl
        rows.append(("Recommended (Chain Ladder)", rec, "recommended"))
    if ar is not None:
        booked = float(ar["Selected.IBNR"].sum())
        if np.isfinite(booked) and abs(booked - rec) > 0.5:
            rows.append(("Booked (after adjustments)", booked, "booked"))
    if res.boot is not None:
        q = np.quantile(res.boot.sim_totals, [0.5, 0.75, 0.95, 0.995])
        rows += [("Bootstrap median", q[0], "stress"), ("1-in-4 adverse (75th pct)", q[1], "stress"),
                 ("1-in-20 adverse (95th pct)", q[2], "stress"),
                 ("1-in-200 adverse (99.5th pct)", q[3], "stress")]
    return (pd.DataFrame(rows, columns=["Label", "Value", "Kind"])
              .sort_values("Value", kind="stable").reset_index(drop=True))


# ---------------------------------------------------------------------------
# Recast / seasonality
# ---------------------------------------------------------------------------

def recast_error_by_origin(recast) -> pd.DataFrame:
    """Held-back actual vs expected per origin, with the % error."""
    d = (recast.detail.groupby("Origin", sort=True)[["Actual", "Expected"]].sum().reset_index())
    with np.errstate(divide="ignore", invalid="ignore"):
        d["Error"] = np.where(d["Expected"] != 0, d["Actual"] / d["Expected"] - 1, np.nan)
    return d


def paid_by_payment_month(df: pd.DataFrame) -> pd.DataFrame:
    """Paid per payment month and its trailing 12-month average."""
    idx = period_index(df["PaymentDate"], "month")
    by = (pd.Series(df["ClaimAmount"].to_numpy(dtype=float)).groupby(idx.to_numpy()).sum()
            .reindex(np.arange(idx.min(), idx.max() + 1), fill_value=0.0))
    return pd.DataFrame({"Month": period_start(by.index, "month"), "Paid": by.to_numpy(),
                         "Trend": by.rolling(12, min_periods=12).mean().to_numpy()})
