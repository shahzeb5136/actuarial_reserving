"""Tabs 11-13: recast back-test, seasonality, executive summary."""
from __future__ import annotations

import html

import streamlit as st

from reserving import insights
from reserving.summary import exec_summary, recast_status
from ui import charts
from ui import insight_charts as ic
from ui.components import alert, card, empty_state, explain, key_points, kpi_cards, note, table
from ui.state import Ctx


def recast(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    alert(recast_status(res))
    rc = res.recast
    if rc is None:
        return
    with card("Back-test summary (Actual vs Expected on held-back diagonals)"):
        table(rc.summary, money=["Total.Actual", "Total.Expected", "Total.Diff", "RMSE"],
              pct=["Total.Pct", "MAPE"])
        explain("A track-record test: we hide the most recent actual data, ask the model to predict "
                "it, then reveal the truth and score the prediction. Small differences mean the "
                "method has been reliable on this data; a large gap means treat the current "
                "estimates with extra caution.",
                "Hold-out back-test. The latest N calendar diagonals are removed, Mack is re-fit on "
                "the truncated triangle, projected forward, and compared to the withheld actuals. "
                "Summary reports cell count, total actual vs expected, total dollar and % "
                "difference, MAPE, and RMSE of the actual-minus-expected errors.")
    with card("Actual vs Expected — detail"):
        table(rc.detail, money=["Actual", "Expected", "AvE_Diff"], pct=["AvE_Pct"],
              height=min(560, 38 + 35 * len(rc.detail)))
        explain("The cell-by-cell scorecard behind the summary above. It shows, for each hidden "
                "value, what the model predicted versus what actually happened, so you can see "
                "exactly where it ran high or low.",
                "Per-cell Actual-vs-Expected detail for each held-back cell: origin, development "
                "lag, actual cumulative, projected cumulative, dollar difference, and percentage "
                "error. Cells in fully-emptied trailing columns are excluded as non-projectable.")
    with card("Actual vs Expected by origin"):
        st.plotly_chart(charts.recast_bars(rc.detail), width="stretch")
        explain("The same back-test shown as bars per starting period: actual (dark) next to "
                "predicted (grey). Where the two bars match, the model did well; visible gaps "
                "highlight the periods where it was least accurate.",
                "Grouped bar chart of held-back cumulative actuals vs Mack-projected expecteds, "
                "aggregated by origin. Systematic one-sided gaps indicate bias in the development "
                "assumptions for specific origin cohorts.")
    with card("Prediction error by origin"):
        st.plotly_chart(ic.recast_error_bars(insights.recast_error_by_origin(rc)), width="stretch")
        explain("For each period, how far the held-back actual payments landed from the model's "
                "prediction. Red bars mean more was paid than predicted (the method would have "
                "under-reserved); blue bars mean less. Mostly one colour points to a systematic "
                "bias rather than noise.",
                "Per-origin sum of held-back actual cumulative over the Mack-projected expected, "
                "minus 1. The subtitle gives the aggregate ratio over all held-back cells (Total.Pct "
                "in the back-test summary).")


def seasonality(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    if res.seas is None:
        return note("Seasonality unavailable. ", "The seasonality analysis could not run.", "warning")
    with card("Seasonal index by month (100 = average)"):
        st.plotly_chart(charts.seasonality_lines(res.seas.index), width="stretch")
        explain("Shows whether certain months run busier than others. A value of 120 means that "
                "month is 20% above the yearly average; 80 means 20% below. Two lines are drawn: "
                "when claims occur, and when they get paid — useful for anticipating cash-flow "
                "peaks and staffing needs.",
                "Monthly seasonal indices on two bases — origin month (incidence) and payment "
                "month (settlement). Each month's mean amount is indexed to 100 = overall monthly "
                "average. The dotted line at 100 marks a flat (non-seasonal) pattern; deviations "
                "quantify seasonal lift/drag.")
    with card("Payment heatmap (year x month)"):
        st.plotly_chart(charts.payment_heatmap(res.seas.heat), width="stretch")
        explain("A colour grid of payments by year (rows) and month (columns). Darker squares are "
                "heavier payment periods. It makes spikes, quiet stretches, and year-over-year "
                "shifts easy to spot at a glance.",
                "Tile heatmap of total paid claims by payment-year × payment-month. Colour "
                "intensity scales with amount. Reveals calendar-period effects (e.g. processing "
                "backlogs, fee-schedule changes) distinct from the averaged seasonal index above.")
    with card("Paid each month and the underlying trend"):
        st.plotly_chart(ic.paid_trend(insights.paid_by_payment_month(res.df_f)), width="stretch")
        explain("Cash paid out each month (bars) against the average of the previous twelve months "
                "(line). Bars above the line are seasonal highs, below it lows; the line itself "
                "shows whether claim payments are growing over time.",
                "Sum of ClaimAmount by payment month for the selected subcategory, with a trailing "
                "12-month mean (shown from the twelfth month). The mean removes a regular annual "
                "cycle, so what the line still shows is trend.")
    with card("Seasonal index table"):
        table(res.seas.index[["Basis", "Mnth", "MeanAmount", "Index"]], money=["MeanAmount"],
              decimals={"Index": 1})
        explain("The exact numbers behind the seasonality chart — the average amount and the index "
                "for each month, on both the occurrence and payment bases — for anyone who wants "
                "the precise figures.",
                "Tabulated seasonal indices: basis, month, mean amount, and index (100 = average) "
                "for both origin-month and payment-month bases, sorted by basis then calendar "
                "month.")


def executive_summary(ctx: Ctx) -> None:
    res = ctx.results
    note("How to read this page. ",
         "This is a plain-language brief that pulls together every other tab into the handful of "
         "points that matter for a reserving decision. It updates automatically after you press "
         "Run analysis. Figures in the cards below are the recommended numbers; the notes "
         "underneath flag where to be careful.", "info")
    if res is None:
        return empty_state()
    es = exec_summary(res, ctx.ar)
    kpi_cards(es.boxes)
    with card("The reserve and its stress levels"):
        st.plotly_chart(ic.reserve_ladder_bars(insights.reserve_ladder(res, ctx.ar)), width="stretch")
        explain("The recommended reserve (navy) next to the booked figure (red, when it differs), "
                "the chain ladder and the simulated adverse outcomes (light grey). The gap between "
                "the recommended bar and the 1-in-20 or 1-in-200 bars is the cushion to discuss for "
                "risk margin or capital.",
                "Recommended (blended) total IBNR, pure chain ladder, the post-adjustment booked "
                "total when it differs, and the bootstrap median, 75th, 95th and 99.5th percentiles "
                "of total IBNR, sorted ascending.")
    st.markdown("##### Key points for the decision")
    key_points(es.points)
    st.html(f'<div class="small-note">{html.escape(es.footer)}</div>')
