"""Engine behaviour that the R parity tests can't cover (cases where R errors
or silently misplaces data)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from reserving.adjustments import apply_cell_edit, initial_state
from reserving.bootstrap import BootstrapError, run_bootstrap
from reserving.export import export_filename
from reserving.mack import mack_chain_ladder, mack_table
from reserving.pipeline import AnalysisSettings, run_analysis
from reserving.triangles import build_triangles


def _claims(rows):
    return pd.DataFrame(rows, columns=["OriginDate", "PaymentDate", "SubCat", "ClaimAmount"]).assign(
        OriginDate=lambda d: pd.to_datetime(d["OriginDate"]),
        PaymentDate=lambda d: pd.to_datetime(d["PaymentDate"]))


def test_lag_with_no_payments_keeps_its_column():
    # Nobody is paid at lag 2; the lag-3 payment must stay in development period 3.
    df = _claims([("2021-01-15", "2021-01-20", "A", 100), ("2021-01-15", "2021-03-10", "A", 50),
                  ("2021-02-15", "2021-02-20", "A", 80), ("2021-03-15", "2021-03-20", "A", 90)])
    tri = build_triangles(df, "month")
    assert list(tri.incremental.columns) == ["Dev_1", "Dev_2", "Dev_3"]
    assert np.isnan(tri.incremental.iloc[0, 1]) and tri.incremental.iloc[0, 2] == 50
    np.testing.assert_array_equal(tri.cum[0], [100, 100, 150])


def test_origin_period_without_claims_gets_a_row():
    df = _claims([("2021-01-15", "2021-01-20", "A", 100), ("2021-01-15", "2021-02-10", "A", 50),
                  ("2021-01-15", "2021-03-10", "A", 10), ("2021-03-15", "2021-03-20", "A", 90)])
    tri = build_triangles(df, "month")
    assert tri.origins == ["2021-01-01", "2021-02-01", "2021-03-01"]
    np.testing.assert_array_equal(tri.cum[1], [0, 0, np.nan])
    mack = mack_chain_ladder(tri.cum, tri.origins)          # R's lm() would fail on the zero row
    assert "2021-02-01" not in set(mack_table(mack)["Origin"])  # zero-latest rows dropped, like R


def test_bootstrap_needs_square_triangle():
    cum = np.array([[1, 2, 3], [1, 2, np.nan], [1, np.nan, np.nan], [1, np.nan, np.nan]], float)
    with pytest.raises(BootstrapError, match="square"):
        run_bootstrap(cum, list("abcd"), n_sims=10)


def test_bootstrap_is_reproducible_with_a_seed(claims):
    tri = build_triangles(claims, "quarter")
    a = run_bootstrap(tri.cum, tri.origins, n_sims=200, seed=7).sim_totals
    b = run_bootstrap(tri.cum, tri.origins, n_sims=200, seed=7).sim_totals
    np.testing.assert_array_equal(a, b)


def test_small_triangle_where_r_mack_errors_still_runs():
    # 3x3: R's est.sigma = "Mack" indexes sigma[0] and stops; we fall back.
    cum = np.array([[100, 150, 160], [110, 170, np.nan], [120, np.nan, np.nan]], float)
    mack = mack_chain_ladder(cum, ["a", "b", "c"])
    assert np.all(np.isfinite(mack.mack_se[:, -1]))


def test_worksheet_validation_rules():
    st = initial_state(["2021", "2022"], "Blended")
    st2, msgs = apply_cell_edit(st, 0, "Basis", "benktander")
    assert st2.loc[0, "Basis"] == "Benktander" and msgs == []
    st3, msgs = apply_cell_edit(st2, 0, "Basis", "Foo")
    assert st3 is st2 and msgs[0][0] == "error"
    st4, msgs = apply_cell_edit(st3, 1, "Override", -5)
    assert np.isnan(st4.loc[1, "Override"]) and msgs[0][0] == "warning"
    st5, msgs = apply_cell_edit(st4, 1, "AdjPct", "150%")
    assert st5.loc[1, "AdjPct"] == 150 and msgs[0][0] == "warning"
    st6, msgs = apply_cell_edit(st5, 1, "AdjAmt", None)
    assert st6 is st5 and msgs == [("error", "Please enter a number.")]


def test_analysis_rejects_unknown_subcategory(claims):
    from reserving.pipeline import AnalysisError
    with pytest.raises(AnalysisError, match="No data for subcategory"):
        run_analysis(claims, AnalysisSettings(subcat="Z", n_sims=100, seed=1))


def _booked(claims, period, edits=()):
    from reserving.adjustments import adjusted_reserves, default_basis, method_ultimates
    res = run_analysis(claims, AnalysisSettings(period=period, n_sims=100, seed=1))
    mu = method_ultimates(res)
    state = initial_state(mu["Origin"], default_basis(res))
    for row, col, value in edits:
        state, _ = apply_cell_edit(state, row, col, value)
    return adjusted_reserves(state, mu, default_basis(res))


def test_ultimate_split_reconciles_to_booked_position(claims):
    from reserving.summary import ultimate_split, with_total_row
    ar = _booked(claims, "quarter", edits=[(11, "AdjPct", 10), (10, "Override", 1.0)])
    split = ultimate_split(ar)
    np.testing.assert_allclose(split["Paid"] + split["IBNR"], split["Ultimate"])
    assert split["IBNR"].iloc[10] < 0                      # override below paid -> negative IBNR
    years = with_total_row(ultimate_split(ar, by="year"))
    assert list(years["Origin"]) == ["2021", "2022", "2023", "Total"]
    total = years.iloc[-1]
    assert np.isclose(total["IBNR"], ar["Selected.IBNR"].sum())
    assert np.isclose(total["Ultimate"], ar["Selected.Ultimate"].sum())
    assert np.isclose(total["IBNR.Share"], ar["Selected.IBNR"].sum() / ar["Selected.Ultimate"].sum())
    assert np.isclose(years["Paid"].iloc[:3].sum(), total["Paid"])


def test_paid_by_origin_year_is_granularity_independent(claims):
    from reserving.summary import ultimate_split
    q = ultimate_split(_booked(claims, "quarter"), by="year")
    m = ultimate_split(_booked(claims, "month"), by="year")
    np.testing.assert_allclose(q["Paid"], m["Paid"])      # all payments to date, either way
    assert np.isclose(q["Paid"].sum(), claims["ClaimAmount"].sum())


def test_export_filename():
    assert export_filename("All Categories", date(2026, 9, 27)) == \
        "Reserving_Results_All_Categories_2026-09-27.xlsx"
