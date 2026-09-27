"""Realistic dummy claims generator for the Reserving Tool (port of
archive/r_shiny_app/generate_dummy_data.R).

Unlike a naive generator (independent random origin & payment dates, which
produces a meaningless triangle), this builds claims with:
  * a genuine payment development lag (most paid early, a tail that runs off)
  * origin-month SEASONALITY (winter peak - typical for medical LOB)
  * three subcategories with different volumes & speed of settlement

Writes aggregated_claims.xlsx (OriginDate, PaymentDate, SubCat, ClaimAmount)
and exposure_premium.xlsx (Period, EarnedPremium, Exposure).

Numbers differ from the R version (different random number generator), but
the structure is the same.  Usage:
    python generate_dummy_data.py [output_dir]      (default: sample_data/)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ORIGIN_START, ORIGIN_END = pd.Timestamp("2021-01-01"), pd.Timestamp("2023-12-31")
N_CLAIMS = 80_000

# Subcategory profiles: volume weight, mean severity, settlement speed (mean lag, months)
SUBCATS = pd.DataFrame({"SubCat": ["A", "B", "C"], "weight": [0.50, 0.30, 0.20],
                        "mean_sev": [15000, 28000, 9000], "mean_lag_mo": [3, 6, 2]})

# Monthly seasonality multiplier on claim INCIDENCE (origin month): higher Dec-Feb.
SEASONAL_MULT = np.array([1.30, 1.25, 1.05, 0.95, 0.90, 0.85,
                          0.85, 0.90, 0.95, 1.05, 1.15, 1.30])


def add_months(dates: pd.DatetimeIndex, months: np.ndarray) -> pd.DatetimeIndex:
    """lubridate's %m+%: add whole months, clipping to the end of shorter months."""
    total = dates.year * 12 + (dates.month - 1) + months
    year, month = total // 12, total % 12 + 1
    first = pd.to_datetime(pd.DataFrame({"year": year, "month": month, "day": 1}))
    day = np.minimum(dates.day, first.dt.days_in_month)
    return pd.DatetimeIndex(pd.to_datetime(pd.DataFrame({"year": year, "month": month, "day": day})))


def generate(seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)

    # Origin dates weighted by seasonality
    all_days = pd.date_range(ORIGIN_START, ORIGIN_END, freq="D")
    w = SEASONAL_MULT[all_days.month - 1]
    origin = pd.DatetimeIndex(rng.choice(all_days.to_numpy(), N_CLAIMS, p=w / w.sum()))

    # Subcategory and its profile
    sc = rng.choice(SUBCATS["SubCat"], N_CLAIMS, p=SUBCATS["weight"])
    prof = SUBCATS.set_index("SubCat").loc[sc]

    # Development lag (months) ~ exponential by subcat, paid lag months after origin,
    # capped at the valuation date (mimics a real valuation cut-off)
    lag = np.round(rng.exponential(prof["mean_lag_mo"].to_numpy())).astype(int)
    payment = add_months(origin, lag)
    payment = payment.where(payment <= ORIGIN_END, ORIGIN_END)

    # Severity ~ Gamma by subcat
    amount = rng.gamma(shape=2, scale=prof["mean_sev"].to_numpy() / 2)

    raw = pd.DataFrame({"OriginDate": origin, "PaymentDate": payment, "SubCat": sc,
                        "ClaimAmount": np.round(amount, 2)})
    claims = (raw.groupby(["OriginDate", "PaymentDate", "SubCat"], as_index=False)["ClaimAmount"]
                 .sum())

    # Companion exposure / earned premium table (monthly origin periods): exposure
    # follows the incidence seasonality with a small upward trend; premium is
    # exposure at a notional rate, so the implied loss ratio lands sensibly.
    periods = pd.date_range(ORIGIN_START, ORIGIN_END, freq="MS")
    trend = 1 + 0.004 * np.arange(len(periods))
    exposure = np.round(1000 * SEASONAL_MULT[periods.month - 1] * trend)
    premium = np.round(exposure * 600 * (1 + rng.uniform(-0.03, 0.03, len(periods))))
    exposure_tbl = pd.DataFrame({"Period": periods, "EarnedPremium": premium.astype(int),
                                 "Exposure": exposure.astype(int)})
    return claims, exposure_tbl


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    claims, exposure = generate()
    claims.to_excel(out_dir / "aggregated_claims.xlsx", index=False)
    exposure.to_excel(out_dir / "exposure_premium.xlsx", index=False)
    print(f"Saved {len(claims):,} aggregated claim rows and {len(exposure)} exposure periods "
          f"to {out_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sample_data")
