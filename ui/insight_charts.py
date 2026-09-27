"""Additional charts, one or two per tab (Plotly).

Colour roles (validated with the dataviz palette checker):
  * entities keep the colours they already have in the app (methods / priors,
    increase / release, booked, navy for the recommended figure);
  * new categorical series use the reference categorical order (CAT);
  * ordered categories (origins, years) use one-hue light->dark navy ramps;
  * polarity uses the blue <-> red diverging pair around a neutral grey.
Never a second y-axis - two measures get two panels.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots

from ui.charts import (BRICK, GREY, NAVY, ORANGE, PAID_TINT, PURPLE, RED, TEAL, apply_layout,
                       thin_width)

CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DIV_NEG, DIV_POS, DIV_MID = "#2a78d6", "#e34948", "#f0efec"
MUTED = "#7F8C8D"
NAVY_RAMP = [[0, PAID_TINT], [1, NAVY]]          # ordinal: lightest step clears 2:1 on white
NAVY_SEQ = [[0, "#EAF2F8"], [1, NAVY]]           # sequential (heatmaps), as the seasonality heatmap
GAP = dict(color="white", width=1)               # surface gap between touching fills
PRIOR_COLOURS = {"benktander": ORANGE, "bf": PURPLE, "capecod": TEAL}


def _legend_top_left(fig: go.Figure) -> None:
    # traceorder "normal": stacked bars otherwise list their series in reverse
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                                  traceorder="normal"))
    if fig.layout.title.text:        # subtitle pinned to the top, legend on its own line below
        fig.update_layout(title=dict(y=1, yanchor="top", yref="container", pad=dict(t=6)),
                          margin=dict(t=72))


def _ramp(n: int) -> list[str]:
    return sample_colorscale(NAVY_RAMP, np.linspace(0, 1, n)) if n > 1 else [NAVY]


# ---------------------------------------------------------------------------
# 1. Data & Checks
# ---------------------------------------------------------------------------

def claims_by_subcat(mix: pd.DataFrame) -> go.Figure:
    series = sorted(mix["SubCat"].unique(), key=lambda s: (s == "Other", s))
    months = sorted(mix["Month"].unique())
    x = [pd.Timestamp(m).strftime("%Y-%m") for m in months]
    fig = go.Figure()
    for i, s in enumerate(series):
        y = mix[mix["SubCat"] == s].set_index("Month")["Amount"].reindex(months, fill_value=0.0)
        colour = GREY if s == "Other" else CAT[i % len(CAT)]
        fig.add_trace(go.Bar(x=x, y=y.to_numpy(), name=str(s), width=thin_width(len(x)),
                             marker=dict(color=colour, line=GAP),
                             hovertemplate=f"<b>%{{y:,.0f}}</b> {s}<extra></extra>"))
    fig.update_layout(barmode="stack", hovermode="x unified", showlegend=len(series) > 1)
    apply_layout(fig, 360, y="Claim amount", tickangle=-45, origin_x=True)
    _legend_top_left(fig)
    return fig


def payment_delay(profile: pd.DataFrame, d50, d90) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=profile["Delay"], y=profile["Share"], width=0.7, marker=dict(color=NAVY, line=GAP),
        customdata=profile["CumShare"], showlegend=False,
        hovertemplate="<b>%{y:.1%}</b> of dollars paid %{x} month(s) after the origin month"
                      "<br>%{customdata:.1%} paid within %{x} month(s)<extra></extra>"))
    for d, txt, y in ((d50, "50%", 0.98), (d90, "90%", 0.84)):      # staggered: no collisions
        if d is not None:
            fig.add_vline(x=d, line_color=MUTED, line_width=1)
            fig.add_annotation(x=d, y=y, xref="x", yref="paper", xanchor="left", xshift=5,
                               showarrow=False, font=dict(size=12),
                               text=f"{txt} of dollars paid within {d} month{'s' if d != 1 else ''}")
    apply_layout(fig, 340, x="Months from origin month to payment month",
                 y="Share of paid dollars", money_y=False)
    fig.update_yaxes(tickformat=".0%")
    return fig


# ---------------------------------------------------------------------------
# 1b. Exposure & Premium
# ---------------------------------------------------------------------------

def premium_and_exposure(labels, premium, exposure) -> go.Figure:
    labels = list(labels)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12,
                        subplot_titles=("Earned premium", "Exposure"))
    for row, vals, fmt in ((1, premium, ",.0f"), (2, exposure, ",.0f")):
        fig.add_trace(go.Bar(x=labels, y=vals, width=thin_width(len(labels)), showlegend=False,
                             marker=dict(color=NAVY, line=GAP),
                             hovertemplate=f"%{{x}}<br><b>%{{y:{fmt}}}</b><extra></extra>"),
                      row=row, col=1)
        fig.update_yaxes(tickformat=fmt, row=row, col=1)
    fig.update_xaxes(type="category")
    fig.update_xaxes(tickangle=-45, row=2, col=1)
    fig.update_layout(height=440, margin=dict(l=10, r=10, t=30, b=10))
    fig.update_annotations(font_size=13, x=0, xanchor="left")
    return fig


def loss_ratio_by_origin(lr: pd.DataFrame, elr: float) -> go.Figure:
    fig = go.Figure()
    for col, name, colour in (("PaidLR", "Paid to date ÷ premium", PAID_TINT),
                              ("UltimateLR", "Blended ultimate ÷ premium", NAVY)):
        fig.add_trace(go.Scatter(x=lr["Origin"], y=lr[col], name=name, mode="lines+markers",
                                 line=dict(color=colour, width=2),
                                 marker=dict(size=8, color=colour, line=dict(color="white", width=2)),
                                 hovertemplate=f"<b>%{{y:.1%}}</b> {name}<extra></extra>"))
    if np.isfinite(elr):
        fig.add_hline(y=elr, line_color=MUTED, line_width=1,
                      annotation_text=f"A-priori ELR {elr:.1%}", annotation_position="top left")
    fig.update_layout(hovermode="x unified")
    apply_layout(fig, 360, y="Loss ratio", money_y=False, tickangle=-45, origin_x=True)
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    _legend_top_left(fig)
    return fig


# ---------------------------------------------------------------------------
# 2-4. Triangles and development
# ---------------------------------------------------------------------------

def payment_pattern_lines(origins, pattern: np.ndarray) -> go.Figure:
    """Cumulative paid as % of ultimate by development period, one line per origin."""
    n_org, n_dev = pattern.shape
    colours = _ramp(n_org)
    x = np.arange(1, n_dev + 1)
    fig = go.Figure()
    for i, (origin, colour) in enumerate(zip(origins, colours)):
        obs = ~np.isnan(pattern[i])
        edge = i in (0, n_org - 1)
        newest = i == n_org - 1           # often a single point: needs a marker to be visible
        fig.add_trace(go.Scatter(
            x=x[obs], y=pattern[i, obs], mode="lines+markers" if newest else "lines",
            line=dict(color=colour, width=2 if edge else 1.5),
            marker=dict(size=9, color=colour, line=dict(color="white", width=2)),
            name=f"{origin} ({'oldest' if i == 0 else 'newest'})" if edge else origin,
            showlegend=edge and n_org > 1,
            hovertemplate=f"{origin}<br>development %{{x}}: <b>%{{y:.1%}}</b> of ultimate paid<extra></extra>"))
    last = pattern[-1][~np.isnan(pattern[-1])]
    if last.size:
        fig.add_annotation(x=last.size, y=last[-1], text=f"{origins[-1]}: {last[-1]:.0%}",
                           showarrow=False, xanchor="left", xshift=10, font=dict(size=12),
                           bgcolor="rgba(255,255,255,0.85)")
    apply_layout(fig, 380, x="Development period", y="Paid as % of ultimate", money_y=False)
    fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
    _legend_top_left(fig)
    return fig


def incremental_heatmap(origins, z: np.ndarray) -> go.Figure:
    n_org, n_dev = z.shape
    fig = go.Figure(go.Heatmap(
        z=z, x=[str(k) for k in range(1, n_dev + 1)], y=list(origins), colorscale=NAVY_SEQ,
        xgap=1, ygap=1, colorbar=dict(title="Paid", tickformat=",.0f"),
        hovertemplate="%{y} · development %{x}<br><b>%{z:,.0f}</b> paid<extra></extra>"))
    apply_layout(fig, max(320, 22 * n_org + 110), x="Development period", money_y=False)
    fig.update_yaxes(type="category", autorange="reversed")
    fig.update_xaxes(type="category")
    return fig


def development_pattern_chart(pattern: pd.DataFrame, p50, p90) -> go.Figure:
    fig = go.Figure([
        go.Bar(x=pattern["Period"], y=pattern["Incremental"], name="Paid in the period",
               width=thin_width(len(pattern)), marker=dict(color=PAID_TINT, line=GAP),
               hovertemplate="<b>%{y:.1%}</b> of ultimate paid in period %{x}<extra></extra>"),
        go.Scatter(x=pattern["Period"], y=pattern["Cumulative"], name="Paid by the end of the period",
                   mode="lines+markers", line=dict(color=NAVY, width=2),
                   marker=dict(size=8, color=NAVY, line=dict(color="white", width=2)),
                   hovertemplate="<b>%{y:.1%}</b> of ultimate paid by period %{x}<extra></extra>"),
    ])
    for label, txt in ((p50, "50%"), (p90, "90%")):
        if label is not None:
            y = float(pattern.loc[pattern["Period"] == label, "Cumulative"].iloc[0])
            fig.add_annotation(x=label, y=y, text=f"{txt} by period {label}", showarrow=True,
                               arrowhead=0, arrowcolor=MUTED, ax=40, ay=30, font=dict(size=12))
    fig.update_layout(hovermode="x unified")
    apply_layout(fig, 360, x="Development period", y="Share of ultimate", money_y=False)
    fig.update_xaxes(type="category")
    fig.update_yaxes(tickformat=".0%", range=[0, 1.08])
    _legend_top_left(fig)
    return fig


def link_ratio_heatmap(origins, ratios: np.ndarray, deviation: np.ndarray, f: np.ndarray) -> go.Figure:
    n_org, n_link = ratios.shape
    finite = np.abs(deviation[np.isfinite(deviation)])
    lim = float(np.percentile(finite, 95)) if finite.size else 0.01
    lim = max(lim, 1e-4)
    sel = np.broadcast_to(np.asarray(f, dtype=float)[:n_link], ratios.shape)
    custom = np.dstack([ratios, sel, deviation])
    fig = go.Figure(go.Heatmap(
        z=np.clip(deviation, -lim, lim), x=[f"{k}→{k + 1}" for k in range(1, n_link + 1)],
        y=list(origins), customdata=custom, zmid=0, zmin=-lim, zmax=lim,
        colorscale=[[0, DIV_NEG], [0.5, DIV_MID], [1, DIV_POS]], xgap=1, ygap=1,
        colorbar=dict(title="vs selected", tickformat="+.1%"),
        hovertemplate="%{y} · %{x}<br>factor <b>%{customdata[0]:.4f}</b> vs selected "
                      "%{customdata[1]:.4f}<br><b>%{customdata[2]:+.2%}</b> vs selected<extra></extra>"))
    apply_layout(fig, max(320, 22 * n_org + 110), x="Development step", money_y=False)
    fig.update_yaxes(type="category", autorange="reversed")
    fig.update_xaxes(type="category")
    return fig


# ---------------------------------------------------------------------------
# 5. Reserve estimates
# ---------------------------------------------------------------------------

def ibnr_with_se(reserve_df: pd.DataFrame) -> go.Figure:
    d = reserve_df[(reserve_df["Origin"] != "Total")]
    fig = go.Figure(go.Bar(
        x=d["Origin"], y=d["IBNR"], width=thin_width(len(d)), showlegend=False,
        marker=dict(color=NAVY, line=GAP),
        error_y=dict(type="data", array=d["Mack.S.E"], color=MUTED, thickness=1.5, width=4),
        customdata=np.stack([d["Mack.S.E"], d["CV"]], axis=-1),
        hovertemplate="%{x}<br><b>%{y:,.0f}</b> IBNR ± %{customdata[0]:,.0f}"
                      " (CV %{customdata[1]:.0%})<extra></extra>"))
    return apply_layout(fig, 360, subtitle="Whiskers: ±1 Mack standard error", y="IBNR reserve",
                        tickangle=-45, origin_x=True)


def method_totals_bars(method_cmp: pd.DataFrame) -> go.Figure:
    d = method_cmp.iloc[::-1]                          # first method on top
    se = d["Std.Err"].to_numpy(dtype=float)
    fig = go.Figure(go.Bar(
        y=d["Method"], x=d["Total.IBNR"], orientation="h", width=0.5, showlegend=False,
        marker=dict(color=NAVY, line=GAP), text=[f"{v:,.0f}" for v in d["Total.IBNR"]],
        # inside the bar end: an outside label would collide with the S.E. whisker
        textposition="inside", insidetextanchor="end", textfont=dict(color="white"),
        error_x=dict(type="data", array=np.nan_to_num(se), visible=True, color=MUTED, thickness=1.5,
                     width=4),
        hovertemplate="%{y}<br><b>%{x:,.0f}</b> total IBNR<extra></extra>"))
    apply_layout(fig, 90 + 56 * len(d), subtitle="Whiskers: ±1 standard error where the method "
                                                 "provides one", money_y=False)
    fig.update_xaxes(tickformat=",.0f", title="Total IBNR")
    fig.update_layout(margin=dict(l=10, r=30, t=48, b=10))
    return fig


# ---------------------------------------------------------------------------
# 6-7. Uncertainty
# ---------------------------------------------------------------------------

def reserve_cdf(points: pd.DataFrame, markers: list[tuple[str, float, float, str]]) -> go.Figure:
    """markers: (label, value, percentile, colour)."""
    fig = go.Figure(go.Scatter(
        x=points["IBNR"], y=points["Prob"], mode="lines", line=dict(color=NAVY, width=2),
        showlegend=False,
        hovertemplate="<b>%{y:.0%}</b> chance total IBNR is at or below %{x:,.0f}<extra></extra>"))
    xs = np.concatenate([points["IBNR"].to_numpy(dtype=float), [m[1] for m in markers]])
    lo, hi = float(xs.min()), float(xs.max())
    pad = 0.04 * (hi - lo or 1.0)
    for label, value, pct, colour in markers:
        # label on whichever side has room, so nothing is clipped at the plot edge
        side = "left" if value > lo + 0.7 * (hi - lo) else "right"
        side = ("top " if pct < 0.15 else "bottom " if pct > 0.9 else "middle ") + side
        fig.add_trace(go.Scatter(
            x=[value], y=[pct], mode="markers+text", name=label, showlegend=True,
            marker=dict(size=11, color=colour, line=dict(color="white", width=2)),
            text=[f"{label}: {pct:.0%}"], textposition=side,
            hovertemplate=f"{label}<br><b>%{{x:,.0f}}</b> sits at the %{{y:.0%}} level<extra></extra>"))
    apply_layout(fig, 360, x="Total IBNR", y="Probability the reserve is enough", money_y=False)
    fig.update_xaxes(tickformat=",.0f", range=[lo - pad, hi + pad])
    fig.update_yaxes(tickformat=".0%", range=[-0.04, 1.04], tickvals=[0, 0.25, 0.5, 0.75, 1])
    _legend_top_left(fig)
    return fig


def origin_spread(bands: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Box(
        x=bands["Origin"], q1=bands["P25"], median=bands["P50"], q3=bands["P75"],
        lowerfence=bands["P5"], upperfence=bands["P95"], fillcolor=PAID_TINT,
        line=dict(color=NAVY, width=1.5), showlegend=False, name="Simulated IBNR",
        hoverinfo="x+y"))
    return apply_layout(fig, 380, subtitle="Box: 25th-75th percentile · line: median · whiskers: "
                                           "5th-95th percentile", y="Simulated IBNR",
                        tickangle=-45, origin_x=True)


def risk_components_bars(rc: pd.DataFrame) -> go.Figure:
    fig = go.Figure([
        go.Bar(x=rc["Origin"], y=rc[col], name=name, marker=dict(color=colour, line=GAP),
               hovertemplate=f"<b>%{{y:,.0f}}</b> {name.lower()}<extra></extra>")
        for col, name, colour in (("Process", "Process risk (S.E.)", CAT[0]),
                                  ("Parameter", "Parameter risk (S.E.)", CAT[1]))])
    fig.update_layout(barmode="group", hovermode="x unified", bargap=0.35, bargroupgap=0.08)
    apply_layout(fig, 360, subtitle="Total Mack S.E. = √(process² + parameter²) - the two "
                                    "components do not simply add", y="Standard error",
                 tickangle=-45, origin_x=True)
    _legend_top_left(fig)
    return fig


# ---------------------------------------------------------------------------
# 8. Blend sensitivity
# ---------------------------------------------------------------------------

def kappa_sensitivity_chart(sens: pd.DataFrame, current_kappa: float, current_prior: str,
                            cl_total: float, labels: dict) -> go.Figure:
    fig = go.Figure()
    ends = {}
    for prior, colour in PRIOR_COLOURS.items():
        d = sens[sens["Prior"] == prior]
        fig.add_trace(go.Scatter(
            x=d["Kappa"], y=d["TotalIBNR"], name=labels[prior], mode="lines",
            line=dict(color=colour, width=2),
            hovertemplate=f"<b>%{{y:,.0f}}</b> {labels[prior]}<extra></extra>"))
        ends[prior] = (float(d["Kappa"].iloc[-1]), float(d["TotalIBNR"].iloc[-1]))
    # Direct end labels only when they cannot collide; otherwise the legend carries identity.
    ys = sorted(y for _, y in ends.values())
    span = max(sens["TotalIBNR"].max(), cl_total) - min(sens["TotalIBNR"].min(), cl_total)
    end_labels = len(ys) < 2 or min(np.diff(ys)) > 0.07 * (span or 1)
    if end_labels:
        for prior, (x_end, y_end) in ends.items():
            fig.add_annotation(x=x_end, y=y_end, text=labels[prior], showarrow=False,
                               xanchor="left", xshift=6, font=dict(size=12))
    fig.add_hline(y=cl_total, line_color=RED, line_width=1,
                  annotation_text=f"Chain Ladder {cl_total:,.0f}", annotation_position="top left")
    cur = sens[(sens["Prior"] == current_prior) & np.isclose(sens["Kappa"], current_kappa)]
    if len(cur):
        v = float(cur["TotalIBNR"].iloc[0])
        fig.add_trace(go.Scatter(x=[current_kappa], y=[v], mode="markers", name="Current setting",
                                 marker=dict(size=13, color=NAVY, line=dict(color="white", width=2)),
                                 hovertemplate=f"Current setting (κ = {current_kappa:.2f})"
                                               "<br><b>%{y:,.0f}</b><extra></extra>"))
    fig.update_layout(hovermode="x unified")
    apply_layout(fig, 380, x="Credibility steepness (kappa)", y="Total blended IBNR")
    k = sens["Kappa"]
    fig.update_xaxes(range=[k.min() - 0.05, k.max() + 0.05])
    fig.update_layout(margin=dict(l=10, r=140 if end_labels else 20, t=28, b=10))
    _legend_top_left(fig)
    return fig


# ---------------------------------------------------------------------------
# 9-10. Adjustments and final summary
# ---------------------------------------------------------------------------

def judgement_bars(ar: pd.DataFrame) -> go.Figure:
    j = ar["Judgement"].to_numpy(dtype=float)
    width = thin_width(len(ar))
    fig = go.Figure()
    for mask, name, colour in ((j > 1e-9, "Increase", TEAL), (j < -1e-9, "Release", BRICK)):
        fig.add_trace(go.Bar(x=ar["Origin"], y=np.where(mask, j, 0.0), name=name, width=width,
                             marker=dict(color=colour, line=GAP), customdata=ar["Basis"],
                             hovertemplate=f"<b>%{{y:+,.0f}}</b> {name.lower()} (basis %{{customdata}})"
                                           "<extra></extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    fig.update_layout(barmode="relative", hovermode="x unified")
    apply_layout(fig, 340, y="Judgement (selected − model ultimate)", tickangle=-45, origin_x=True)
    _legend_top_left(fig)
    return fig


def football_field(methods: pd.DataFrame, band: tuple | None, booked: float) -> go.Figure:
    rows = list(methods["Label"])
    if band is not None:
        rows = ["Simulated range (5th-95th pct)"] + rows
    fig = go.Figure()
    if band is not None:
        p5, p50, p95 = band
        fig.add_trace(go.Bar(y=[rows[0]], x=[p95 - p5], base=[p5], orientation="h", width=0.35,
                             marker=dict(color="#D5DCE4"), name="Simulated 5th-95th pct",
                             hovertemplate=f"5th pct {p5:,.0f} · 95th pct {p95:,.0f}<extra></extra>"))
        fig.add_trace(go.Scatter(y=[rows[0]], x=[p50], mode="markers", name="Simulated median",
                                 marker=dict(symbol="line-ns", size=18, line=dict(color=NAVY, width=2.5)),
                                 hovertemplate="Simulated median <b>%{x:,.0f}</b><extra></extra>"))
    colours = [NAVY if k == "recommended" else GREY for k in methods["Kind"]]
    fig.add_trace(go.Scatter(
        y=methods["Label"], x=methods["Value"], mode="markers", name="Method estimate",
        marker=dict(size=12, color=colours, line=dict(color="white", width=2)),
        hovertemplate="%{y}<br><b>%{x:,.0f}</b> total IBNR<extra></extra>"))
    fig.add_vline(x=booked, line_color=BRICK, line_width=2,
                  annotation_text=f"Booked {booked:,.0f}", annotation_position="top",
                  annotation_font_color=BRICK)
    apply_layout(fig, 90 + 48 * len(rows), x="Total IBNR", money_y=False)
    fig.update_xaxes(tickformat=",.0f")
    fig.update_yaxes(categoryorder="array", categoryarray=rows[::-1])
    fig.update_layout(showlegend=False, margin=dict(l=10, r=20, t=40, b=10))
    return fig


# ---------------------------------------------------------------------------
# 11-13. Recast, seasonality, executive summary
# ---------------------------------------------------------------------------

def recast_error_bars(err: pd.DataFrame) -> go.Figure:
    e = err["Error"].to_numpy(dtype=float)
    width = thin_width(len(err))
    custom = np.stack([err["Actual"], err["Expected"]], axis=-1)
    fig = go.Figure()
    for mask, name, colour in ((e > 0, "Actual above expected", DIV_POS),
                               (e <= 0, "Actual below expected", DIV_NEG)):
        fig.add_trace(go.Bar(x=err["Origin"], y=np.where(mask, e, 0.0), name=name, width=width,
                             marker=dict(color=colour, line=GAP), customdata=custom,
                             hovertemplate="<b>%{y:+.1%}</b> vs expected<br>actual %{customdata[0]:,.0f}"
                                           " · expected %{customdata[1]:,.0f}<extra></extra>"))
    fig.add_hline(y=0, line_color=MUTED, line_width=1)
    total = err["Actual"].sum() / err["Expected"].sum() - 1
    fig.update_layout(barmode="relative", hovermode="x unified")
    apply_layout(fig, 340, subtitle=f"Overall: actual {total:+.1%} vs expected across all "
                                    "held-back cells", y="Actual vs expected", money_y=False,
                 tickangle=-45, origin_x=True)
    fig.update_yaxes(tickformat="+.0%")
    _legend_top_left(fig)
    return fig


def paid_trend(pm: pd.DataFrame) -> go.Figure:
    x = [m.strftime("%Y-%m") for m in pm["Month"]]
    fig = go.Figure([
        go.Bar(x=x, y=pm["Paid"], name="Paid in the month", width=thin_width(len(x)),
               marker=dict(color=PAID_TINT, line=GAP),
               hovertemplate="<b>%{y:,.0f}</b> paid in the month<extra></extra>"),
        go.Scatter(x=x, y=pm["Trend"], name="12-month average", mode="lines",
                   line=dict(color=NAVY, width=2),
                   hovertemplate="<b>%{y:,.0f}</b> 12-month average<extra></extra>"),
    ])
    fig.update_layout(hovermode="x unified")
    apply_layout(fig, 360, y="Paid", tickangle=-45, origin_x=True)
    _legend_top_left(fig)
    return fig


def reserve_ladder_bars(ladder: pd.DataFrame) -> go.Figure:
    colour = {"recommended": NAVY, "booked": BRICK, "model": GREY, "stress": "#C4CBD3"}
    fig = go.Figure(go.Bar(
        y=ladder["Label"], x=ladder["Value"], orientation="h", width=0.55, showlegend=False,
        marker=dict(color=[colour[k] for k in ladder["Kind"]], line=GAP),
        text=[f"{v:,.0f}" for v in ladder["Value"]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>"))
    apply_layout(fig, 90 + 44 * len(ladder), x="Total IBNR", money_y=False)
    fig.update_xaxes(tickformat=",.0f")
    fig.update_layout(margin=dict(l=10, r=100, t=20, b=10))
    return fig
