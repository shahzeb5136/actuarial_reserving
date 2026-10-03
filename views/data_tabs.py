"""Tabs 1, 1b, 2, 3, 4: data checks, exposure, triangles, development factors."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from reserving import insights
from reserving.summary import data_value_boxes, exposure_status, exposure_value_boxes
from ui import insight_charts as ic
from ui.components import alert, card, empty_state, explain, table
from ui.state import Ctx


def data_checks(ctx: Ctx) -> None:
    if ctx.claims_error:
        st.error(ctx.claims_error)
    if ctx.claims is None:
        st.info("Upload a claims workbook (.xlsx) in the sidebar - or press **Use the bundled sample "
                "claims file** - to begin. Expected columns: OriginDate, PaymentDate, ClaimAmount, "
                "SubCat (transactional or aggregated rows).", icon=":material/upload_file:")
        return
    df = ctx.claims
    with card("Uploaded data (post-cleaning)"):
        st.dataframe(df, hide_index=True, height=400, width="stretch", placeholder="",
                     column_config={
                         "OriginDate": st.column_config.DateColumn(format="YYYY-MM-DD"),
                         "PaymentDate": st.column_config.DateColumn(format="YYYY-MM-DD"),
                         "ClaimAmount": st.column_config.NumberColumn(format="%,.2f")})
        explain("This is the raw claims data you uploaded, after we removed blank or invalid rows. "
                "The boxes below summarise how many records came through, the total dollars paid, "
                "and the date range covered — a quick sanity check that the right file loaded.",
                "Transactional/aggregated claim records filtered to drop rows with missing "
                "OriginDate, PaymentDate, or ClaimAmount, and any record where PaymentDate < "
                "OriginDate. Dates are coerced to Date, ClaimAmount to numeric, SubCat to factor. "
                "No de-duplication is applied here; identical Origin/Payment/SubCat rows are summed "
                "downstream during triangle construction.")
    vb = data_value_boxes(df)
    c1, c2, c3 = st.columns([1, 1, 1.6])          # the date span needs the room
    c1.metric("Rows", vb["rows"], border=True, icon=":material/table_rows:")
    c2.metric("Total paid", vb["total"], border=True, icon=":material/payments:")
    c3.metric("Origin span", vb["span"], border=True, icon=":material/calendar_month:")

    with card("Claims by origin month — by subcategory"):
        st.plotly_chart(ic.claims_by_subcat(insights.claims_by_origin_month(df)), width="stretch")
        explain("How much claim cost arose in each month, split by subcategory. Use it to spot "
                "gaps, spikes, or a subcategory that suddenly grows or disappears - signs the file "
                "may be incomplete or that the mix of business is shifting.",
                "Sum of ClaimAmount by origin month (OriginDate floored to the month) and SubCat, "
                "over all payments to date in the uploaded file (not filtered by the sidebar). "
                "Recent months look lower partly because their claims are not yet fully paid. More "
                "than seven subcategories fold the smallest into 'Other'.")
    with card("How quickly claims are paid"):
        prof = insights.payment_delay_profile(df)
        delays = prof["Delay"].tolist()
        st.plotly_chart(ic.payment_delay(prof, insights.first_reaching(prof["CumShare"], delays, 0.5),
                                         insights.first_reaching(prof["CumShare"], delays, 0.9)),
                        width="stretch")
        explain("Each bar is the share of all dollars paid a given number of months after the claim "
                "occurred. Most money goes out in the first few months, then it tails off. The "
                "markers show when half, and nine-tenths, of the dollars have been paid.",
                "Dollar-weighted distribution of payment delay (payment month minus origin month, "
                "whole months), pooled over every origin in the file. Recent origins have not had "
                "time for long delays, so the tail is understated here - the Development Factors "
                "tab shows the fully developed pattern implied by the chain ladder.")


def exposure(ctx: Ctx) -> None:
    res, ex = ctx.results, ctx.exposure
    alert(exposure_status(ex, res))
    with card("Exposure / earned premium by period"):
        if res is not None and res.exp_al is not None:
            table(res.exp_al.aligned, money=["EarnedPremium", "Exposure"])
        elif ex is not None:
            table(ex[["Period", "EarnedPremium", "Exposure"]], money=["EarnedPremium", "Exposure"],
                  height=560)
        explain("This is the earned premium and exposure you provided for each period, lined up "
                "against the periods in the claims triangle. Earned premium and exposure tell the "
                "model how much business was actually on risk in each period, so the reserve for "
                "the newest, least-developed periods can be anchored to how much was written rather "
                "than guessed from a few early payments. If a period shows blank, no exposure was "
                "matched to it and the model falls back to its data-only estimate for that period.",
                "Exposure base aligned to origin rows of the cumulative triangle. Period values are "
                "parsed (date, year-month, or year-quarter) and floored to the triangle granularity, "
                "so a monthly exposure table aggregates up to a quarterly triangle. Earned premium "
                "feeds the Bornhuetter-Ferguson / Benktander a-priori; exposure feeds the Cape Cod "
                "(Stanard-Buhlmann) ELR. Both a-priori loss ratios are estimated on a developed "
                "basis: ELR = sum(latest) / sum(base * %developed).")
    vb = exposure_value_boxes(ex, res)
    c1, c2, c3 = st.columns(3)
    c1.metric("Periods matched", vb["matched"], border=True, icon=":material/link:")
    c2.metric("Total earned premium", vb["premium"], border=True, icon=":material/request_quote:")
    c3.metric("Total exposure", vb["exposure"], border=True, icon=":material/stacks:")

    if res is not None and res.exp_al is not None:
        al = res.exp_al.aligned
        labels, prem, expo = al["Origin"], al["EarnedPremium"], al["Exposure"]
    elif ex is not None:
        labels, prem, expo = ex["Period"], ex["EarnedPremium"], ex["Exposure"]
    else:
        labels = None
    if labels is not None:
        with card("Earned premium and exposure by period"):
            st.plotly_chart(ic.premium_and_exposure(labels, prem, expo), width="stretch")
            explain("The business on risk in each period: earned premium on top, exposure below. "
                    "Both should move smoothly; a sudden jump or a missing period usually points to "
                    "a problem in the exposure file rather than a real change in the book.",
                    "Aligned to the triangle's origin periods once the analysis has run (monthly "
                    "exposure is summed to quarters for a quarterly triangle); before that, the "
                    "periods as read from the file. Two panels because the measures are on "
                    "different scales.")

    with card("Fitted a-priori loss ratios (developed basis)"):
        if res is not None and res.blend is not None and res.blend.has_exposure:
            tab = pd.DataFrame({"Basis": ["Earned premium (BF / Benktander)", "Exposure (Cape Cod)"],
                                "A-priori loss ratio": [res.blend.elr_premium, res.blend.elr_capecod]})
            tab = tab[np.isfinite(tab["A-priori loss ratio"])]
            if len(tab):
                table(tab, pct=["A-priori loss ratio"])
            else:
                st.caption("Loss ratios become available once an exposure file is matched.")
        else:
            st.caption("Shown once an exposure file is loaded and the analysis has been run.")
        explain("The implied loss ratio the model has read from your data and premium - roughly, "
                "expected claims as a share of premium. It is what the exposure-based methods use "
                "to project the immature periods. A figure that looks out of line with pricing "
                "expectations is worth questioning before booking the reserve.",
                "Developed-basis (Cape Cod / Stanard-Buhlmann) a-priori loss ratios: the premium "
                "ELR drives BF and Benktander, the exposure ELR drives Cape Cod. Each is sum(latest) "
                "over sum(base x %developed), i.e. claims emerged to date divided by the earned, "
                "developed portion of the exposure base. Shown only when an exposure file is loaded "
                "and the analysis has been run.")

    lr = insights.loss_ratios(res) if res is not None else None
    if lr is not None:
        with card("Loss ratio by origin — paid to date vs estimated ultimate"):
            st.plotly_chart(ic.loss_ratio_by_origin(lr, res.blend.elr_premium), width="stretch")
            explain("Claims as a share of premium for each period: what has been paid so far "
                    "(light) and the estimated final cost (dark). Old periods have the two lines "
                    "together because they are nearly fully paid; for new periods the gap is the "
                    "reserve. The grey line is the loss ratio the model expects.",
                    "Paid-to-date LR = Latest / earned premium; ultimate LR = blended ultimate / "
                    "earned premium, for origins with matched premium. Reference line: the "
                    "developed-basis a-priori ELR (sum latest / sum premium x %developed) behind "
                    "the BF / Benktander prior.")


def cumulative(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    with card(f"Cumulative triangle — {res.sel}"):
        table(res.cum_df, money=[c for c in res.cum_df.columns if c != "Origin"])
        explain("A 'triangle' tracks how claim payments for each starting period (rows) pile up as "
                "time passes (columns). Reading left to right shows how much had been paid so far. "
                "The blank upper-right corner is simply the future — payments that haven't happened "
                "yet. This shape is the raw material for estimating money still owed.",
                "Cumulative run-off triangle. Rows are origin periods (monthly or quarterly), "
                "columns are development lags. Cell (i, j) is the running total of incremental "
                "claims for origin i through development period j. Only cells on or below the "
                "leading diagonal are populated; upper-right cells are NA (unobserved future "
                "development).")
    with card("Payment pattern by origin — paid as % of ultimate"):
        pattern = insights.payment_pattern(res.triangles.cum, res.mack.ultimate)
        st.plotly_chart(ic.payment_pattern_lines(res.triangles.origins, pattern), width="stretch")
        explain("Each line follows one starting period and shows what share of its estimated final "
                "cost had been paid at each age. If the newer (darker) lines sit above or below the "
                "older ones at the same age, claims are being paid faster or slower than they used "
                "to be - a warning that the historical pattern may not hold.",
                "Cumulative paid / chain-ladder ultimate (Mack, including any tail) by development "
                "period, one line per origin, shaded light (oldest) to dark (newest). The newest "
                "origin's latest point equals its % developed.")


def incremental(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    with card(f"Incremental triangle — {res.sel}"):
        table(res.incr_df, money=[c for c in res.incr_df.columns if c != "Origin"])
        explain("The same triangle, but showing the new dollars paid in each period rather than the "
                "running total. This makes it easy to see the payment 'rhythm' — typically a lot "
                "early on, tapering off as older claims close out.",
                "Incremental claims matrix: cell (i, j) is the standalone (non-cumulative) paid "
                "amount for origin i at development lag j, obtained by summing ClaimAmount per "
                "origin/dev cell. Cumulating across columns reproduces the cumulative triangle. NA "
                "cells denote unobserved future development.")
    with card("Incremental payments heatmap"):
        tri = res.triangles
        z = insights.observed_incremental(tri.incremental.to_numpy(), tri.cum)
        st.plotly_chart(ic.incremental_heatmap(tri.origins, z), width="stretch")
        explain("The incremental triangle as a colour grid: darker squares are bigger payments. "
                "Payments are heaviest in the first few columns and fade to the right. A dark streak "
                "running diagonally points to something that happened in one calendar period, such "
                "as a backlog being cleared.",
                "Incremental paid by origin (rows, oldest at the top) and development period "
                "(columns) on the observed triangle; blank cells are the unobserved future. "
                "Diagonals are calendar periods, so diagonal bands indicate calendar-period effects, "
                "which the chain ladder assumes away.")


def dev_factors(ctx: Ctx) -> None:
    res = ctx.results
    if res is None:
        return empty_state()
    with card("Selected age-to-age (link) factors"):
        table(res.dev_df, decimals={"Factor": 4})
        explain("These multipliers describe how claims typically grow from one period to the next. "
                "A factor of 1.30 means claims tend to grow 30% from that age to the next. Factors "
                "close to 1.00 mean claims have largely stopped developing. They are the engine that "
                "projects today's partial claims out to their eventual final cost.",
                "Volume-weighted age-to-age (link) ratios f_k selected by the Mack Chain Ladder, "
                "where f_k is the ratio of column k+1 to column k cumulative totals over origins "
                "with both observed. Multiplying the latest diagonal by the running product of "
                "remaining factors (plus any tail factor) yields projected ultimates.")
    with card("Expected payment pattern — share of ultimate paid by development period"):
        pat = insights.development_pattern(res.mack.f)
        periods = pat["Period"].tolist()
        st.plotly_chart(ic.development_pattern_chart(
            pat, insights.first_reaching(pat["Cumulative"], periods, 0.5),
            insights.first_reaching(pat["Cumulative"], periods, 0.9)), width="stretch")
        explain("What the development factors imply for a typical period: the bars show how much of "
                "the final cost is paid in each period, the line how much has been paid by the end "
                "of it. The labels mark when half, and nine-tenths, of the eventual cost is usually "
                "paid.",
                "From the selected link ratios: share paid by period k = 1 / (product of the "
                "remaining link ratios x tail); the increment is its first difference. With a tail "
                "factor above 1 the curve stops short of 100% at the last period and an 'Ult' point "
                "closes the gap.")
    with card("Individual link ratios vs the selected factor"):
        ratios, deviation = insights.link_ratio_deviation(res.triangles.cum, res.mack.f)
        st.plotly_chart(ic.link_ratio_heatmap(res.triangles.origins, ratios, deviation, res.mack.f),
                        width="stretch")
        explain("Every period's own growth at each step, compared with the factor the model uses. "
                "Red means that period grew faster than the norm, blue slower. Scattered colour is "
                "normal noise; a block of one colour in recent rows or in one column means the "
                "pattern is shifting and the selected factors deserve a closer look.",
                "Individual age-to-age factors C[i,k+1] / C[i,k] divided by the selected "
                "(volume-weighted) factor, minus 1. Colour is capped at the 95th percentile of the "
                "absolute deviations so a few outliers do not wash out the rest; hover for the exact "
                "factor. Early steps naturally vary more because they carry more development.")
