"""Mack chain ladder.

A line-by-line port of R ChainLadder (v0.2.21) MackChainLadder, Mack.S.E,
MackRecursive.S.E, TotalMack.S.E and tail_SE for the settings the reserving
app uses:  alpha = 1 (volume-weighted link ratios), unit weights,
est.sigma = "Mack", a numeric tail factor and mse.method = "Mack".

Where R would stop with an error on a degenerate triangle (a zero or negative
cumulative in the fitted region, too few link ratios to extrapolate sigma) we
fall back to something sensible instead; those branches never trigger on a
well-formed triangle, so results are identical to R wherever R succeeds.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .triangles import latest_values


class MackError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Chain ladder fit (ChainLadder::chainladder + predict)
# ---------------------------------------------------------------------------

@dataclass
class ChainLadderFit:
    """One weighted regression through the origin per development step:
    C[k+1] = f_k * C[k] with weights 1 / C[k]  ->  f_k = sum C[k+1] / sum C[k]."""
    f: np.ndarray            # link ratios, length n_dev - 1
    sigma: np.ndarray        # residual standard error per link (NaN if < 2 obs)
    f_se: np.ndarray         # standard error of each link ratio (NaN if < 2 obs)
    n_obs: np.ndarray        # observations used per link
    full_triangle: np.ndarray


def _link_mask(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    # lm() drops NA rows; a zero / negative C[k] would get an infinite /
    # negative weight 1/C[k] (R errors), so those rows are left out too.
    return np.isfinite(x) & np.isfinite(y) & (x > 0)


def project_triangle(tri: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Fill the future cells column by column: C[i, j] = f[j-1] * C[i, j-1]."""
    full = np.array(tri, dtype=float, copy=True)
    for j in range(1, full.shape[1]):
        miss = np.isnan(full[:, j])
        full[miss, j] = f[j - 1] * full[miss, j - 1]
    return full


def fit_chain_ladder(tri: np.ndarray) -> ChainLadderFit:
    tri = np.asarray(tri, dtype=float)
    n = tri.shape[1]
    f = np.ones(n - 1)
    sigma = np.full(n - 1, np.nan)
    f_se = np.full(n - 1, np.nan)
    n_obs = np.zeros(n - 1, dtype=int)
    for k in range(n - 1):
        x, y = tri[:, k], tri[:, k + 1]
        ok = _link_mask(x, y)
        xk, yk = x[ok], y[ok]
        n_obs[k] = xk.size
        if xk.size == 0:
            continue                      # no data at all: leave f = 1
        f[k] = yk.sum() / xk.sum()
        if xk.size > 1:
            rss = np.sum((yk - f[k] * xk) ** 2 / xk)      # sum of w * r^2, w = 1/x
            sigma[k] = np.sqrt(rss / (xk.size - 1))
            f_se[k] = sigma[k] / np.sqrt(xk.sum())        # sigma / sqrt(sum w x^2)
    return ChainLadderFit(f, sigma, f_se, n_obs, project_triangle(tri, f))


# ---------------------------------------------------------------------------
# Mack standard errors
# ---------------------------------------------------------------------------

def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Intercept and slope of lm(y ~ x), dropping non-finite points."""
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 2:
        raise MackError("Too few usable development factors to extrapolate the "
                        "tail factor's standard error.")
    xm, ym = x.mean(), y.mean()
    slope = np.sum((x - xm) * (y - ym)) / np.sum((x - xm) ** 2)
    return ym - slope * xm, slope


@dataclass
class MackResult:
    origins: list[str]
    triangle: np.ndarray          # observed cumulative (NaN = future)
    full_triangle: np.ndarray     # projected; an extra 'Inf' column when tail > 1
    f: np.ndarray                 # link ratios followed by the tail factor
    f_se: np.ndarray
    sigma: np.ndarray
    F_se: np.ndarray
    process_risk: np.ndarray
    parameter_risk: np.ndarray
    total_se: float
    total_process_risk: np.ndarray
    total_parameter_risk: np.ndarray
    tail_factor: float
    fit: ChainLadderFit

    @property
    def mack_se(self) -> np.ndarray:
        return np.sqrt(self.process_risk ** 2 + self.parameter_risk ** 2)

    @property
    def latest(self) -> np.ndarray:
        return latest_values(self.triangle)

    @property
    def ultimate(self) -> np.ndarray:
        return self.full_triangle[:, -1]

    def by_origin(self) -> pd.DataFrame:
        """summary(MackChainLadder)$ByOrigin for every origin (not yet filtered)."""
        latest, ult = self.latest, self.ultimate
        se = self.mack_se[:, -1]
        with np.errstate(divide="ignore", invalid="ignore"):
            return pd.DataFrame({
                "Origin": self.origins,
                "Latest": latest,
                "Dev.To.Date": latest / ult,
                "Ultimate": ult,
                "IBNR": ult - latest,
                "Mack.S.E": se,
                "CV": se / (ult - latest),
            })

    def residuals(self) -> pd.DataFrame:
        """residuals(MackChainLadder): raw and standardised residuals per link.

        Standardised residuals are rstandard() of each weighted regression:
        r * sqrt(w) / (sigma * sqrt(1 - h)), with w = 1/x and h = x / sum(x).
        """
        rows = []
        tri = self.triangle
        for k in range(tri.shape[1] - 1):
            x, y = tri[:, k], tri[:, k + 1]
            ok = _link_mask(x, y)
            if ok.sum() < 2:
                continue
            fk, sk = self.fit.f[k], self.fit.sigma[k]
            xs = x[ok]
            h = xs / xs.sum()
            raw = y[ok] - fk * xs
            with np.errstate(divide="ignore", invalid="ignore"):
                std = raw / np.sqrt(xs) / (sk * np.sqrt(1 - h))
            for idx, r, s, fit_v in zip(np.flatnonzero(ok), raw, std, fk * xs):
                rows.append((idx + 1, k + 1, idx + k + 1, r, s, fit_v))
        out = pd.DataFrame(rows, columns=["origin.period", "dev.period", "cal.period",
                                          "residuals", "standard.residuals", "fitted.value"])
        return out[np.isfinite(out["standard.residuals"])].reset_index(drop=True)


def mack_chain_ladder(tri: np.ndarray, origins: list[str], tail: float = 1.0) -> MackResult:
    tri = np.asarray(tri, dtype=float)
    m, n = tri.shape
    if n > m:
        raise MackError(f"Number of origin periods, {m}, is less than the number of "
                        f"development periods, {n}.")
    if n < 2:
        raise MackError("Chain Ladder needs >= 2 development periods.")

    fit = fit_chain_ladder(tri)
    full = fit.full_triangle.copy()
    sigma = fit.sigma.copy()
    f_se = fit.f_se.copy()

    # est.sigma = "Mack": extrapolate sigma where a link has a single observation.
    for i in np.flatnonzero(np.isnan(sigma)):
        if i >= 2:
            s1, s2 = sigma[i - 1], sigma[i - 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = s1 ** 4 / s2 ** 2
            if np.isnan(ratio) or np.isinf(ratio):
                cand = np.min([s2 ** 2, s1 ** 2])
            else:
                cand = np.min([ratio, np.min([s2 ** 2, s1 ** 2])])
            sigma[i] = np.sqrt(np.abs(cand))
        elif i == 1 and np.isfinite(sigma[0]):
            sigma[i] = sigma[0]           # R errors here (no sigma[i-2])
        else:
            sigma[i] = 0.0                # R errors here too
        with np.errstate(divide="ignore", invalid="ignore"):
            f_se[i] = sigma[i] / np.sqrt(full[0, i])

    with np.errstate(divide="ignore", invalid="ignore"):
        F_se = sigma[None, :] / np.sqrt(full[:, : n - 1])

    tail = float(tail)
    f = np.append(fit.f, tail)
    if tail > 1:
        # tail_E: append the ultimate column
        full = np.column_stack([full, full[:, n - 1] * tail])
        # tail_SE: log-linear extrapolation of f.se and sigma to the tail position
        dev = np.arange(1, n, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            a, b = _ols(dev, np.log(fit.f - 1))
            tail_pos = (np.log(tail - 1) - a) / b
            a_se, b_se = _ols(dev, np.log(f_se[: n - 1]))
            a_sg, b_sg = _ols(dev, np.log(sigma[: n - 1]))
        tail_se = np.exp(a_se + b_se * tail_pos)
        tail_sigma = np.exp(a_sg + b_sg * tail_pos)
        f_se = np.append(f_se, tail_se)
        sigma = np.append(sigma, tail_sigma)
        with np.errstate(divide="ignore", invalid="ignore"):
            F_se = np.column_stack([F_se, tail_sigma / np.sqrt(full[:, n - 1])])

    # MackRecursive.S.E
    nn = full.shape[1]
    proc = np.where(np.isnan(full), np.nan, 0.0)
    param = proc.copy()
    for k in range(nn - 1):
        for i in range(m - k - 1, m):
            proc[i, k + 1] = np.sqrt(full[i, k] ** 2 * F_se[i, k] ** 2
                                     + proc[i, k] ** 2 * f[k] ** 2)
            param[i, k + 1] = np.sqrt(full[i, k] ** 2 * f_se[k] ** 2
                                      + param[i, k] ** 2 * f[k] ** 2)

    # TotalMack.S.E
    total_proc = np.sqrt(np.nansum(proc ** 2, axis=0))
    M = np.array([np.nansum(full[max(m - k - 1, 0):, k]) for k in range(nn)])
    total_param = np.zeros(nn)
    for k in range(nn - 1):
        total_param[k + 1] = np.sqrt(M[k] ** 2 * f_se[k] ** 2
                                     + total_param[k] ** 2 * f[k] ** 2)
    total_se = float(np.sqrt(total_proc ** 2 + total_param ** 2)[nn - 1])

    return MackResult(origins=list(origins), triangle=tri, full_triangle=full, f=f,
                      f_se=f_se, sigma=sigma, F_se=F_se, process_risk=proc,
                      parameter_risk=param, total_se=total_se,
                      total_process_risk=total_proc, total_parameter_risk=total_param,
                      tail_factor=tail, fit=fit)


def mack_table(mack: MackResult) -> pd.DataFrame:
    """By-origin Mack table plus a Total row, as shown on the Reserve Estimates tab.

    Origins with a zero latest are dropped, like summary.MackChainLadder does.
    The Total row carries Mack's aggregate standard error (the R app looked it
    up under the wrong row name and always showed NA there).
    """
    by = mack.by_origin()
    by = by[by["Latest"] != 0].reset_index(drop=True)
    latest_all, ult_all = mack.latest, mack.ultimate
    tot_ibnr = np.nansum(ult_all - latest_all)
    total = pd.DataFrame({
        "Origin": ["Total"],
        "Latest": [np.nansum(latest_all)],
        "Dev.To.Date": [np.nan],
        "Ultimate": [np.nansum(ult_all)],
        "IBNR": [tot_ibnr],
        "Mack.S.E": [mack.total_se],
        "CV": [mack.total_se / tot_ibnr if tot_ibnr != 0 else np.nan],
    })
    return pd.concat([by, total], ignore_index=True)
