"""Over-dispersed Poisson bootstrap of the chain ladder (England & Verrall).

A port of R ChainLadder::BootChainLadder (v0.2.21): same fitted values,
Pearson residuals, scale parameter, residual resampling, re-fitting and
process distributions ("gamma" or "od.pois").  Simulations are vectorised
with numpy and processed in chunks to keep memory bounded.

Random numbers come from numpy rather than R's generator, so individual
simulated values differ from R while the distribution is the same.  Tests can
inject R's residual draws via ``resid_sample`` to check the arithmetic exactly.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

QUANTILE_LABELS = ["Mean", "SD", "5%", "25%", "50%", "75%", "95%", "99%", "99.5%"]
_QUANTILE_PROBS = [0.05, 0.25, 0.50, 0.75, 0.95, 0.99, 0.995]


class BootstrapError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Array helpers mirroring ChainLadder's internal (n x n x R) routines, written
# for arrays shaped (sims, origin, dev).
# ---------------------------------------------------------------------------

def _incremental(cum: np.ndarray) -> np.ndarray:
    out = cum.copy()
    out[..., 1:] = cum[..., 1:] - cum[..., :-1]
    return out


def _indiv_dfs(cum: np.ndarray) -> np.ndarray:
    out = np.full(cum.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[..., :-1] = cum[..., 1:] / cum[..., :-1]
    return out


def _av_dfs(dfs: np.ndarray, wghts: np.ndarray) -> np.ndarray:
    """getAvDFs: weighted average link ratios (weights = cumulative claims)."""
    include = (~np.isnan(dfs) & ~np.isnan(wghts)).astype(float)
    d = np.where(np.isnan(dfs), 0.0, dfs)
    w = np.where(np.isnan(wghts), 0.0, wghts)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.sum(d * w * include, axis=-2) / np.sum(w * include, axis=-2)
    out[np.isnan(out)] = 1.0
    return out                                   # (..., dev)


def _ult_dfs(av: np.ndarray) -> np.ndarray:
    """getUltDFs: cumulative product of link ratios from the right."""
    out = av.copy()
    for i in range(out.shape[-1] - 2, -1, -1):
        out[..., i] = out[..., i + 1] * out[..., i]
    return out


def _diagonal(cum: np.ndarray) -> np.ndarray:
    """Latest diagonal of a square triangle: cell (i, n-1-i) for each row i."""
    n = cum.shape[-1]
    return cum[..., np.arange(n), n - 1 - np.arange(n)]


def _expected_incremental(latest: np.ndarray, ult_dfs: np.ndarray) -> np.ndarray:
    """Fitted incrementals by backward recursion from the latest diagonal."""
    ults = latest * ult_dfs[..., ::-1]                         # getUltimates
    cum = ults[..., :, None] * (1.0 / ult_dfs)[..., None, :]   # getExpected
    return _incremental(cum)


@dataclass
class BootstrapSetup:
    """Deterministic part of the bootstrap (no random numbers yet)."""
    exp_inc: np.ndarray          # fitted incrementals, NaN outside the observed triangle
    unscaled_residuals: np.ndarray
    adj_residuals: np.ndarray
    scale_phi: float
    link_ratios: np.ndarray


def bootstrap_setup(cum: np.ndarray) -> BootstrapSetup:
    cum = np.asarray(cum, dtype=float)
    m, n = cum.shape
    if m != n:
        raise BootstrapError(
            f"the ODP bootstrap needs a square triangle (as many origin periods as "
            f"development periods); this one is {m} x {n}.")
    inc = _incremental(cum)
    av = _av_dfs(_indiv_dfs(cum), cum)
    exp_inc = _expected_incremental(_diagonal(cum), _ult_dfs(av))
    exp_inc[np.isnan(inc)] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        unscaled = (inc - exp_inc) / np.sqrt(np.abs(exp_inc))
    nobs = 0.5 * n * (n + 1)
    scale_factor = nobs - 2 * n + 1
    with np.errstate(divide="ignore", invalid="ignore"):
        phi = np.nansum(unscaled ** 2) / scale_factor
        adj = unscaled * np.sqrt(nobs / scale_factor)
    return BootstrapSetup(exp_inc, unscaled, adj, float(phi), av)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class BootstrapResult:
    origins: list[str]
    latest: np.ndarray            # latest cumulative per origin
    ibnr_by_origin: np.ndarray    # (origins, sims) simulated IBNR
    process_distr: str
    scale_phi: float = np.nan
    param_by_origin: np.ndarray | None = None   # simulated expected IBNR (no process noise)

    @property
    def sim_totals(self) -> np.ndarray:
        return self.ibnr_by_origin.sum(axis=0)

    @property
    def n_sims(self) -> int:
        return self.ibnr_by_origin.shape[1]

    def quantiles(self) -> pd.DataFrame:
        s = self.sim_totals
        vals = [s.mean(), s.std(ddof=1), *np.quantile(s, _QUANTILE_PROBS)]
        return pd.DataFrame({"Quantile": QUANTILE_LABELS, "IBNR": vals})

    def by_origin(self) -> pd.DataFrame:
        """summary(BootChainLadder)$ByOrigin (origins with zero latest dropped)."""
        ib = self.ibnr_by_origin
        mean = ib.mean(axis=1)
        q = np.quantile(ib, [0.75, 0.95], axis=1)
        out = pd.DataFrame({
            "Origin": self.origins,
            "Latest": self.latest,
            "Mean Ultimate": self.latest + mean,
            "Mean IBNR": mean,
            "SD IBNR": ib.std(axis=1, ddof=1),
            "IBNR 75%": q[0],
            "IBNR 95%": q[1],
        })
        return out[out["Latest"] != 0].reset_index(drop=True)


def run_bootstrap(cum: np.ndarray, origins: list[str], n_sims: int = 1000,
                  process_distr: str = "gamma", seed: int | None = None,
                  resid_sample: np.ndarray | None = None,
                  keep_param: bool = False) -> BootstrapResult:
    """Simulate the predictive distribution of IBNR.

    resid_sample: optional (n_sims, n, n) array of pre-drawn adjusted residuals
    (NaN outside the observed triangle) that replaces numpy's resampling - used
    by the parity tests to reproduce R's draws.
    """
    if process_distr not in ("gamma", "od.pois"):
        raise BootstrapError(f"unknown process distribution {process_distr!r}")
    cum = np.asarray(cum, dtype=float)
    setup = bootstrap_setup(cum)
    n = cum.shape[1]
    phi = setup.scale_phi
    exp_inc = setup.exp_inc
    past = ~np.isnan(exp_inc)
    pool = setup.adj_residuals[~np.isnan(setup.adj_residuals)]
    if pool.size == 0:
        raise BootstrapError("no residuals available to resample.")
    if resid_sample is not None:
        n_sims = resid_sample.shape[0]
    rng = np.random.default_rng(seed)
    root_abs_exp = np.sqrt(np.abs(exp_inc))

    ibnr = np.empty((n, n_sims))
    param = np.empty((n, n_sims)) if keep_param else None
    chunk = max(1, int(2_000_000 // (n * n)))
    for start in range(0, n_sims, chunk):
        stop = min(start + chunk, n_sims)
        c = stop - start
        if resid_sample is not None:
            samp = np.array(resid_sample[start:stop], dtype=float)
        else:
            samp = pool[rng.integers(0, pool.size, size=(c, n, n))]
        samp[:, ~past] = np.nan
        sim_claims = samp * root_abs_exp + exp_inc              # randomClaims
        sim_cum = np.cumsum(sim_claims, axis=2)                 # makeCumulative
        sim_av = _av_dfs(_indiv_dfs(sim_cum), sim_cum)
        sim_exp = _expected_incremental(_diagonal(sim_cum), _ult_dfs(sim_av))
        sim_exp[:, past] = np.nan                               # keep future cells only

        fut = ~np.isnan(sim_exp)
        mu = sim_exp[fut]
        draws = np.zeros(mu.shape)
        if process_distr == "gamma":
            draws = np.sign(mu) * rng.gamma(shape=np.abs(mu / phi), scale=phi)
        else:
            lam = np.abs(mu)
            pos = lam > 0
            if phi > 1:
                size = lam[pos] / (phi - 1)                     # rnbinom(size, mu = lam)
                draws[pos] = rng.negative_binomial(size, 1.0 / phi)
            else:
                draws[pos] = rng.poisson(lam[pos])
            draws = np.sign(mu) * draws
        process = np.zeros(sim_exp.shape)
        process[fut] = draws
        ibnr[:, start:stop] = process.sum(axis=2).T
        if keep_param:
            param[:, start:stop] = np.nan_to_num(sim_exp, nan=0.0).sum(axis=2).T

    latest = np.nansum(_incremental(cum), axis=1)
    return BootstrapResult(list(origins), latest, ibnr, process_distr, phi, param)
