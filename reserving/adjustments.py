"""Actuary adjustments: a judgemental overlay on the model output.

One worksheet row per origin: selection Basis, a % load (AdjPct), a dollar
add-on (AdjAmt), an optional hard Override ultimate, a Comment and the
last-edit timestamp.

    Selected Ultimate = Override                      if Override > 0
                      = Base(Basis) * (1 + AdjPct/100) + AdjAmt   otherwise
"""
from __future__ import annotations

import re
from datetime import datetime

import numpy as np
import pandas as pd

ADJ_BASES = ("Blended", "CL", "BF", "Benktander", "CapeCod", "Expected")
ADJ_COLS = ["Origin", "Latest", "Pct.Dev", "Basis", "Base.Ultimate", "AdjPct", "AdjAmt",
            "Override", "Selected.Ultimate", "Selected.IBNR", "Comment"]
EDITABLE_COLS = ("Basis", "AdjPct", "AdjAmt", "Override", "Comment")
STATE_COLS = ["Origin", "Basis", "AdjPct", "AdjAmt", "Override", "Comment", "Edited"]

Message = tuple[str, str]   # (level, text) with level in info / warning / error


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def method_ultimates(res) -> pd.DataFrame:
    """Per-origin ultimate for every candidate estimator, in triangle order."""
    if res.blend is not None:
        d = res.blend.detail
        return pd.DataFrame({"Origin": d["Origin"], "Latest": d["Latest"], "Pct.Dev": d["Pct.Dev"],
                             "CL": d["CL"], "Expected": d["Expected"], "BF": d["BF"],
                             "Benktander": d["Benktander"], "CapeCod": d["CapeCod"],
                             "Blended": d["Blended"]})
    d = res.reserve_df[res.reserve_df["Origin"] != "Total"]
    return pd.DataFrame({"Origin": d["Origin"], "Latest": d["Latest"], "Pct.Dev": d["Dev.To.Date"],
                         "CL": d["Ultimate"], "Expected": np.nan, "BF": np.nan,
                         "Benktander": np.nan, "CapeCod": np.nan,
                         "Blended": d["Ultimate"]}).reset_index(drop=True)


def default_basis(res) -> str:
    return "Blended" if res.blend is not None else "CL"


def initial_state(origins, basis: str) -> pd.DataFrame:
    n = len(origins)
    return pd.DataFrame({"Origin": list(origins), "Basis": [basis] * n,
                         "AdjPct": np.zeros(n), "AdjAmt": np.zeros(n),
                         "Override": np.full(n, np.nan), "Comment": [""] * n,
                         "Edited": [None] * n}, columns=STATE_COLS)


def has_overlays(state: pd.DataFrame | None) -> bool:
    if state is None or state.empty:
        return False
    return bool(((state["AdjPct"] != 0) | (state["AdjAmt"] != 0)
                 | np.isfinite(state["Override"].astype(float))
                 | state["Comment"].fillna("").astype(str).str.len().gt(0)).any())


def adjusted_reserves(state: pd.DataFrame, mu: pd.DataFrame, default: str) -> pd.DataFrame:
    """The single source of truth for the booked position."""
    df = mu.merge(state, on="Origin", how="left")
    basis = [b if isinstance(b, str) and b in ADJ_BASES else default for b in df["Basis"]]
    adj_pct = df["AdjPct"].fillna(0).to_numpy(dtype=float)
    adj_amt = df["AdjAmt"].fillna(0).to_numpy(dtype=float)
    override = df["Override"].to_numpy(dtype=float)
    comment = df["Comment"].fillna("").astype(str).tolist()

    base_ult = np.empty(len(df))
    for i, b in enumerate(basis):
        v = float(df[b].iloc[i])
        if not np.isfinite(v):
            v = float(df["Blended"].iloc[i])      # fallback chain
        if not np.isfinite(v):
            v = float(df["CL"].iloc[i])
        base_ult[i] = v

    with np.errstate(invalid="ignore"):
        use_override = np.isfinite(override) & (override > 0)
    selected = np.where(use_override, override, base_ult * (1 + adj_pct / 100) + adj_amt)
    latest = df["Latest"].to_numpy(dtype=float)
    cl = df["CL"].to_numpy(dtype=float)
    return pd.DataFrame({
        "Origin": df["Origin"].tolist(),
        "Latest": latest,
        "Pct.Dev": df["Pct.Dev"].to_numpy(dtype=float),
        "Basis": basis,
        "Base.Ultimate": base_ult,
        "AdjPct": adj_pct,
        "AdjAmt": adj_amt,
        "Override": override,
        "Selected.Ultimate": selected,
        "Selected.IBNR": selected - latest,
        "Model.IBNR": base_ult - latest,
        "Judgement": selected - base_ult,
        "CL.IBNR": cl - latest,
        "Comment": comment,
        "Edited": df["Edited"].tolist(),
    })


def _parse_number(val) -> float:
    """R: as.numeric(gsub("[,%$ ]", "", as.character(val))) -> NaN if unparseable."""
    if val is None:
        return np.nan
    if isinstance(val, (int, float, np.integer, np.floating)) and not isinstance(val, bool):
        return float(val)
    txt = re.sub(r"[,%$ ]", "", str(val))
    try:
        return float(txt)
    except ValueError:
        return np.nan


def apply_cell_edit(state: pd.DataFrame, row: int, column: str, value,
                    stamp: str | None = None) -> tuple[pd.DataFrame, list[Message]]:
    """Apply one worksheet edit (row is 0-based) with the R app's validation rules."""
    msgs: list[Message] = []
    if row < 0 or row >= len(state) or column not in EDITABLE_COLS:
        return state, msgs
    st = state.copy()
    if column == "Basis":
        v = "" if value is None else str(value).strip()
        hit = [b for b in ADJ_BASES if b.lower() == v.lower()]
        if not hit:
            msgs.append(("error", f"'{v}' is not a valid basis. Use one of: "
                                  f"{', '.join(ADJ_BASES)}."))
            return state, msgs
        st.loc[row, "Basis"] = hit[0]
    elif column in ("AdjPct", "AdjAmt", "Override"):
        v = _parse_number(value)
        if column == "Override":
            ok = np.isfinite(v) and v > 0
            st.loc[row, "Override"] = v if ok else np.nan
            if value is not None and str(value).strip() != "" and not ok:
                msgs.append(("warning", "Override must be a positive number; cleared instead."))
        else:
            if not np.isfinite(v):
                msgs.append(("error", "Please enter a number."))
                return state, msgs
            if column == "AdjPct" and abs(v) > 100:
                msgs.append(("warning", "That is a very large percentage adjustment - "
                                        "double-check it was intended."))
            st.loc[row, column] = v
    else:  # Comment
        st.loc[row, "Comment"] = "" if value is None else str(value)
    st.loc[row, "Edited"] = stamp or now_stamp()
    return st, msgs


def apply_basis_to_all(state: pd.DataFrame, basis: str,
                       stamp: str | None = None) -> tuple[pd.DataFrame, list[Message]]:
    st = state.copy()
    st["Basis"] = basis
    st["Edited"] = stamp or now_stamp()
    return st, [("info", f"Selection basis set to {basis} for all origins.")]


def adjustment_log(ar: pd.DataFrame) -> pd.DataFrame:
    """Rows with a non-zero judgement or an edit timestamp (the audit trail)."""
    edited = pd.Series([e is not None and not (isinstance(e, float) and np.isnan(e))
                        for e in ar["Edited"]], index=ar.index)
    keep = (ar["Judgement"].abs() > 1e-9) | edited
    return (ar.loc[keep, ["Origin", "Basis", "AdjPct", "AdjAmt", "Override", "Judgement",
                          "Comment", "Edited"]]
              .rename(columns={"Edited": "Last edited"})
              .reset_index(drop=True))
