"""Expected-loss methods and the credibility-weighted blend.

Ports exposure_apriori, run_bf, build_expected_prior, run_benktander,
run_capecod and run_blend from app.R.

Why a blend: pure Chain Ladder multiplies each origin's latest cumulative by
the product of the remaining development factors.  For the youngest periods
the latest cell is tiny and that product is huge, so noise is amplified into a
volatile ultimate.  The blend anchors immature periods to a stable prior and
lets their own data earn weight as they mature:

    Ultimate_blend = Z * CL + (1 - Z) * Prior,   Z = (% developed) ** kappa
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .mack import MackResult, fit_chain_ladder
from .triangles import latest_values, pct_developed

BLEND_PRIORS = ("benktander", "bf", "capecod")
METHOD_COLUMNS = ["CL", "Expected", "BF", "Benktander", "CapeCod", "Blended"]


def _has_valid(base) -> bool:
    if base is None:
        return False
    b = np.asarray(base, dtype=float)
    return bool(np.any(np.isfinite(b) & (b > 0)))


def _grossed_up(latest: np.ndarray, pct_dev: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(pct_dev > 0, latest / pct_dev, np.nan)


# ---------------------------------------------------------------------------
# Exposure-based a-priori
# ---------------------------------------------------------------------------

@dataclass
class ExposureApriori:
    apriori: np.ndarray
    elr: float
    pct_dev: np.ndarray
    latest: np.ndarray
    base: np.ndarray


def exposure_apriori(cum: np.ndarray, f: np.ndarray, base) -> ExposureApriori:
    """Per-origin a-priori ultimate = ELR * exposure, with the ELR estimated on
    a developed basis (Cape Cod / Stanard-Buhlmann):

        ELR = sum(latest) / sum(exposure * %developed)

    Origins without usable exposure fall back to the grossed-up latest.
    """
    latest = latest_values(cum)
    pct_dev = pct_developed(cum, f)
    base = np.asarray(base, dtype=float)
    valid = np.isfinite(base) & (base > 0)
    denom = np.nansum(base[valid] * pct_dev[valid])
    elr = float(np.nansum(latest[valid]) / denom) if np.isfinite(denom) and denom > 0 else np.nan

    apriori = np.full(latest.shape, np.nan)
    if np.isfinite(elr):
        apriori[valid] = elr * base[valid]
    fill = ~valid | ~np.isfinite(apriori)
    if fill.any():
        apriori[fill] = _grossed_up(latest, pct_dev)[fill]
    return ExposureApriori(apriori, elr, pct_dev, latest, base)


# ---------------------------------------------------------------------------
# Bornhuetter-Ferguson, Benktander, Cape Cod
# ---------------------------------------------------------------------------

def run_bf(cum: np.ndarray, origins: list[str], apriori_ultimate) -> pd.DataFrame:
    """Bornhuetter-Ferguson: latest + a-priori * (1 - %developed)."""
    f = fit_chain_ladder(cum).f
    latest = latest_values(cum)
    pct_dev = pct_developed(cum, f)
    apriori = np.asarray(apriori_ultimate, dtype=float)
    ult = latest + apriori * (1 - pct_dev)
    return pd.DataFrame({"Origin": origins, "Latest": latest, "Pct.Dev": pct_dev,
                         "Apriori": apriori, "Ultimate": ult, "IBNR": ult - latest})


def run_benktander(cum: np.ndarray, origins: list[str], f: np.ndarray,
                   apriori_ultimate, n_iter: int = 2) -> pd.DataFrame:
    """Iterated BF (n_iter = 1 is BF, 2 is classic Benktander, -> inf is CL)."""
    latest = latest_values(cum)
    pct_dev = pct_developed(cum, f)
    u = np.asarray(apriori_ultimate, dtype=float)
    for _ in range(n_iter):
        u = latest + u * (1 - pct_dev)
    return pd.DataFrame({"Origin": origins, "Latest": latest, "Pct.Dev": pct_dev,
                         "Ultimate": u, "IBNR": u - latest})


def run_capecod(cum: np.ndarray, origins: list[str], f: np.ndarray, exposure=None) -> pd.DataFrame:
    """Cape Cod.  With exposure: ELR * exposure as the a-priori; without it,
    the equal-exposure level sum(latest) / sum(%developed)."""
    latest = latest_values(cum)
    pct_dev = pct_developed(cum, f)
    if _has_valid(exposure):
        ea = exposure_apriori(cum, f, exposure)
        ult = latest + ea.apriori * (1 - pct_dev)
        return pd.DataFrame({"Origin": origins, "Latest": latest, "Pct.Dev": pct_dev,
                             "Exposure": ea.base, "ELR": ea.elr, "Apriori": ea.apriori,
                             "Ultimate": ult, "IBNR": ult - latest})
    level = np.sum(latest) / np.sum(pct_dev)
    ult = latest + level * (1 - pct_dev)
    return pd.DataFrame({"Origin": origins, "Latest": latest, "Pct.Dev": pct_dev,
                         "Exposure": np.nan, "ELR": np.nan, "Apriori": level,
                         "Ultimate": ult, "IBNR": ult - latest})


# ---------------------------------------------------------------------------
# Stable prior for the blend
# ---------------------------------------------------------------------------

def seasonal_multipliers(seas_index, origin_dates, period: str) -> np.ndarray:
    """Seasonal tilt per origin from a 12-month index (100 = average).
    Quarterly origins average their three months."""
    idx = np.asarray(seas_index, dtype=float) / 100.0
    out = []
    for d in pd.DatetimeIndex(origin_dates):
        if pd.isna(d):
            out.append(1.0)
            continue
        if period == "month":
            val = idx[d.month - 1]
        else:
            q = (d.month - 1) // 3
            vals = idx[q * 3: q * 3 + 3]
            val = np.nanmean(vals) if np.any(~np.isnan(vals)) else np.nan
        out.append(1.0 if np.isnan(val) else float(val))
    return np.asarray(out)


def build_expected_prior(cum: np.ndarray, origins: list[str], f: np.ndarray,
                         seas_index=None, period: str = "quarter",
                         origin_dates=None, exposure=None) -> pd.DataFrame:
    """A stable per-origin a-priori ultimate that doesn't lean on the thin
    latest diagonal.

    With earned premium: developed loss ratio * premium (no seasonal tilt -
    premium already carries the level).  Without: the average grossed-up
    ultimate of the mature (>= 70% developed) origins, optionally tilted by
    the seasonal index.
    """
    latest = latest_values(cum)
    pct_dev = pct_developed(cum, f)
    if _has_valid(exposure):
        ea = exposure_apriori(cum, f, exposure)
        return pd.DataFrame({"Origin": origins, "Latest": latest, "Pct.Dev": pct_dev,
                             "Seas.Mult": 1.0, "Exposure": ea.base, "ELR": ea.elr,
                             "Apriori": ea.apriori})

    grossed = _grossed_up(latest, pct_dev)
    mature = pct_dev >= 0.70
    if mature.sum() < 1:
        mature = pct_dev >= np.nanmedian(pct_dev)
    base_level = np.nanmean(grossed[mature]) if np.any(~np.isnan(grossed[mature])) else np.nan
    if not np.isfinite(base_level):
        base_level = np.nanmean(grossed)

    seas_mult = np.ones(len(latest))
    if seas_index is not None and origin_dates is not None:
        seas_mult = seasonal_multipliers(seas_index, origin_dates, period)
    return pd.DataFrame({"Origin": origins, "Latest": latest, "Pct.Dev": pct_dev,
                         "Seas.Mult": seas_mult, "Exposure": np.nan, "ELR": np.nan,
                         "Apriori": base_level * seas_mult})


# ---------------------------------------------------------------------------
# Blend
# ---------------------------------------------------------------------------

@dataclass
class BlendResult:
    detail: pd.DataFrame
    methods_long: pd.DataFrame
    totals: pd.DataFrame
    prior: pd.DataFrame
    kappa: float
    prior_name: str
    elr_premium: float
    elr_capecod: float
    has_exposure: bool


def run_blend(cum: np.ndarray, origins: list[str], mack: MackResult, seas_index=None,
              period: str = "quarter", origin_dates=None, kappa: float = 1.5,
              prior: str = "benktander", earned_premium=None, exposure=None) -> BlendResult:
    """Maturity-credibility blend of Chain Ladder with a stabilising prior.

    kappa: Z = %developed ** kappa (< 1 trusts data sooner, > 1 discounts
    green periods harder).  prior: 'benktander', 'bf' or 'capecod'.
    """
    if prior not in BLEND_PRIORS:
        raise ValueError(f"prior must be one of {BLEND_PRIORS}")
    f = mack.f
    cl_ult = latest_values(mack.full_triangle)
    latest = latest_values(cum)
    pct_dev = pct_developed(cum, f)

    cc_exposure = exposure if exposure is not None else earned_premium
    prior_tbl = build_expected_prior(cum, origins, f, seas_index, period, origin_dates,
                                     exposure=earned_premium)
    apriori = prior_tbl["Apriori"].to_numpy(dtype=float)

    bf = run_bf(cum, origins, apriori)
    benk = run_benktander(cum, origins, f, apriori, n_iter=2)
    cc = run_capecod(cum, origins, f, exposure=cc_exposure)
    prior_ult = {"bf": bf, "benktander": benk, "capecod": cc}[prior]["Ultimate"].to_numpy()

    z = np.clip(pct_dev, 0, 1) ** kappa
    blend_ult = z * cl_ult + (1 - z) * prior_ult

    n = len(latest)
    ep_vec = np.asarray(earned_premium, dtype=float) if earned_premium is not None else np.full(n, np.nan)
    exp_vec = np.asarray(cc_exposure, dtype=float) if cc_exposure is not None else np.full(n, np.nan)
    detail = pd.DataFrame({
        "Origin": origins, "Latest": latest, "Pct.Dev": pct_dev, "Credibility": z,
        "EarnedPrem": ep_vec, "Exposure": exp_vec,
        "CL": cl_ult, "Expected": apriori, "BF": bf["Ultimate"].to_numpy(),
        "Benktander": benk["Ultimate"].to_numpy(), "CapeCod": cc["Ultimate"].to_numpy(),
        "Blended": blend_ult, "IBNR.CL": cl_ult - latest, "IBNR.Blend": blend_ult - latest,
    })

    elr_premium = exposure_apriori(cum, f, earned_premium).elr if earned_premium is not None else np.nan
    elr_capecod = float(cc["ELR"].iloc[0]) if len(cc) else np.nan
    has_exposure = earned_premium is not None or exposure is not None

    methods_long = detail.melt(id_vars=["Origin", "Pct.Dev"], value_vars=METHOD_COLUMNS,
                               var_name="Method", value_name="Ultimate")
    sum_latest = latest.sum()
    totals = pd.DataFrame({
        "Method": ["Chain Ladder", "Expected/Seasonal", "Bornhuetter-Ferguson",
                   "Benktander", "Cape Cod", "Blended (recommended)"],
        "Total.Ultimate": [detail["CL"].sum(skipna=False), detail["Expected"].sum(skipna=False),
                           bf["Ultimate"].sum(skipna=False), benk["Ultimate"].sum(skipna=False),
                           cc["Ultimate"].sum(skipna=False), detail["Blended"].sum(skipna=False)],
        "Total.IBNR": [detail["CL"].sum(skipna=False) - sum_latest,
                       detail["Expected"].sum(skipna=False) - sum_latest,
                       bf["IBNR"].sum(skipna=False), benk["IBNR"].sum(skipna=False),
                       cc["IBNR"].sum(skipna=False),
                       detail["Blended"].sum(skipna=False) - sum_latest],
    })
    return BlendResult(detail, methods_long, totals, prior_tbl, kappa, prior,
                       elr_premium, elr_capecod, has_exposure)
