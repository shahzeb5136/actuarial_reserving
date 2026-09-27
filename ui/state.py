"""Session-state helpers: analysis results, the adjustments worksheet,
persistent inputs and queued toast notifications."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from reserving.adjustments import adjusted_reserves, default_basis, method_ultimates

_TOAST_ICONS = {"info": ":material/info:", "warning": ":material/warning:",
                "error": ":material/error:", "success": ":material/check_circle:"}


@dataclass
class Ctx:
    """Everything the tab views need for one script run."""
    claims: pd.DataFrame | None
    claims_error: str | None
    exposure: pd.DataFrame | None
    results: object | None          # reserving.pipeline.AnalysisResults
    ar: pd.DataFrame | None         # adjusted reserves (the booked position)


def init() -> None:
    ss = st.session_state
    ss.setdefault("results", None)
    ss.setdefault("results_key", None)
    ss.setdefault("analysis_error", None)
    ss.setdefault("adj_state", None)
    ss.setdefault("adj_editor_version", 0)
    ss.setdefault("toasts", [])
    for k in ("signoff_name", "signoff_role", "signoff_notes"):
        ss.setdefault(k, "")


def notify(level: str, text: str) -> None:
    st.session_state.toasts.append((level, text))


def flush_toasts() -> None:
    while st.session_state.toasts:
        level, text = st.session_state.toasts.pop(0)
        st.toast(text, icon=_TOAST_ICONS.get(level), duration="long" if level != "info" else "short")


def set_adj_state(df: pd.DataFrame) -> None:
    """Replace the worksheet state and remount the editor so it shows it cleanly."""
    st.session_state.adj_state = df
    st.session_state.adj_editor_version += 1


def current_adjusted(results) -> pd.DataFrame | None:
    if results is None or st.session_state.adj_state is None:
        return None
    return adjusted_reserves(st.session_state.adj_state, method_ultimates(results),
                             default_basis(results))


def persistent_text(label: str, store: str, area: bool = False, **kwargs) -> None:
    """A text input whose value survives the widget not being drawn (hidden tab)."""
    wkey = f"w_{store}"
    if wkey not in st.session_state:
        st.session_state[wkey] = st.session_state[store]

    def _sync():
        st.session_state[store] = st.session_state[wkey]

    (st.text_area if area else st.text_input)(label, key=wkey, on_change=_sync, **kwargs)


def signoff() -> dict:
    ss = st.session_state
    return {"name": ss.signoff_name, "role": ss.signoff_role, "notes": ss.signoff_notes}
