"""Seasonality: monthly indices on an origin-month (incidence) basis and a
payment-month (settlement) basis, 100 = the average month.  Ports
seasonality_analysis from app.R.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MONTH_ABB = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class SeasonalityResult:
    index: pd.DataFrame   # Mnth, MeanAmount, Index, Basis
    heat: pd.DataFrame    # Yr, Mnth, Amount (payment year x payment month)

    def payment_index_vector(self) -> np.ndarray:
        """12-vector (Jan..Dec) of the payment-month index; missing months = 100."""
        pay = self.index[self.index["Basis"] == "Payment month"]
        v = np.full(12, 100.0)
        for mn, val in zip(pay["Mnth"], pay["Index"]):
            v[MONTH_ABB.index(mn)] = val
        return v


def _month_index(dates: pd.Series, amounts: pd.Series, basis: str) -> pd.DataFrame:
    d = pd.to_datetime(dates)
    by_ym = (pd.DataFrame({"Yr": d.dt.year, "M": d.dt.month, "Amount": amounts})
               .groupby(["Yr", "M"], as_index=False)["Amount"].sum())
    by_m = by_ym.groupby("M", as_index=False)["Amount"].mean().sort_values("M")
    mean_amount = by_m["Amount"].to_numpy(dtype=float)
    return pd.DataFrame({
        "Mnth": [MONTH_ABB[m - 1] for m in by_m["M"]],
        "MeanAmount": mean_amount,
        "Index": 100 * mean_amount / mean_amount.mean(),
        "Basis": basis,
    })


def seasonality_analysis(df: pd.DataFrame) -> SeasonalityResult:
    amounts = df["ClaimAmount"].astype(float)
    index = pd.concat([_month_index(df["OriginDate"], amounts, "Origin month"),
                       _month_index(df["PaymentDate"], amounts, "Payment month")],
                      ignore_index=True)
    pay = pd.to_datetime(df["PaymentDate"])
    heat = (pd.DataFrame({"Yr": pay.dt.year, "M": pay.dt.month, "Amount": amounts})
              .groupby(["Yr", "M"], as_index=False)["Amount"].sum()
              .sort_values(["Yr", "M"]))
    heat = pd.DataFrame({"Yr": heat["Yr"].astype(str).to_numpy(),
                         "Mnth": [MONTH_ABB[m - 1] for m in heat["M"]],
                         "Amount": heat["Amount"].to_numpy(dtype=float)})
    return SeasonalityResult(index, heat)
