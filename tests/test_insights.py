"""The derived views behind the extra charts must reconcile with the core results."""
import numpy as np
import pytest

from reserving import insights
from reserving.adjustments import adjusted_reserves, default_basis, initial_state, method_ultimates
from reserving.pipeline import AnalysisSettings, run_analysis


@pytest.fixture(scope="module")
def run(claims):
    res = run_analysis(claims, AnalysisSettings(period="quarter", n_sims=500, seed=3))
    mu = method_ultimates(res)
    ar = adjusted_reserves(initial_state(mu["Origin"], default_basis(res)), mu, default_basis(res))
    return res, ar


def test_claims_mix_and_delay_profile_cover_every_dollar(claims):
    total = claims["ClaimAmount"].sum()
    mix = insights.claims_by_origin_month(claims)
    assert np.isclose(mix["Amount"].sum(), total)
    assert set(mix["SubCat"]) == {"A", "B", "C"}
    folded = insights.claims_by_origin_month(claims, max_series=2)
    assert set(folded["SubCat"]) == {"B", "Other"} and np.isclose(folded["Amount"].sum(), total)
    prof = insights.payment_delay_profile(claims)
    assert np.isclose(prof["Amount"].sum(), total) and np.isclose(prof["CumShare"].iloc[-1], 1)
    assert insights.first_reaching(prof["CumShare"], prof["Delay"].tolist(), 0.5) is not None


def test_development_pattern_matches_percent_developed(run):
    res, _ = run
    pat = insights.development_pattern(res.mack.f)
    assert np.isclose(pat["Cumulative"].iloc[-1], 1.0)
    assert np.all(np.diff(pat["Cumulative"]) >= -1e-12)
    assert np.isclose(pat["Incremental"].sum(), 1.0)
    # the newest origin sits at its % developed after one period
    assert np.isclose(pat["Cumulative"].iloc[0], res.blend.detail["Pct.Dev"].iloc[-1])
    tail = insights.development_pattern(np.append(res.mack.f[:-1], 1.05))
    assert tail["Period"].iloc[-1] == "Ult" and np.isclose(tail["Cumulative"].iloc[-2], 1 / 1.05)


def test_payment_pattern_and_link_ratios(run):
    res, _ = run
    cum = res.triangles.cum
    pattern = insights.payment_pattern(cum, res.mack.ultimate)
    np.testing.assert_allclose(pattern[0, -1], 1.0)          # fully developed origin
    ratios, dev = insights.link_ratio_deviation(cum, res.mack.f)
    # volume-weighted average of the individual ratios reproduces the selected factor
    w = cum[:, :-1]
    ok = np.isfinite(ratios)
    sel = np.nansum(np.where(ok, ratios * w, 0), axis=0) / np.nansum(np.where(ok, w, 0), axis=0)
    np.testing.assert_allclose(sel, res.mack.f[:-1])
    obs = insights.observed_incremental(res.triangles.incremental.to_numpy(), cum)
    np.testing.assert_allclose(np.nansum(obs, axis=1), res.mack.latest)


def test_kappa_sensitivity_reproduces_the_blend(run):
    res, _ = run
    b = res.blend
    sens = insights.kappa_sensitivity(b)
    cur = sens[(sens["Prior"] == b.prior_name) & np.isclose(sens["Kappa"], b.kappa)]
    blended = b.totals.loc[b.totals["Method"] == "Blended (recommended)", "Total.IBNR"].iloc[0]
    assert np.isclose(cur["TotalIBNR"].iloc[0], blended)
    near_zero = insights.kappa_sensitivity(b, kappas=[1e-9])
    cl = b.totals.loc[b.totals["Method"] == "Chain Ladder", "Total.IBNR"].iloc[0]
    np.testing.assert_allclose(near_zero["TotalIBNR"], cl, rtol=1e-6)


def test_uncertainty_views(run):
    res, _ = run
    sims = res.boot.sim_totals
    pts = insights.cdf_points(sims)
    assert pts["IBNR"].is_monotonic_increasing and pts["Prob"].iloc[-1] == 1
    assert insights.percentile_of(np.median(sims), sims) == pytest.approx(0.5, abs=0.01)
    bands = insights.origin_bands(res.boot)
    assert (bands[["P5", "P25", "P50", "P75", "P95"]].diff(axis=1).iloc[:, 1:] >= -1e-9).all().all()
    rc = insights.risk_components(res.mack)
    np.testing.assert_allclose(np.hypot(rc["Process"], rc["Parameter"]), rc["Total"])


def test_booked_comparisons(run):
    res, ar = run
    mt = insights.method_totals(res)
    assert (mt["Kind"] == "recommended").sum() == 1
    ladder = insights.reserve_ladder(res, ar)
    assert ladder["Value"].is_monotonic_increasing
    assert "Booked (after adjustments)" not in set(ladder["Label"])      # no overlay yet
    err = insights.recast_error_by_origin(res.recast)
    total = res.recast.summary["Total.Pct"].iloc[0]
    assert np.isclose(err["Actual"].sum() / err["Expected"].sum() - 1, total)


def test_paid_by_payment_month(claims):
    pm = insights.paid_by_payment_month(claims)
    assert np.isclose(pm["Paid"].sum(), claims["ClaimAmount"].sum())
    assert pm["Trend"].iloc[:11].isna().all()
    assert np.isclose(pm["Trend"].iloc[11], pm["Paid"].iloc[:12].mean())


def test_loss_ratios_with_exposure(claims):
    from pathlib import Path
    from reserving.data import read_exposure_table
    exposure = read_exposure_table(Path(__file__).parents[1] / "sample_data" / "exposure_premium.xlsx")
    res = run_analysis(claims, AnalysisSettings(period="quarter", n_sims=100, seed=1), exposure)
    lr = insights.loss_ratios(res)
    np.testing.assert_allclose(lr["PaidLR"], res.blend.detail["Latest"] / res.exp_al.earned_premium)
    np.testing.assert_allclose(lr["UltimateLR"] * lr["Premium"], res.blend.detail["Blended"])
    assert insights.loss_ratios(run_analysis(claims, AnalysisSettings(n_sims=100, seed=1))) is None
