"""Medical LOB Reserving Tool - Streamlit edition.

Mack Chain Ladder + Bootstrap + credibility-blended estimate, recast
(back-test) and seasonality analysis.  A port of the R Shiny app archive/r_shiny_app/app.R;
tests/test_parity.py checks the numbers against the R app.

    streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import astuple
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Medical LOB Reserving Tool", page_icon=":material/monitoring:",
                   layout="wide")

from reserving.adjustments import default_basis, has_overlays, initial_state, method_ultimates  # noqa: E402
from reserving.data import (ALL_CATEGORIES, DataError, read_claims, read_exposure_table,  # noqa: E402
                            subcat_choices)
from reserving.export import build_workbook, export_filename  # noqa: E402
from reserving.pipeline import AnalysisError, AnalysisSettings, run_analysis  # noqa: E402
from ui import state  # noqa: E402
from ui.components import inject_css  # noqa: E402
from views import TABS  # noqa: E402

SAMPLE_DIR = Path(__file__).parent / "sample_data"
SAMPLE_CLAIMS = SAMPLE_DIR / "aggregated_claims.xlsx"
SAMPLE_EXPOSURE = SAMPLE_DIR / "exposure_premium.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@st.cache_data(show_spinner="Reading claims workbook…", max_entries=8)
def load_claims(data: bytes):
    return read_claims(io.BytesIO(data))


@st.cache_data(show_spinner=False, max_entries=8)
def load_exposure(data: bytes):
    return read_exposure_table(io.BytesIO(data))


def _source(upload, sample_flag: str, sample_path: Path) -> bytes | None:
    if upload is not None:
        return upload.getvalue()
    if st.session_state.get(sample_flag) and sample_path.exists():
        return sample_path.read_bytes()
    return None


def _digest(data: bytes | None) -> str | None:
    return hashlib.md5(data).hexdigest() if data else None


def sidebar_inputs():
    claims = claims_error = exposure = None
    with st.sidebar:
        st.header("Controls")
        up = st.file_uploader("Claims data (.xlsx)", type=["xlsx"], key="data_file")
        if up is None and SAMPLE_CLAIMS.exists():
            st.checkbox("Use the bundled sample claims file", key="use_sample_claims")
        claims_bytes = _source(up, "use_sample_claims", SAMPLE_CLAIMS)
        if claims_bytes:
            try:
                claims = load_claims(claims_bytes)
            except DataError as exc:
                claims_error = str(exc)

        up_e = st.file_uploader("Exposure / earned premium (.xlsx, optional)", type=["xlsx"],
                                key="exposure_file")
        st.caption("Columns: Period, EarnedPremium, Exposure. Drives the exposure-based Cape Cod, "
                   "Bornhuetter-Ferguson and Benktander a-priori.")
        if up_e is None and SAMPLE_EXPOSURE.exists():
            st.checkbox("Use the bundled sample exposure file", key="use_sample_exposure")
        exposure_bytes = _source(up_e, "use_sample_exposure", SAMPLE_EXPOSURE)
        if exposure_bytes:
            try:
                exposure = load_exposure(exposure_bytes)
            except DataError as exc:
                st.warning(f"Exposure file problem: {exc} The file is ignored.")

        if claims is not None:
            choices = subcat_choices(claims)
            if st.session_state.get("subcat") not in choices:
                st.session_state["subcat"] = ALL_CATEGORIES
            st.selectbox("Subcategory", choices, key="subcat")
        st.radio("Triangle granularity", ["month", "quarter"], index=1, horizontal=True,
                 format_func={"month": "Monthly", "quarter": "Quarterly"}.get, key="period")
        st.number_input("Tail factor (Mack)", min_value=1.0, max_value=3.0, value=1.0, step=0.01,
                        format="%.2f", key="tail")

        st.divider()
        st.markdown("**Bootstrap**")
        st.number_input("Simulations", min_value=100, max_value=10000, value=1000, step=100,
                        key="n_sims")
        st.selectbox("Process distribution", ["gamma", "od.pois"], key="boot_dist",
                     format_func={"gamma": "Gamma", "od.pois": "Over-dispersed Poisson"}.get)
        st.divider()
        st.markdown("**Recast / back-test**")
        st.number_input("Diagonals to hold back", min_value=1, max_value=6, value=1, step=1,
                        key="n_holdback")
        st.divider()
        st.markdown("**Blended estimate**")
        st.selectbox("Stabilising prior", ["benktander", "bf", "capecod"], key="blend_prior",
                     format_func={"benktander": "Benktander (iterated BF)",
                                  "bf": "Bornhuetter-Ferguson", "capecod": "Cape Cod"}.get)
        st.slider("Credibility steepness (kappa)", min_value=0.5, max_value=3.0, value=1.5,
                  step=0.1, key="blend_kappa")
        st.checkbox("Seasonally adjust the prior", value=True, key="blend_seasonal")
        st.divider()
        run = st.button("Run analysis", type="primary", icon=":material/play_arrow:",
                        width="stretch", disabled=claims is None)

    ss = st.session_state
    settings = AnalysisSettings(subcat=ss.get("subcat", ALL_CATEGORIES), period=ss.period,
                                tail=float(ss.tail), n_sims=int(ss.n_sims), boot_dist=ss.boot_dist,
                                n_holdback=int(ss.n_holdback), blend_prior=ss.blend_prior,
                                blend_kappa=float(ss.blend_kappa),
                                blend_seasonal=bool(ss.blend_seasonal))
    run_key = (_digest(claims_bytes), _digest(exposure_bytes) if exposure is not None else None,
               *astuple(settings))
    return claims, claims_error, exposure, settings, run_key, run


def run_now(claims, exposure, settings, run_key) -> None:
    with st.spinner("Running analysis…", show_time=True):
        try:
            res = run_analysis(claims, settings, exposure)
        except AnalysisError as exc:
            st.session_state.update(results=None, results_key=None, analysis_error=str(exc))
            return
    had_overlays = has_overlays(st.session_state.adj_state)
    st.session_state.update(results=res, results_key=run_key, analysis_error=None)
    mu = method_ultimates(res)
    state.set_adj_state(initial_state(mu["Origin"], default_basis(res)))
    for level, text in res.messages:
        state.notify(level, text)
    if had_overlays:
        state.notify("warning", "Analysis re-run: the adjustment worksheet was re-initialised and "
                                "previous overlays cleared.")


def main() -> None:
    inject_css()
    state.init()
    claims, claims_error, exposure, settings, run_key, run = sidebar_inputs()
    if run and claims is not None:
        run_now(claims, exposure, settings, run_key)

    res = st.session_state.results
    ar = state.current_adjusted(res)
    with st.sidebar:
        if res is not None:
            if st.session_state.results_key != run_key:
                st.caption(":material/info: Inputs have changed since the last run - press "
                           "**Run analysis** to refresh the results.")
            signoff = state.signoff()
            st.download_button("Download results (.xlsx)",
                               data=lambda: build_workbook(res, ar, signoff),
                               file_name=export_filename(res.sel), mime=XLSX_MIME,
                               icon=":material/download:", width="stretch", on_click="ignore")

    st.markdown("## Medical LOB Reserving Tool")
    st.caption("Mack Chain Ladder · Bootstrap ODP · Credibility-blended estimate · "
               "Recast back-test · Seasonality")
    if st.session_state.analysis_error:
        st.error(st.session_state.analysis_error, icon=":material/error:")

    ctx = state.Ctx(claims=claims, claims_error=claims_error, exposure=exposure, results=res, ar=ar)
    tabs = st.tabs([label for label, _ in TABS], key="main_tab", on_change="rerun")
    for tab, (_, render) in zip(tabs, TABS):
        if tab.open:
            with tab:
                render(ctx)
    state.flush_toasts()


main()
