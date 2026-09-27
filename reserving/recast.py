"""Recast (back-test): hold back the latest calendar diagonals, re-fit the
chain ladder on what is left, project forward and score the projection against
the actuals that were held back (actual vs expected).  Ports
truncate_diagonals / recast_analysis from app.R.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .mack import fit_chain_ladder


class RecastError(ValueError):
    pass


@dataclass
class RecastResult:
    detail: pd.DataFrame      # Origin, DevLag, Actual, Expected, AvE_Diff, AvE_Pct
    summary: pd.DataFrame     # one row: Cells, Total.Actual, ... MAPE, RMSE
    truncated: np.ndarray
    projected: np.ndarray


def truncate_diagonals(cum: np.ndarray, n_holdback: int) -> np.ndarray:
    """Blank out the latest n_holdback calendar diagonals."""
    cum = np.asarray(cum, dtype=float)
    n_org, n_dev = cum.shape
    out = cum.copy()
    for i in range(n_org):
        keep_to = max(n_org - i - n_holdback, 0)
        if keep_to < n_dev:
            out[i, keep_to:] = np.nan
    return out


def recast_analysis(cum: np.ndarray, origins: list[str], n_holdback: int = 1) -> RecastResult:
    cum = np.asarray(cum, dtype=float)
    n_org, n_dev = cum.shape
    if n_org - n_holdback < 2:
        raise RecastError(f"Holdback of {n_holdback} leaves fewer than 2 origin periods. "
                          f"This triangle has {n_org} origins; try a smaller holdback.")
    truncated = truncate_diagonals(cum, n_holdback)

    # Holding back N diagonals empties the rightmost development columns - there
    # is nothing to project into them, so they are dropped before fitting.
    keep_cols = np.flatnonzero(np.sum(~np.isnan(truncated), axis=0) > 0)
    if keep_cols.size < 2:
        raise RecastError(
            f"Holdback of {n_holdback} removes almost all development history "
            f"(only {keep_cols.size} development period(s) remain). Use a smaller "
            f"holdback, or switch to monthly granularity for a larger triangle.")
    fit = fit_chain_ladder(truncated[:, keep_cols])
    projected = np.full(cum.shape, np.nan)
    projected[:, keep_cols] = fit.full_triangle

    rows = []
    for i in range(n_org):
        for j in range(n_dev):
            actual = cum[i, j]
            if np.isnan(truncated[i, j]) and not np.isnan(actual):
                proj = projected[i, j]
                if np.isnan(proj):
                    continue           # cell sits in a dropped (empty) column
                rows.append((origins[i], j + 1, actual, proj, actual - proj,
                             np.nan if proj == 0 else (actual - proj) / proj))
    if not rows:
        raise RecastError("No held-back cells available for comparison.")
    detail = pd.DataFrame(rows, columns=["Origin", "DevLag", "Actual", "Expected",
                                         "AvE_Diff", "AvE_Pct"])
    ape = detail["AvE_Pct"].abs()
    summary = pd.DataFrame({
        "Cells": [len(detail)],
        "Total.Actual": [detail["Actual"].sum()],
        "Total.Expected": [detail["Expected"].sum()],
        "Total.Diff": [detail["AvE_Diff"].sum()],
        "Total.Pct": [detail["AvE_Diff"].sum() / detail["Expected"].sum()],
        "MAPE": [ape.mean() if ape.notna().any() else np.nan],
        "RMSE": [np.sqrt(np.mean(detail["AvE_Diff"] ** 2))],
    })
    return RecastResult(detail, summary, truncated, projected)
