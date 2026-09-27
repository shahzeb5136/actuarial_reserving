"""Run the full analysis for one set of settings.

Mirrors the Shiny server's `analysis_results` eventReactive: filter by
sub-category, build the triangles, align the (optional) exposure table, then
run Mack, the bootstrap, BF, Cape Cod, the recast back-test, seasonality and
the credibility blend.  Non-fatal problems are collected as messages (the R
app showed them as notifications) instead of stopping the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .bootstrap import BootstrapResult, run_bootstrap
from .data import ALL_CATEGORIES, ExposureAlignment, align_exposure, filter_subcat
from .mack import MackResult, mack_chain_ladder, mack_table
from .methods import BlendResult, exposure_apriori, run_bf, run_blend, run_capecod
from .recast import RecastResult, recast_analysis
from .seasonality import SeasonalityResult, seasonality_analysis
from .triangles import Triangles, build_triangles, triangle_frame


class AnalysisError(ValueError):
    """The analysis cannot run with these data / settings."""


@dataclass(frozen=True)
class AnalysisSettings:
    subcat: str = ALL_CATEGORIES
    period: str = "quarter"          # "month" | "quarter"
    tail: float = 1.0
    n_sims: int = 1000
    boot_dist: str = "gamma"         # "gamma" | "od.pois"
    n_holdback: int = 1
    blend_prior: str = "benktander"  # "benktander" | "bf" | "capecod"
    blend_kappa: float = 1.5
    blend_seasonal: bool = True
    seed: int | None = None          # bootstrap seed (None = fresh draws each run)


@dataclass
class AnalysisResults:
    settings: AnalysisSettings
    sel: str
    df_f: pd.DataFrame
    triangles: Triangles
    incr_df: pd.DataFrame
    cum_df: pd.DataFrame
    dev_df: pd.DataFrame
    mack: MackResult
    reserve_df: pd.DataFrame
    boot: BootstrapResult | None
    bf: pd.DataFrame | None
    bf_label: str
    capecod: pd.DataFrame | None
    method_cmp: pd.DataFrame
    recast: RecastResult | None
    recast_error: str | None
    seas: SeasonalityResult | None
    blend: BlendResult | None
    exp_al: ExposureAlignment | None
    ep_vec: np.ndarray | None
    exposure_vec: np.ndarray | None
    messages: list[tuple[str, str]] = field(default_factory=list)   # (level, text)


def method_comparison(mack: MackResult, boot: BootstrapResult | None, boot_dist: str,
                      bf: pd.DataFrame | None, bf_label: str,
                      capecod: pd.DataFrame | None, exposure_based: bool) -> pd.DataFrame:
    rows = [("Mack Chain Ladder", float(np.nansum(mack.ultimate - mack.latest)), mack.total_se)]
    if boot is not None:
        s = boot.sim_totals
        rows.append((f"Bootstrap ODP ({boot_dist})", s.mean(), s.std(ddof=1)))
    if bf is not None:
        rows.append((bf_label, bf["IBNR"].sum(skipna=False), np.nan))
    if capecod is not None:
        label = "Cape Cod (exposure ELR)" if exposure_based else "Cape Cod (equal-exposure)"
        rows.append((label, capecod["IBNR"].sum(skipna=False), np.nan))
    return pd.DataFrame(rows, columns=["Method", "Total.IBNR", "Std.Err"])


def run_analysis(claims: pd.DataFrame, settings: AnalysisSettings,
                 exposure: pd.DataFrame | None = None) -> AnalysisResults:
    msgs: list[tuple[str, str]] = []
    s = settings
    df_f = filter_subcat(claims, s.subcat)
    if df_f.empty:
        raise AnalysisError(f"No data for subcategory: {s.subcat}")

    tri = build_triangles(df_f, s.period)
    cum = tri.cum
    origins = tri.origins
    if cum.shape[0] <= 1:
        raise AnalysisError("Chain Ladder needs >= 2 origin periods.")
    if cum.shape[1] <= 1:
        raise AnalysisError("Chain Ladder needs >= 2 development periods.")

    # ---- Exposure / earned premium alignment (optional) ----
    exp_al = ep_vec = exposure_vec = None
    if exposure is not None:
        try:
            exp_al = align_exposure(exposure, tri.origin_dates, s.period)
        except Exception as exc:  # noqa: BLE001
            msgs.append(("warning", f"Exposure alignment: {exc}"))
        if exp_al is not None:
            ep_vec, exposure_vec = exp_al.earned_premium, exp_al.exposure
            if exp_al.matched == 0:
                msgs.append(("warning", "Exposure file loaded but no periods matched the "
                                        "triangle - check the Period column. Using data-only "
                                        "estimates."))
                exp_al = ep_vec = exposure_vec = None
            elif exp_al.matched < exp_al.n_origins:
                msgs.append(("info", f"Exposure matched {exp_al.matched} of {exp_al.n_origins} "
                                     "origin periods; unmatched periods use the data-only estimate."))

    # ---- Mack ----
    try:
        mack = mack_chain_ladder(cum, origins, tail=s.tail)
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"Mack error: {exc}") from exc

    # ---- Bootstrap ----
    boot = None
    try:
        boot = run_bootstrap(cum, origins, n_sims=s.n_sims, process_distr=s.boot_dist, seed=s.seed)
    except Exception as exc:  # noqa: BLE001
        msgs.append(("warning", f"Bootstrap skipped: {exc}"))

    # ---- BF (premium a-priori if supplied, else CL-seeded) ----
    try:
        if ep_vec is not None:
            bf = run_bf(cum, origins, exposure_apriori(cum, mack.f, ep_vec).apriori)
            bf_label = "Bornhuetter-Ferguson (premium a-priori)"
        else:
            bf = run_bf(cum, origins, mack.ultimate)
            bf_label = "Bornhuetter-Ferguson (CL a-priori)"
    except Exception:  # noqa: BLE001
        bf, bf_label = None, "Bornhuetter-Ferguson"

    # ---- Cape Cod (exposure ELR if supplied, else equal-exposure) ----
    try:
        capecod = run_capecod(cum, origins, mack.f, exposure=exposure_vec)
    except Exception:  # noqa: BLE001
        capecod = None

    method_cmp = method_comparison(mack, boot, s.boot_dist, bf, bf_label, capecod,
                                   exposure_based=exposure_vec is not None)

    # ---- Recast ----
    recast, recast_error = None, None
    try:
        recast = recast_analysis(cum, origins, n_holdback=s.n_holdback)
    except Exception as exc:  # noqa: BLE001
        recast_error = str(exc)
        msgs.append(("warning", f"Recast: {recast_error}"))

    # ---- Seasonality ----
    try:
        seas = seasonality_analysis(df_f)
    except Exception:  # noqa: BLE001
        seas = None

    # ---- Blend ----
    blend = None
    try:
        seas_vec = seas.payment_index_vector() if (s.blend_seasonal and seas is not None) else None
        blend = run_blend(cum, origins, mack, seas_index=seas_vec, period=s.period,
                          origin_dates=tri.origin_dates, kappa=s.blend_kappa,
                          prior=s.blend_prior, earned_premium=ep_vec, exposure=exposure_vec)
    except Exception as exc:  # noqa: BLE001
        msgs.append(("warning", f"Blend skipped: {exc}"))

    n_f = len(mack.f)
    dev_df = pd.DataFrame({"Transition": [f"Dev{k}->{k + 1}" for k in range(1, n_f + 1)],
                           "Factor": mack.f})

    return AnalysisResults(
        settings=s, sel=s.subcat, df_f=df_f, triangles=tri,
        incr_df=triangle_frame(tri.incremental), cum_df=triangle_frame(tri.cumulative),
        dev_df=dev_df, mack=mack, reserve_df=mack_table(mack), boot=boot, bf=bf,
        bf_label=bf_label, capecod=capecod, method_cmp=method_cmp, recast=recast,
        recast_error=recast_error, seas=seas, blend=blend, exp_al=exp_al, ep_vec=ep_vec,
        exposure_vec=exposure_vec, messages=msgs)
