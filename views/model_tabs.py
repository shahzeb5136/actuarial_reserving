"""Tabs 5-8: reserve estimates, bootstrap, Mack diagnostics, blended estimate."""
from __future__ import annotations

import streamlit as st

from reserving import insights
from ui import charts
from ui import insight_charts as ic
from ui.components import card, empty_state, explain, note, table
from ui.state import Ctx


def _skip_reason(res, prefix: str) -> str | None:
    for _, text in res.messages:
        if text.startswith(prefix):
            return text
    return None


def _cdf_markers(res, ar, sims) -> list[tuple[str, float, float, str]]:
    """Chain ladder, recommended and booked totals placed on the simulated distribution."""
    cl = float(res.reserve_df.loc[res.reserve_df["Origin"] == "Total", "IBNR"].iloc[0])
    marks = [("Chain Ladder", cl, charts.RED)]
    if res.blend is not None:
        t = res.blend.totals
        marks.append(("Recommended", float(t.loc[t["Method"] == "Blended (recommended)",
                                                "Total.IBNR"].iloc[0]), charts.NAVY))
    if ar is not None:
        booked = float(ar["Selected.IBNR"].sum())
        if all(abs(booked - v) > 0.5 for _, v, _ in marks):
            marks.append(("Booked", booked, charts.BRICK))
    return [(lbl, v, insights.percentile_of(v, sims), c) for lbl, v, c in marks]


def reserve_estimates(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    with card("Mack Chain Ladder — by origin"):
        table(res.reserve_df, money=["Latest", "Ultimate", "IBNR", "Mack.S.E"],
              pct=["Dev.To.Date", "CV"])
        explain("The headline result: for each starting period, how much has been paid so far "
                "(Latest), the estimated final cost (Ultimate), and the gap still expected to be "
                "paid (IBNR — the reserve we need to hold). The last column flags how uncertain each "
                "estimate is — larger means less mature, less reliable. The 'Total' row is the "
                "bottom line.",
                "Mack Chain Ladder by-origin summary. Latest = most recent cumulative; Dev.To.Date "
                "= Latest/Ultimate; Ultimate = projected final; IBNR = Ultimate − Latest; Mack.S.E "
                "= Mack's analytic standard error of the IBNR (captures estimation + process "
                "error); CV = coefficient of variation of IBNR (S.E. / IBNR). Totals row uses "
                "Mack's correlated aggregation, not a simple column sum of S.E.")
    with card("IBNR by origin with ±1 standard error"):
        st.plotly_chart(ic.ibnr_with_se(res.reserve_df), width="stretch")
        explain("The reserve needed for each period with its uncertainty band. The newest periods "
                "carry both the biggest reserves and the widest bands - that is where most of the "
                "risk in the total sits.",
                "Mack chain-ladder IBNR by origin with whiskers at ±1 Mack standard error (process "
                "plus parameter error); hover for the coefficient of variation. The total S.E. is "
                "not the sum of these because of correlation and diversification across origins.")
    with card("Method comparison — total IBNR"):
        table(res.method_cmp, money=["Total.IBNR", "Std.Err"])
        explain("A side-by-side of the reserve estimate produced by each method. When the "
                "different methods land in roughly the same place, that's reassuring. A wide spread "
                "is a signal to dig deeper before settling on a number.",
                "Total IBNR and its standard error across Mack Chain Ladder, Bootstrap ODP (mean "
                "and SD of simulated reserves), Bornhuetter-Ferguson, and Cape Cod. When an "
                "exposure / earned-premium file is loaded, BF uses a premium a-priori and Cape Cod "
                "uses an exposure-weighted ELR (both on a developed basis); otherwise they fall back "
                "to CL-seeded and equal-exposure assumptions respectively. Material divergence "
                "usually points to thin tails, an unstable diagonal, or sensitivity to the "
                "a-priori assumption.")
    with card("Total IBNR by method"):
        st.plotly_chart(ic.method_totals_bars(res.method_cmp), width="stretch")
        explain("The bottom-line reserve from each method side by side. Bars of similar length mean "
                "the methods agree; the whiskers show the uncertainty where a method measures it.",
                "Total IBNR from the method comparison table: Mack chain ladder (±1 Mack S.E.), "
                "bootstrap ODP mean (±1 SD of the simulated totals), Bornhuetter-Ferguson and Cape "
                "Cod (point estimates, no whisker).")


def bootstrap(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    if res.boot is None:
        note("Bootstrap unavailable. ", _skip_reason(res, "Bootstrap skipped") or
             "The bootstrap did not run for this triangle.", "warning")
        return
    left, right = st.columns([7, 5])
    with left, card("Predictive distribution of total IBNR"):
        st.plotly_chart(charts.bootstrap_histogram(res.boot.sim_totals), width="stretch")
        explain("Instead of a single reserve number, this shows the full range of plausible "
                "outcomes from thousands of simulated scenarios. Taller bars are more likely "
                "results. The dashed lines mark the midpoint and the high-end (75th and 95th) cases "
                "— useful for thinking about how much cushion to hold for a bad year.",
                "Histogram of total IBNR across R bootstrap replications of the over-dispersed "
                "Poisson chain ladder (Gamma or ODP process distribution). Dashed verticals mark "
                "the 50th, 75th, and 95th percentiles of the simulated total-reserve distribution, "
                "capturing combined estimation and process variability.")
    with right, card("Quantiles"):
        table(res.boot.quantiles(), money=["IBNR"])
        explain("The same simulation results as a table. It reads as: 'there's a 75% chance the "
                "reserve need comes in at or below this figure.' Higher percentiles (95%, 99%) are "
                "the stress / worst-case levels.",
                "Summary statistics (mean, SD) and selected percentiles (5/25/50/75/95/99/99.5%) of "
                "the simulated total-IBNR vector. Upper quantiles support risk-margin / capital and "
                "reinsurance attachment discussions.")
    with card("How likely is the reserve to be enough?"):
        sims = res.boot.sim_totals
        st.plotly_chart(ic.reserve_cdf(insights.cdf_points(sims), _cdf_markers(res, ctx.ar, sims)),
                        width="stretch")
        explain("Read across from any reserve level to the chance that claims come in at or below "
                "it. The markers show where the chain-ladder, recommended and booked reserves sit - "
                "'75%' means a 3-in-4 chance that reserve will be enough.",
                "Empirical cumulative distribution of total IBNR across the bootstrap simulations; "
                "each marker sits at its empirical percentile (share of simulations at or below the "
                "figure), i.e. the confidence level implied by that reserve.")
    with card("Simulated IBNR by origin — spread"):
        st.plotly_chart(ic.origin_spread(insights.origin_bands(res.boot)), width="stretch")
        explain("The range of simulated reserves for each period. Taller boxes and longer whiskers "
                "mean more uncertainty - it is concentrated in the newest periods.",
                "Per-origin distribution of simulated IBNR: box from the 25th to the 75th "
                "percentile, median line, whiskers at the 5th and 95th percentiles. Fully developed "
                "origins collapse to zero.")
    with card("Bootstrap by origin"):
        by = res.boot.by_origin()
        table(by, money=[c for c in by.columns if c != "Origin"])
        explain("Breaks the simulated reserve down by starting period, showing the average "
                "estimate and its variability for each. This pinpoints which periods contribute "
                "most of the uncertainty.",
                "Per-origin bootstrap summary: simulated IBNR mean, standard error, and percentiles "
                "by origin period. Origin-level S.E.s do not add to the total S.E. because of "
                "diversification across origins.")


def mack_diagnostics(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    left, right = st.columns(2)
    with left, card("Mack summary plots"):
        st.plotly_chart(charts.mack_results(res.reserve_df), width="stretch")
        explain("A visual health check on the projection. It shows the claims paid to date "
                "alongside the projected remainder for each period, with the uncertainty drawn as "
                "error bars. Bigger bars mean a less certain estimate — typically the most recent, "
                "least-developed periods.",
                "Standard MackChainLadder summary panel: latest cumulative plus projected IBNR by "
                "origin with Mack standard-error bars, and the aggregated forecast. Conveys the "
                "relative scale of estimation uncertainty across origin periods.")
    with right, card("Chain ladder developments by origin period"):
        st.plotly_chart(charts.mack_developments(res.mack), width="stretch")
        explain("Each line follows one starting period as its payments accumulate. The solid part "
                "is what has actually been paid; the dotted continuation is where the chain ladder "
                "expects it to end up. Lines that bend unusually are worth a closer look.",
                "Cumulative claims by development period for every origin (plot.MackChainLadder "
                "panel 2): observed cells solid with markers, the chain-ladder projection of the "
                "unobserved cells dotted, including the tail column when a tail factor is applied.")
    with card("Residual diagnostics"):
        st.plotly_chart(charts.mack_residuals(res.mack), width="stretch")
        explain("This checks whether the model's assumptions hold up. Ideally the dots scatter "
                "randomly around the centre line with no pattern. A clear trend or fanning shape is "
                "a warning that the standard method may be over- or under-stating the reserve and "
                "needs expert review.",
                "Standardised residuals plotted against development period, origin period, and "
                "fitted values. Patterns, trends, or heteroscedasticity violate Mack's assumptions "
                "(independent, proportional-variance development), flagging the need for tail "
                "adjustment, weighting, or an alternative model.")
    with card("Where the uncertainty comes from — process vs parameter risk"):
        st.plotly_chart(ic.risk_components_bars(insights.risk_components(res.mack)), width="stretch")
        explain("Splits each period's uncertainty into two sources: randomness in the future claims "
                "themselves (process risk) and doubt about development factors estimated from "
                "limited history (parameter risk). Where parameter risk dominates, more or better "
                "data would narrow the range.",
                "Mack's decomposition of the standard error of each origin's ultimate "
                "(MackRecursive.S.E) into process and parameter components. They combine in "
                "quadrature, S.E.² = process² + parameter²; parameter errors are also correlated "
                "across origins, which is why the total S.E. is not a simple sum either.")


def blended(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    note("Why this tab. ",
         "Chain Ladder is reliable for mature origin periods but becomes erratic for the newest "
         "ones - it multiplies a tiny, noisy latest figure by the full remaining development "
         "pattern, so the youngest period (and lag-1 cells, especially on monthly data) can swing "
         "wildly. Here we anchor the green periods to a stable, seasonally-adjusted prior and let "
         "each period's own data earn weight only as it matures.", "info")
    b = res.blend
    if b is None:
        note("Blend unavailable. ", _skip_reason(res, "Blend skipped") or "", "warning")
        return
    with card("Recommended ultimate & IBNR by origin (blended)"):
        d = b.detail if b.has_exposure else b.detail.drop(columns=["EarnedPrem", "Exposure"])
        table(d, money=[c for c in ("Latest", "EarnedPrem", "Exposure", "CL", "Expected", "BF",
                                    "Benktander", "CapeCod", "Blended", "IBNR.CL", "IBNR.Blend")
                        if c in d.columns],
              pct=["Pct.Dev", "Credibility"])
        explain("This is the recommended reserve. For older periods it follows the standard Chain "
                "Ladder estimate (which works well once a period is mostly settled). For the newest "
                "periods it leans on a stable expected level instead, because their raw projections "
                "are too jumpy to trust. The 'Credibility' column shows the mix: near 1.00 means "
                "almost all Chain Ladder; near 0 means almost all the stable prior.",
                "Per-origin blend Ultimate = Z * CL + (1 - Z) * Prior, with maturity credibility "
                "Z = (percent-developed)^kappa and Prior drawn from the selected BF / Benktander / "
                "Cape Cod family. Columns show each candidate estimator (CL, Expected/seasonal, BF, "
                "Benktander, Cape Cod) plus the blended result and the implied CL vs blended IBNR.")
    with card("Method comparison — totals"):
        table(b.totals, money=["Total.Ultimate", "Total.IBNR"])
        explain("The total reserve each method would produce. The blended figure is the "
                "recommendation; the others are shown so you can see how much the choice of method "
                "moves the bottom line. A large spread is a signal the newest periods are driving "
                "most of the uncertainty.",
                "Aggregate ultimate and IBNR across Chain Ladder, Expected/seasonal prior, "
                "Bornhuetter-Ferguson, Benktander, Cape Cod, and the blend. Divergence concentrates "
                "in low-maturity origins where CL leverage is highest.")
    with card("How sensitive is the total to kappa and the prior?"):
        cl_total = float(b.totals.loc[b.totals["Method"] == "Chain Ladder", "Total.IBNR"].iloc[0])
        st.plotly_chart(ic.kappa_sensitivity_chart(insights.kappa_sensitivity(b), b.kappa,
                                                   b.prior_name, cl_total, insights.PRIOR_LABELS),
                        width="stretch")
        explain("How the recommended total would change with a different credibility steepness "
                "(left to right) or a different stabilising prior (each line). A flat line means "
                "the choice hardly matters; a steep one means the setting materially drives the "
                "reserve and should be justified. The dot marks the current setting.",
                "Total blended IBNR = sum of [Z x CL + (1 - Z) x Prior] - sum of Latest, with Z = "
                "%developed^kappa, recomputed for kappa from 0.5 to 3.0 under each prior using this "
                "run's estimators. The red line is the pure chain-ladder total - the limit as kappa "
                "falls towards 0.")
    with card("Ultimate by origin — method curves"):
        st.plotly_chart(charts.blend_curves(b), width="stretch")
        explain("Each line is one method's estimate across the starting periods (oldest on the "
                "left, newest on the right). On the left the lines sit on top of each other - "
                "everyone agrees once a period is mature. On the right they fan apart, and that fan "
                "is exactly the instability we are managing. The blended line stays in the sensible "
                "middle of the fan.",
                "Ultimate by origin for every estimator. Convergence at high maturity and divergence "
                "at low maturity visualises CL's tail leverage. The blended series tracks CL where "
                "Z is high and migrates toward the prior as Z falls.")
    with card("Credibility weighting by maturity"):
        st.plotly_chart(charts.credibility_curve(b), width="stretch")
        explain("Shows how much trust each period's own data is given, based on how developed it "
                "is. Fully-developed periods get full trust; brand-new periods get little, with the "
                "gap filled by the stable prior. The steepness slider in the sidebar controls how "
                "cautious this is.",
                "Credibility Z = (percent-developed)^kappa plotted against origin maturity. Raising "
                "kappa pushes Z down for immature periods (more prior weight, more stability); "
                "lowering it lets data earn weight sooner (more responsiveness).")
    with card("CL vs Blended IBNR by origin"):
        st.plotly_chart(charts.blend_ibnr(b), width="stretch")
        explain("A direct before/after on the reserve held per period: raw Chain Ladder next to "
                "the blended recommendation. The bars match for older periods and differ most for "
                "the newest ones - which is precisely where the blend is doing its job of taming "
                "volatile estimates.",
                "Grouped bars of IBNR (Ultimate minus Latest) under CL vs the blend by origin. "
                "Differences are concentrated in the youngest origins; mature origins are unchanged "
                "because Z approx 1 there.")
