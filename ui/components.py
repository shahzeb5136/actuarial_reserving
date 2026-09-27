"""Reusable Streamlit building blocks: explainer blocks, alerts, KPI cards,
formatted tables."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import streamlit as st

from reserving.summary import Alert, Box, Point

NAVY = "#2C3E50"

CSS = """
<style>
.explain {margin: .55rem 0 .25rem 0; font-size: .92rem; line-height: 1.45;}
.explain .exec {background: rgba(52,152,219,.10); border-left: 4px solid #2C3E50;
  border-radius: 4px; padding: .5rem .7rem; margin-bottom: .3rem;}
.explain .tech {background: rgba(127,140,141,.10); border-left: 4px solid #95A5A6;
  border-radius: 4px; padding: .5rem .7rem; font-size: .9em;}
.explain .lbl {font-weight: 600;}
.explain .exec .lbl {color: #2C3E50;}
.explain .tech .lbl {color: #566573;}
.kpi-row {display: flex; gap: 14px; flex-wrap: wrap; margin: .2rem 0 1.1rem 0;}
.kpi {flex: 1; min-width: 200px; background: rgba(255,255,255,.04);
  border: 1px solid rgba(49,51,63,.12); border-top: 4px solid var(--accent);
  border-radius: 6px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06);}
.kpi .t {font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; opacity: .65;}
.kpi .v {font-size: 1.6rem; font-weight: 700; color: var(--accent); line-height: 1.3;}
.kpi .s {font-size: .85rem; opacity: .8; margin-top: 2px;}
.point {border-left: 4px solid var(--accent); background: rgba(127,140,141,.08);
  padding: 10px 14px; margin-bottom: 10px; border-radius: 4px;}
.point .h {font-weight: 600; margin-bottom: 2px;}
.small-note {font-size: .8rem; opacity: .6; margin-top: 12px;}
</style>
"""


def inject_css() -> None:
    st.html(CSS)


def card(title: str):
    """Bordered container with a bold header (the R app's bslib card)."""
    box = st.container(border=True)
    box.markdown(f"**{title}**")
    return box


def explain(exec_text: str, technical: str) -> None:
    """Two-part explainer under each plot / table (as in the R app)."""
    st.html(f'<div class="explain"><div class="exec"><span class="lbl">For an executive: </span>'
            f'{html.escape(exec_text)}</div><div class="tech"><span class="lbl">Technical note: '
            f'</span>{html.escape(technical)}</div></div>')


def _md(text: str) -> str:
    return text.replace("$", r"\$")


def alert(a: Alert | None) -> None:
    if a is None:
        return
    body = (f"**{_md(a.title.strip())}** " if a.title.strip() else "") + _md(a.body)
    {"danger": st.error, "warning": st.warning, "success": st.success,
     "info": st.info}.get(a.kind, st.info)(body)


def note(title: str, body: str, kind: str = "info") -> None:
    alert(Alert(kind, title, body))


def kpi_cards(boxes: list[Box]) -> None:
    cards = "".join(
        f'<div class="kpi" style="--accent:{b.accent}"><div class="t">{html.escape(b.title)}</div>'
        f'<div class="v">{html.escape(b.value)}</div><div class="s">{html.escape(b.sub)}</div></div>'
        for b in boxes)
    st.html(f'<div class="kpi-row">{cards}</div>')


def key_points(points: list[Point]) -> None:
    st.html("".join(f'<div class="point" style="--accent:{p.accent}"><div class="h">'
                    f'{html.escape(p.head)}</div><div>{p.body}</div></div>' for p in points))


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def table(df: pd.DataFrame, money=(), pct=(), decimals: dict | None = None,
          height="content", color_rules: dict | None = None, key: str | None = None) -> None:
    """Read-only table with R-style number formatting.

    money:   columns shown rounded with thousands separators (formatRound 0)
    pct:     columns shown as percentages with 1 decimal (formatPercentage 1)
    decimals: {column: digits} for other fixed-decimal columns
    color_rules: {column: callable(value) -> css} for per-cell text styling
    """
    decimals = decimals or {}
    fmt = {}
    for c in df.columns:
        if c in money:
            fmt[c] = lambda v: "" if _na(v) else f"{v:,.0f}"
        elif c in pct:
            fmt[c] = lambda v: "" if _na(v) else f"{100 * v:.1f}%"
        elif c in decimals:
            fmt[c] = (lambda d: lambda v: "" if _na(v) else f"{v:,.{d}f}")(decimals[c])
    styler = df.style.format(fmt, na_rep="")
    for col, rule in (color_rules or {}).items():
        if col in df.columns:
            styler = styler.map(rule, subset=[col])
    right = [c for c in df.columns if c in fmt]
    if right:
        styler = styler.set_properties(subset=right, **{"text-align": "right"})
    st.dataframe(styler, hide_index=True, height=height, width="stretch", key=key, placeholder="")


def _na(v) -> bool:
    try:
        return v is None or (isinstance(v, float) and np.isnan(v))
    except TypeError:
        return False


def empty_state(message: str = "Press **Run analysis** in the sidebar to populate this tab.") -> None:
    st.info(message, icon=":material/play_circle:")
