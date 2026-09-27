"""Tabs 9-10: actuary adjustments worksheet and the post-adjustment final summary."""
from __future__ import annotations

import numpy as np
import streamlit as st

from reserving import insights
from reserving.adjustments import (ADJ_BASES, ADJ_COLS, adjustment_log, apply_basis_to_all,
                                   apply_cell_edit, default_basis, initial_state, method_ultimates)
from reserving.summary import (adjustment_value_boxes, booked_percentile, final_headline,
                               guardrail_alerts, ultimate_split, waterfall_steps, with_total_row)
from ui import charts
from ui import insight_charts as ic
from ui.components import alert, card, empty_state, explain, kpi_cards, note, table
from ui.state import Ctx, notify, persistent_text, set_adj_state

LOCKED = ["Origin", "Latest", "Pct.Dev", "Base.Ultimate", "Selected.Ultimate", "Selected.IBNR"]
_num = st.column_config.NumberColumn


def _on_edit(key: str) -> None:
    """Apply the worksheet edits with the R app's validation rules."""
    edits = st.session_state[key].get("edited_rows", {})
    state = st.session_state.adj_state
    for row, changes in sorted(edits.items(), key=lambda kv: int(kv[0])):
        for col, value in changes.items():
            state, msgs = apply_cell_edit(state, int(row), col, value)
            for level, text in msgs:
                notify(level, text)
    set_adj_state(state)


def _apply_basis_all() -> None:
    state, msgs = apply_basis_to_all(st.session_state.adj_state, st.session_state.adj_basis_all)
    set_adj_state(state)
    for m in msgs:
        notify(*m)


def _reset(results) -> None:
    mu = method_ultimates(results)
    set_adj_state(initial_state(mu["Origin"], default_basis(results)))
    notify("info", "All adjustments reset to the model position.")


def _ibnr_colour(v) -> str:          # R: styleInterval(0, c("#B03A2E", "#2C3E50"))
    return f"font-weight: bold; color: {'#B03A2E' if v <= 0 else '#2C3E50'}"


def _judgement_colour(v) -> str:     # R: styleInterval(c(-1e-9, 1e-9), c(red, grey, green))
    colour = "#B03A2E" if v <= -1e-9 else ("#7F8C8D" if v <= 1e-9 else "#16A085")
    return f"font-weight: bold; color: {colour}"


_SPLIT_COLS = {"Paid": "Paid to date", "IBNR": "IBNR (booked)", "Ultimate": "Ultimate (booked)",
               "IBNR.Share": "IBNR share"}


def _ultimate_split_card(res, ar) -> None:
    """Sense check: how much of each origin's booked ultimate is paid vs still IBNR."""
    period = res.settings.period
    split = ultimate_split(ar)
    with card("Booked ultimate by origin — paid to date vs IBNR"):
        st.plotly_chart(charts.ultimate_split(split, period), width="stretch")
        st.markdown("**By origin year**")
        years = with_total_row(ultimate_split(ar, by="year"))
        table(years.rename(columns={"Origin": "Origin year", **_SPLIT_COLS}),
              money=list(_SPLIT_COLS.values())[:3], pct=["IBNR share"])
        with st.expander(f"Numbers by origin {period}"):
            table(with_total_row(split).rename(columns=_SPLIT_COLS),
                  money=list(_SPLIT_COLS.values())[:3], pct=["IBNR share"])
        explain("Each bar is the final cost being booked for one starting period, split into what "
                "has already been paid (light) and what is still to be paid - the IBNR reserve "
                "(dark). The lower panel shows the share not yet paid: near 0% for old periods, "
                "rising for the newest. A period whose bar or share jumps out from its neighbours "
                "is worth a second look before sign-off. It updates as you edit the worksheet.",
                "Booked ultimate = latest paid + selected IBNR, after the judgemental overlay "
                "(Selected IBNR = Selected Ultimate - Latest). Lower panel: IBNR / booked "
                "ultimate per origin; a negative IBNR (selected ultimate below paid) plots below "
                "zero. The year table sums origin periods by origin year; each Total share is "
                "total IBNR over total booked ultimate.")


def adjustments(ctx: Ctx) -> None:
    res, ar = ctx.results, ctx.ar
    if res is None or ar is None:
        return empty_state()
    note("Why this tab. ",
         "No reserving model captures everything an actuary knows - pending large claims, benefit "
         "changes, processing backlogs, management actions. This worksheet lets you overlay that "
         "judgement on the model output, per origin period, with an auditable rationale. "
         "Double-click a white cell to edit it: choose the selection Basis (CL, BF, Benktander, "
         "CapeCod, Blended or Expected), apply a percentage load (Adj %), a dollar add-on (Adj "
         "Amount), or type a hard Override ultimate, and record your reasoning in Comment. "
         "Adjustments flow straight through to the Final Summary tab and the Excel export.", "info")

    with card("Bulk actions"):
        c1, c2, c3 = st.columns([6, 3, 3], vertical_alignment="bottom")
        c1.selectbox("Selection basis for all origins", ADJ_BASES, key="adj_basis_all")
        c2.button("Apply basis to all", icon=":material/layers:", width="stretch",
                  on_click=_apply_basis_all)
        c3.button("Reset all adjustments", icon=":material/undo:", width="stretch",
                  on_click=_reset, args=(res,))

    with card("Selection & judgemental overlay worksheet (double-click a cell to edit)"):
        key = f"adj_editor_{st.session_state.adj_editor_version}"
        view = ar[ADJ_COLS].copy()
        view["Pct.Dev"] = view["Pct.Dev"] * 100
        st.data_editor(
            view.style.map(_ibnr_colour, subset=["Selected.IBNR"]),
            key=key, hide_index=True, width="stretch", height="content", disabled=LOCKED,
            on_change=_on_edit, args=(key,), placeholder="",
            column_config={
                "Latest": _num(format="%,.0f"),
                "Pct.Dev": _num("Pct.Dev", format="%.1f%%"),
                "Basis": st.column_config.SelectboxColumn(options=list(ADJ_BASES), required=True),
                "Base.Ultimate": _num(format="%,.0f"),
                "AdjPct": _num("AdjPct", format="%.1f", help="Percentage load on the base ultimate"),
                "AdjAmt": _num("AdjAmt", format="%,.0f", help="Dollar add-on to the base ultimate"),
                "Override": _num("Override", format="%,.0f",
                                   help="Hard override of the ultimate (wins over AdjPct / AdjAmt)"),
                "Selected.Ultimate": _num(format="%,.0f"),
                "Selected.IBNR": _num(format="%,.0f"),
                "Comment": st.column_config.TextColumn(width="large"),
            })
        explain("Each row is one origin period. 'Base Ultimate' is what the chosen method says; "
                "'Selected Ultimate' is that figure after your adjustments. Edit Basis, Adj %, Adj "
                "Amount, Override or Comment by double-clicking. A hard Override wins over the "
                "percentage and dollar adjustments. Everything you type is kept in the audit log "
                "with a timestamp.",
                "Selected Ultimate = Override if supplied, otherwise Base(Basis) * (1 + AdjPct/100) "
                "+ AdjAmt. Basis draws from the estimator set computed on the Blended tab (CL, "
                "Expected, BF, Benktander, Cape Cod, Blended). Selected IBNR = Selected Ultimate - "
                "Latest. Edits re-run reactively; re-running the analysis (new data / settings) "
                "re-initialises the worksheet since prior overlays no longer refer to the same "
                "model.")

    vb = adjustment_value_boxes(ar)
    c1, c2, c3 = st.columns(3)
    c1.metric("Model IBNR (pre-adjustment)", vb["base"], border=True, icon=":material/balance:")
    c2.metric("Booked IBNR (after adjustment)", vb["final"], border=True, icon=":material/edit_note:")
    c3.metric("Judgemental overlay", vb["delta"], border=True, icon=":material/swap_vert:")
    for a in guardrail_alerts(ar, res.boot):
        alert(a)

    _ultimate_split_card(res, ar)
    with card("Model vs booked IBNR by origin"):
        st.plotly_chart(charts.adjustment_compare(ar), width="stretch")
        explain("A live before/after of the reserve per period: the model's figure next to your "
                "adjusted figure. Bars that differ are the periods you have overridden - a quick "
                "visual check that the adjustments landed where you intended and nothing was "
                "fat-fingered.",
                "Grouped bars of IBNR under the selected basis before and after the judgemental "
                "overlay, by origin. Updates reactively with every cell edit; origins with a hard "
                "Override are marked.")
    with card("Judgement by origin — increases and releases"):
        if (ar["Judgement"].abs() > 1e-9).any():
            st.plotly_chart(ic.judgement_bars(ar), width="stretch")
        else:
            st.caption("No adjustments yet - edit the worksheet above and they will appear here.")
        explain("Every adjustment you have made, period by period: teal bars add reserve, red bars "
                "release it. It is the quickest way to confirm your edits went where you meant them "
                "to, and in the direction you meant.",
                "Judgement = selected ultimate - model ultimate under the chosen basis, by origin. "
                "The same figures drive the reserve walk and the audit log on the Final Summary tab.")


def final_summary(ctx: Ctx) -> None:
    res, ar = ctx.results, ctx.ar
    if res is None or ar is None:
        return empty_state()
    note("How to read this page. ",
         "This is the post-adjustment brief: the reserve you are actually booking after actuarial "
         "judgement, how it differs from the pure model answer, where it sits in the simulated "
         "range of outcomes, and the audit trail of every change. It updates live as you edit the "
         "Adjustments tab. Complete the sign-off block before exporting - it is written into the "
         "Excel download.", "info")
    kpi_cards(final_headline(ar, res.boot))

    _ultimate_split_card(res, ar)
    with card("Reserve walk - model estimate to booked figure"):
        steps, sub = waterfall_steps(ar)
        st.plotly_chart(charts.reserve_walk(steps, sub), width="stretch")
        explain("A bridge from the model's reserve to the booked reserve. Each middle bar is one "
                "period's judgemental adjustment - up-bars add reserve, down-bars release it - so "
                "anyone reviewing can see exactly which decisions moved the bottom line and by how "
                "much.",
                "Waterfall of total IBNR: model total under the selected bases, one increment per "
                "origin with a non-zero judgement (largest eight shown; the remainder grouped as "
                "'Other'), closing at the booked total. Latest paid is unchanged by adjustments, so "
                "ultimate deltas equal IBNR deltas.")
    with card("IBNR by origin - Chain Ladder vs model selection vs booked"):
        st.plotly_chart(charts.final_ibnr(ar), width="stretch")
        explain("Three views of the reserve for each period: the raw Chain Ladder answer, the "
                "model's blended/selected answer, and the final booked figure after judgement. It "
                "shows at a glance how far the booked number has moved from the mechanical "
                "estimates, and where.",
                "Grouped bars of per-origin IBNR: pure CL (Ultimate_CL - Latest), the "
                "pre-adjustment selected basis, and the post-adjustment booked figure. Differences "
                "between the last two are the judgemental overlay; differences between the first "
                "two are the model blend.")
    with card("Booked reserve against the simulated distribution"):
        if res.boot is None:
            st.caption("Bootstrap distribution unavailable for this run.")
        else:
            st.plotly_chart(charts.booked_vs_distribution(
                res.boot.sim_totals, ar["Model.IBNR"].sum(), ar["Selected.IBNR"].sum(),
                booked_percentile(ar, res.boot)), width="stretch")
        explain("Where the booked reserve lands in the range of simulated outcomes. A booked "
                "figure near the middle is a best-estimate stance; one out in the right tail is "
                "deliberately prudent; one in the left tail is optimistic and worth a second look "
                "before sign-off.",
                "Bootstrap ODP predictive distribution of total IBNR with verticals at the "
                "pre-adjustment model total (dashed) and the booked total (solid). The subtitle "
                "reports the booked figure's empirical percentile within the simulated "
                "distribution - an implicit confidence-level reading of the judgemental margin.")
    with card("Booked reserve vs every method and the simulated range"):
        band = None
        if res.boot is not None:
            p5, p50, p95 = np.quantile(res.boot.sim_totals, [0.05, 0.5, 0.95])
            band = (p5, p50, p95)
        st.plotly_chart(ic.football_field(insights.method_totals(res), band,
                                          float(ar["Selected.IBNR"].sum())), width="stretch")
        explain("Where the booked reserve (red line) sits against every method the model ran and "
                "the range of simulated outcomes. Inside the shaded band and close to the cluster of "
                "methods is a comfortable position; outside either deserves an explanation in the "
                "sign-off.",
                "Total IBNR of each estimator from the Blended Estimate tab (the recommended one in "
                "navy), the bootstrap 5th-95th percentile interval with its median tick, and the "
                "post-adjustment booked total as a vertical line.")
    with card("Final selection by origin"):
        table(ar[["Origin", "Latest", "Pct.Dev", "Basis", "Base.Ultimate", "AdjPct", "AdjAmt",
                  "Override", "Selected.Ultimate", "Selected.IBNR", "Judgement", "Comment"]],
              money=["Latest", "Base.Ultimate", "AdjAmt", "Override", "Selected.Ultimate",
                     "Selected.IBNR", "Judgement"],
              pct=["Pct.Dev"], decimals={"AdjPct": 1}, color_rules={"Judgement": _judgement_colour})
        explain("The definitive per-period table: what has been paid, what the model said, what "
                "you selected, and the resulting reserve to book - with the size of the judgement "
                "called out in its own column.",
                "Per-origin final selection: Latest, %developed, selection basis, base ultimate, "
                "adjustment terms, selected ultimate, selected IBNR, and Judgement = Selected - "
                "Base ultimate. This table is exported verbatim to the 'Final Selection' sheet of "
                "the Excel download.")
    with card("Adjustment audit log"):
        log = adjustment_log(ar)
        if len(log):
            table(log, money=["AdjAmt", "Override", "Judgement"], decimals={"AdjPct": 1})
        else:
            st.caption("No adjustments have been made - the booked reserve equals the model position.")
        explain("Every judgement call in one place: which periods were touched, what was changed, "
                "by how much, why, and when. This is the trail a peer reviewer, auditor, or "
                "regulator will ask for.",
                "Rows of the adjustment worksheet with a non-zero judgement or an edit timestamp: "
                "basis, percentage load, dollar add-on, override, resulting judgement, comment, and "
                "last-edited time. Exported to the 'Adjustment Log' sheet.")
    with card("Sign-off"):
        c1, c2 = st.columns(2)
        with c1:
            persistent_text("Reviewing actuary", "signoff_name")
        with c2:
            persistent_text("Role / credential", "signoff_role")
        persistent_text("Overall rationale / basis of selection", "signoff_notes", area=True,
                        height=100)
        explain("Record who reviewed the reserve and the overall reasoning. These fields are "
                "stamped into the Excel export together with the booked figure and the date, "
                "closing the governance loop.",
                "Free-text sign-off captured client-side and written to a 'Sign-off' sheet on "
                "download: reviewer, role, rationale, model total IBNR, booked total IBNR, "
                "judgemental overlay, export date.")
