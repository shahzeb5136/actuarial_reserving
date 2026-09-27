"""Parity tests: the Python app vs the ORIGINAL R Shiny app (archive/r_shiny_app/app.R).

The R side comes from tests/r_reference/make_reference.R, which drives the
real app.R server headlessly with shiny::testServer: it uploads the files,
sets the sidebar inputs, presses Run analysis, edits the adjustments
worksheet, fills in the sign-off and downloads the Excel results.

Per scenario we compare, against R:
  * every sheet and cell of the Excel download (R's own file vs ours)
  * the Executive Summary, Final Summary, guardrail and status texts, word
    for word, as rendered by R
  * value boxes, notifications, worksheet state, Mack internals, heatmap data

Intentional, documented differences:
  1. R's Total row shows Mack S.E. = NA: the app looked the total up as
     tot["Mack.S.E", 1] but summary() names that row "Mack S.E.:".  Python
     shows the value R actually computed (MackChainLadder()$Total.Mack.S.E);
     the test checks it equals R's.
  2. The bootstrap is random and the R app sets no seed, so R's simulated
     IBNR draws are injected into the Python results for these comparisons.
     The simulation engine itself is verified by the test_bootstrap_* tests
     (exact arithmetic with R's residual draws + distributional agreement).
  3. Monthly SubCat A and C have a development lag with no payments at all.
     R's pivot_wider drops that column and shifts later payments into the
     wrong development period.  Python builds the complete lag grid; those
     scenarios are compared with R running the same one-line fix, and the
     *_asis scenarios quantify the R bug.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from parity_helpers import (ATOL, REF_DIR, RTOL, TESTS_DIR, alerts_text, arr, boxes_text,
                            compare_workbooks, exec_text, export_date, html_text, load_ref,
                            read_workbook, replay, scenario_ids)
from reserving.bootstrap import bootstrap_setup, run_bootstrap
from reserving.data import parse_period_cell, read_exposure_table
from reserving.export import build_workbook
from reserving.formatting import fmt_money
from reserving.lowess import lowess
from reserving.summary import (adjustment_value_boxes, data_value_boxes, exec_summary,
                               exposure_status, exposure_value_boxes, final_headline,
                               guardrail_alerts, recast_status)
from reserving.triangles import build_triangles

if not REF_DIR.exists() or not scenario_ids():
    pytest.skip("R reference outputs not generated (run tests/r_reference/make_reference.R)",
                allow_module_level=True)

SCENARIOS = [s for s in scenario_ids() if not s.endswith("_asis")]
ASIS = [s for s in scenario_ids() if s.endswith("_asis")]
REPORT: list[dict] = []


@pytest.fixture(scope="module")
def replays(claims):
    cache = {}

    def get(sid):
        if sid not in cache:
            ref = load_ref(sid)
            assert "error" not in ref, f"R run failed: {ref.get('error')}"
            cache[sid] = (ref, replay(ref, claims))
        return cache[sid]
    return get


@pytest.fixture(scope="module", autouse=True)
def write_report():
    yield
    if not REPORT:
        return
    lines = ["# R vs Python parity report", "",
             "| Scenario | Sheets | Cells compared | Numeric cells | Max relative diff | Result |",
             "|---|---:|---:|---:|---:|---|"]
    for r in REPORT:
        lines.append(f"| {r['scenario']} | {r['sheets']} | {r['cells']:,} | {r['numeric']:,} | "
                     f"{r['max_rel']:.1e} | {r['result']} |")
    (TESTS_DIR / "parity_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _workbook_diffs(ref, rp):
    r_book = read_workbook(REF_DIR / f"{ref['id']}.xlsx")
    py_book = read_workbook(build_workbook(rp.res, rp.ar, ref["signoff"], export_date(ref)))
    total_se = ref["mack"]["total_se"]

    def total_se_rule(a, b):      # difference 1: R shows NA, Python R's computed value
        return a is None and np.isclose(b, total_se, rtol=RTOL)

    def stamp_rule(a, b):         # edit timestamps: compare presence, not the clock
        return (a is None) == (b is None)

    exceptions = {("Reserve Estimates", "Total", "Mack.S.E"): total_se_rule,
                  ("Method Comparison", "Mack Chain Ladder", "Std.Err"): total_se_rule}
    for row in r_book.get("Adjustment Log", [])[1:]:
        exceptions[("Adjustment Log", row[0], "LastEdited")] = stamp_rule
    return r_book, compare_workbooks(r_book, py_book, exceptions)


# ---------------------------------------------------------------------------
# Scenario-level parity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", SCENARIOS)
def test_excel_download_matches_r(sid, replays):
    ref, rp = replays(sid)
    r_book, diffs = _workbook_diffs(ref, rp)
    bad = [f"{d.sheet}: {m}" for d in diffs for m in d.mismatches]
    REPORT.append({"scenario": sid, "sheets": len(r_book), "cells": sum(d.cells for d in diffs),
                   "numeric": sum(d.numeric_cells for d in diffs),
                   "max_rel": max(d.max_rel_diff for d in diffs),
                   "result": "match" if not bad else f"{len(bad)} mismatches"})
    assert not bad, "\n".join(bad[:40])


@pytest.mark.parametrize("sid", SCENARIOS)
def test_summary_texts_match_r(sid, replays, claims):
    ref, rp = replays(sid)
    res, ar, t = rp.res, rp.ar, ref["texts"]
    se_txt = f"Std. error +/- {fmt_money(ref['mack']['total_se'])}"
    r_exec = html_text(t["exec"]).replace("Std. error +/- n/a", se_txt)   # difference 1
    assert exec_text(exec_summary(res, ar)) == r_exec
    assert html_text(boxes_text(final_headline(ar, res.boot))) == html_text(t["final_headline"])
    assert alerts_text(guardrail_alerts(ar, res.boot)) == html_text(t["guardrail"])
    assert alerts_text([exposure_status(rp.exposure, res)]) == html_text(t["exposure_status"])
    assert alerts_text([recast_status(res)]) == html_text(t["recast_status"])


@pytest.mark.parametrize("sid", SCENARIOS)
def test_value_boxes_match_r(sid, replays, claims):
    ref, rp = replays(sid)
    vb = ref["value_boxes"]
    d = data_value_boxes(claims)
    a = adjustment_value_boxes(rp.ar)
    e = exposure_value_boxes(rp.exposure, rp.res)
    assert (d["rows"], d["total"], d["span"]) == (vb["vb_rows"], vb["vb_total"], vb["vb_span"])
    assert (a["base"], a["final"], a["delta"]) == (vb["vb_adj_base"], vb["vb_adj_final"], vb["vb_adj_delta"])
    assert (e["matched"], e["premium"], e["exposure"]) == (
        vb["vb_exp_matched"], vb["vb_exp_premium"], vb["vb_exp_exposure"])
    assert f"Cumulative triangle — {rp.res.sel}" == vb["cum_caption"]


_LEVEL = {"info": "message", "warning": "warning", "error": "error"}


def _norm_note(level, text):
    if text.startswith("Bootstrap skipped:"):     # same event, clearer wording in Python
        return (level, "Bootstrap skipped")
    return (level, text)


@pytest.mark.parametrize("sid", SCENARIOS)
def test_notifications_match_r(sid, replays):
    ref, rp = replays(sid)
    r_notes = [_norm_note(n["type"], n["text"]) for n in ref["notifications"]
               if n["text"] not in ("Running analysis...", "Results downloaded.")]
    py_notes = [_norm_note(_LEVEL[lv], tx) for lv, tx in rp.res.messages + rp.edit_messages]
    assert py_notes == r_notes


@pytest.mark.parametrize("sid", SCENARIOS)
def test_worksheet_state_matches_r(sid, replays):
    ref, rp = replays(sid)
    r_state = pd.DataFrame(ref["adj_state"])
    st = rp.state
    assert list(st["Origin"]) == list(r_state["Origin"])
    assert list(st["Basis"]) == list(r_state["Basis"])
    for col in ("AdjPct", "AdjAmt", "Override"):
        np.testing.assert_allclose(st[col].to_numpy(float), r_state[col].to_numpy(float),
                                   rtol=RTOL, equal_nan=True)
    assert list(st["Comment"].fillna("")) == list(r_state["Comment"].fillna(""))
    assert [e is not None for e in st["Edited"]] == list(r_state["Edited"])


@pytest.mark.parametrize("sid", SCENARIOS)
def test_mack_internals_match_r(sid, replays):
    ref, rp = replays(sid)
    m, mk = ref["mack"], rp.res.mack
    np.testing.assert_allclose(mk.f, m["f"], rtol=RTOL)
    # atol: a link with no development at all has sigma exactly 0 in Python and
    # ~1e-17 (QR round-off) in R's lm()
    np.testing.assert_allclose(mk.f_se, arr(m["f_se"]), rtol=RTOL, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(mk.sigma, arr(m["sigma"]), rtol=RTOL, atol=1e-12, equal_nan=True)
    np.testing.assert_allclose(mk.full_triangle, arr(m["full_triangle"]), rtol=RTOL)
    np.testing.assert_allclose(mk.mack_se[:, -1], arr(m["mack_se_last"]), rtol=RTOL, atol=ATOL)
    assert np.isclose(mk.total_se, m["total_se"], rtol=RTOL)


@pytest.mark.parametrize("sid", SCENARIOS)
def test_seasonality_heatmap_matches_r(sid, replays):
    ref, rp = replays(sid)
    r_heat = pd.DataFrame(ref["heat"])
    py = rp.res.seas.heat
    assert list(py["Yr"]) == list(r_heat["Yr"]) and list(py["Mnth"]) == list(r_heat["Mnth"])
    np.testing.assert_allclose(py["Amount"], r_heat["Amount"], rtol=RTOL)


@pytest.mark.parametrize("sid", ASIS)
def test_r_empty_lag_bug_is_the_only_difference(sid, replays):
    """Unpatched R differs from Python; the patched R run matches exactly."""
    ref, rp = replays(sid)
    _, diffs = _workbook_diffs(ref, rp)
    assert any(d.mismatches for d in diffs), "expected the R lag bug to change results"
    fixed = sid.removesuffix("_asis")
    assert fixed in SCENARIOS
    r_tot = read_workbook(REF_DIR / f"{sid}.xlsx")["Reserve Estimates"][-1]
    ok_tot = read_workbook(REF_DIR / f"{fixed}.xlsx")["Reserve Estimates"][-1]
    REPORT.append({"scenario": f"{sid} (R without fix)", "sheets": len(diffs),
                   "cells": sum(d.cells for d in diffs), "numeric": sum(d.numeric_cells for d in diffs),
                   "max_rel": max(d.max_rel_diff for d in diffs),
                   "result": f"R bug: total IBNR {r_tot[4]:,.0f} vs {ok_tot[4]:,.0f} when fixed"})


# ---------------------------------------------------------------------------
# Bootstrap engine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period", ["quarter", "month"])
def test_bootstrap_setup_matches_r(claims, period):
    b = load_ref("bootstrap_internals")[period]
    setup = bootstrap_setup(build_triangles(claims, period).cum)
    np.testing.assert_allclose(setup.exp_inc, arr(b["exp_inc"]), rtol=RTOL, equal_nan=True)
    # Residuals are O(100-1000); the two corner cells are exactly 0 in R but
    # ~1e-12 in Python (R's colSums accumulates in 80-bit long double).
    np.testing.assert_allclose(setup.adj_residuals, arr(b["adj_resids"]), rtol=RTOL, atol=1e-8,
                               equal_nan=True)
    assert np.isclose(setup.scale_phi, b["phi"], rtol=RTOL)


@pytest.mark.parametrize("period", ["quarter", "month"])
def test_bootstrap_with_r_residual_draws_matches_r(claims, period):
    """Feed R's exact residual resample in: every simulated reserve (before
    process noise) must equal R's ParamDist.ByOrigin."""
    b = load_ref("bootstrap_internals")[period]
    n, R = b["n"], b["R"]
    flat = np.array([np.nan if v is None else v for v in b["resid_sample_colmajor"]], dtype=float)
    samp = flat.reshape((n, n, R), order="F").transpose(2, 0, 1)
    tri = build_triangles(claims, period)
    out = run_bootstrap(tri.cum, tri.origins, resid_sample=samp, keep_param=True, seed=0)
    np.testing.assert_allclose(out.param_by_origin, arr(b["param_by_origin"]), rtol=1e-8, atol=1e-4)


@pytest.mark.parametrize("case", ["quarter_gamma", "quarter_odpois", "month_gamma"])
def test_bootstrap_distribution_matches_r(claims, case):
    """Same predictive distribution as R, to within Monte Carlo error."""
    d = load_ref("bootstrap_distributions")[case]
    r_tot, n_sims = np.array(d["totals"], dtype=float), int(d["R"])
    period, distr = case.split("_")
    tri = build_triangles(claims, period)
    py = run_bootstrap(tri.cum, tri.origins, n_sims=n_sims,
                       process_distr={"gamma": "gamma", "odpois": "od.pois"}[distr],
                       seed=2026).sim_totals
    se_mean = np.sqrt(r_tot.var(ddof=1) / n_sims + py.var(ddof=1) / n_sims)
    assert abs(r_tot.mean() - py.mean()) < 4 * se_mean
    assert abs(r_tot.std(ddof=1) / py.std(ddof=1) - 1) < 4 * np.sqrt(1 / n_sims)
    for p, tol in [(0.05, 0.02), (0.25, 0.015), (0.5, 0.015), (0.75, 0.015), (0.95, 0.02),
                   (0.99, 0.04)]:
        rq, pq = np.quantile(r_tot, p), np.quantile(py, p)
        assert abs(pq / rq - 1) < tol, f"{p:.0%} quantile: R {rq:,.0f} vs Python {pq:,.0f}"
    stats = pytest.importorskip("scipy.stats")
    assert stats.ks_2samp(r_tot, py).pvalue > 0.001


# ---------------------------------------------------------------------------
# Exposure parsing
# ---------------------------------------------------------------------------

def test_period_parsing_matches_r():
    for row in load_ref("exposure_parsing")["periods"]:
        got = parse_period_cell(row["input"])
        assert (got.strftime("%Y-%m-%d") if got is not None else None) == row["parsed"], row["input"]


def test_period_parsing_where_r_misbehaves():
    """Inputs R mangles or rejects (see make_reference.R probe) are handled sensibly."""
    ts = pd.Timestamp
    assert parse_period_cell("Jan-21") == ts("2021-01-01")        # R: year 0021
    assert parse_period_cell("Sept 2022") == ts("2022-09-01")     # R: error, file rejected
    assert parse_period_cell("01/02/2021") == ts("2021-01-02")    # R: year 0001
    assert parse_period_cell("2021") is None                      # R: error, file rejected
    assert parse_period_cell("2021 Q5") is None                   # R: 2021-05-01
    assert parse_period_cell("abc") is None                       # R: error, file rejected


def test_lowess_matches_r():
    """Residual-plot trend lines use a port of R's lowess()."""
    d = load_ref("lowess")
    x, y = np.array(d["x"], dtype=float), np.array(d["y"], dtype=float)
    for key, kwargs in (("fit", {}), ("fit_f03_iter0", {"f": 0.3, "iterations": 0})):
        lx, ly = lowess(x, y, **kwargs)
        np.testing.assert_allclose(lx, d[key]["x"], rtol=RTOL)
        np.testing.assert_allclose(ly, d[key]["y"], rtol=1e-9, atol=1e-12, err_msg=key)


def test_exposure_tables_match_r():
    for t in load_ref("exposure_parsing")["exposure_tables"]:
        py = read_exposure_table(TESTS_DIR / "fixtures" / t["file"])
        r = pd.DataFrame(t["table"])
        assert list(py["Period"]) == list(r["Period"]), t["file"]
        assert list(py["PeriodDate"].dt.strftime("%Y-%m-%d")) == list(r["PeriodDate"]), t["file"]
        for col in ("EarnedPremium", "Exposure"):
            np.testing.assert_allclose(py[col].to_numpy(float), r[col].to_numpy(float),
                                       rtol=RTOL, equal_nan=True, err_msg=t["file"])
