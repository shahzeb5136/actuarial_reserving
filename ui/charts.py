"""Plotly versions of the R app's ggplot / base-graphics charts, keeping its colours."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots

from reserving.lowess import lowess
from reserving.mack import MackResult
from reserving.seasonality import MONTH_ABB

NAVY, RED, GREEN, PURPLE, ORANGE, TEAL, GREY, BRICK, SILVER = (
    "#2C3E50", "#E74C3C", "#27AE60", "#8E44AD", "#E67E22", "#16A085", "#95A5A6", "#B03A2E",
    "#BDC3C7")
METHOD_COLOURS = {"CL": RED, "Expected": GREEN, "BF": PURPLE, "Benktander": ORANGE,
                  "CapeCod": TEAL, "Blended": NAVY}


def thin_width(n_bars: int) -> float:
    """Bar width (category units) for thin ~20-26px columns with air between them."""
    return min(0.7, 0.024 * max(n_bars, 1))


def apply_layout(fig: go.Figure, height: int = 380, subtitle: str | None = None,
                 x: str | None = None, y: str | None = None, money_y: bool = True,
                 tickangle: int | None = None, origin_x: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=48 if subtitle else 28, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, title=None),
        bargap=0.2,
    )
    if subtitle:
        fig.update_layout(title=dict(text=subtitle, x=0, xanchor="left", font=dict(size=13)))
    fig.update_xaxes(title=x, tickangle=tickangle)
    if origin_x:            # origin labels are categories, not a time axis
        fig.update_xaxes(type="category")
    fig.update_yaxes(title=y, tickformat=",.0f" if money_y else None)
    return fig


def _hist_bins(values: np.ndarray, bins: int = 50) -> dict:
    lo, hi = float(np.min(values)), float(np.max(values))
    width = (hi - lo) / (bins - 1) if hi > lo else 1.0      # ggplot2's bins = 50 rule
    return dict(start=lo - width / 2, end=hi + width / 2, size=width)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_histogram(sims: np.ndarray) -> go.Figure:
    fig = go.Figure(go.Histogram(x=sims, xbins=_hist_bins(sims), marker_color=NAVY, opacity=0.85,
                                 name="Simulations",
                                 hovertemplate="IBNR %{x:,.0f}<br>%{y} simulations<extra></extra>"))
    for p, q in zip(("50%", "75%", "95%"), np.quantile(sims, [0.5, 0.75, 0.95])):
        fig.add_vline(x=q, line_dash="dash", line_color=RED, annotation_text=p,
                      annotation_position="top right", annotation_font_color=RED)
    apply_layout(fig, 360, x="Total IBNR", y="Simulations", money_y=False)
    fig.update_xaxes(tickformat=",.0f")
    return fig


def booked_vs_distribution(sims: np.ndarray, model: float, booked: float, pctl: float) -> go.Figure:
    fig = go.Figure(go.Histogram(x=sims, xbins=_hist_bins(sims), marker_color=SILVER, opacity=0.9,
                                 name="Simulations",
                                 hovertemplate="IBNR %{x:,.0f}<br>%{y} simulations<extra></extra>"))
    fig.add_vline(x=model, line_dash="dash", line_width=2, line_color=NAVY, annotation_text="Model",
                  annotation_position="top left", annotation_font_color=NAVY)
    fig.add_vline(x=booked, line_width=2.5, line_color=BRICK, annotation_text="Booked",
                  annotation_position="top right", annotation_font_color=BRICK)
    apply_layout(fig, 360, subtitle=f"Booked reserve sits at the {100 * pctl:.0f}% percentile of "
                               "simulated outcomes", x="Total IBNR", y="Simulations", money_y=False)
    fig.update_xaxes(tickformat=",.0f")
    return fig


# ---------------------------------------------------------------------------
# Mack diagnostics (plot.MackChainLadder)
# ---------------------------------------------------------------------------

def mack_results(by_origin: pd.DataFrame) -> go.Figure:
    """which = 1: latest + forecast (IBNR) per origin with Mack S.E. error bars."""
    d = by_origin[by_origin["Origin"] != "Total"]
    fig = go.Figure([
        go.Bar(x=d["Origin"], y=d["Latest"], name="Latest", marker_color=NAVY,
               hovertemplate="%{x}<br>Latest %{y:,.0f}<extra></extra>"),
        go.Bar(x=d["Origin"], y=d["IBNR"], name="Forecast", marker_color=SILVER,
               hovertemplate="%{x}<br>Forecast (IBNR) %{y:,.0f}<extra></extra>"),
        go.Scatter(x=d["Origin"], y=d["Ultimate"], mode="markers", name="Ultimate ± Mack S.E.",
                   marker=dict(color="rgba(0,0,0,0)", size=1),
                   error_y=dict(type="data", array=d["Mack.S.E"], color="#34495E", thickness=1.4,
                                width=4),
                   hovertemplate="%{x}<br>Ultimate %{y:,.0f}<br>± %{error_y.array:,.0f}<extra></extra>"),
    ])
    fig.update_layout(barmode="stack")
    return apply_layout(fig, 380, x="Origin period", y="Amount", tickangle=-45, origin_x=True)


def mack_developments(mack: MackResult) -> go.Figure:
    """which = 2: chain ladder developments by origin period (observed solid, projected dashed)."""
    full, tri = mack.full_triangle, mack.triangle
    n_dev = tri.shape[1]
    x = list(range(1, full.shape[1] + 1))
    xlabels = [str(k) for k in range(1, n_dev + 1)] + (["Ult"] if full.shape[1] > n_dev else [])
    colours = sample_colorscale("Blues", np.linspace(0.35, 1.0, len(mack.origins)))
    fig = go.Figure()
    for i, (origin, colour) in enumerate(zip(mack.origins, colours)):
        n_obs = int(np.sum(~np.isnan(tri[i])))
        fig.add_trace(go.Scatter(x=x[n_obs - 1:], y=full[i, n_obs - 1:], mode="lines",
                                 line=dict(color=colour, dash="dot", width=1.3), showlegend=False,
                                 hovertemplate=f"{origin}<br>dev %{{x}}: %{{y:,.0f}} (projected)<extra></extra>"))
        fig.add_trace(go.Scatter(x=x[:n_obs], y=tri[i, :n_obs], mode="lines+markers",
                                 line=dict(color=colour, width=1.6), marker=dict(size=4),
                                 name=origin, showlegend=False,
                                 hovertemplate=f"{origin}<br>dev %{{x}}: %{{y:,.0f}}<extra></extra>"))
    apply_layout(fig, 380, subtitle="Solid = observed cumulative, dotted = chain ladder projection",
            x="Development period", y="Amount")
    if len(x) <= 16:
        fig.update_xaxes(tickvals=x, ticktext=xlabels)
    return fig


def mack_residuals(mack: MackResult) -> go.Figure:
    """which = 3..6: standardised residuals vs fitted, origin, calendar and development
    period, each with R's lowess trend line."""
    r = mack.residuals()
    panels = [("fitted.value", "Fitted"), ("origin.period", "Origin period"),
              ("cal.period", "Calendar period"), ("dev.period", "Development period")]
    fig = make_subplots(rows=2, cols=2, horizontal_spacing=0.08, vertical_spacing=0.16)
    for k, (col, label) in enumerate(panels):
        row, c = divmod(k, 2)
        xs, ys = r[col].to_numpy(float), r["standard.residuals"].to_numpy(float)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", showlegend=False,
                                 marker=dict(color=NAVY, size=5, opacity=0.6),
                                 hovertemplate=f"{label} %{{x:,.0f}}<br>residual %{{y:.2f}}<extra></extra>"),
                      row=row + 1, col=c + 1)
        if xs.size >= 2:
            lx, ly = lowess(xs, ys)
            fig.add_trace(go.Scatter(x=lx, y=ly, mode="lines", line=dict(color=RED, width=2),
                                     showlegend=False, hoverinfo="skip"), row=row + 1, col=c + 1)
        fig.add_hline(y=0, line_color=GREY, line_width=1, row=row + 1, col=c + 1)
        fig.update_xaxes(title_text=label, row=row + 1, col=c + 1,
                         tickformat="~s" if col == "fitted.value" else None)
        fig.update_yaxes(title_text="Standardised residuals" if c == 0 else None, row=row + 1, col=c + 1)
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=20, b=10))
    return fig


# ---------------------------------------------------------------------------
# Blend
# ---------------------------------------------------------------------------

def blend_curves(blend) -> go.Figure:
    d = blend.detail
    fig = go.Figure()
    for method in ["CL", "Expected", "BF", "Benktander", "CapeCod", "Blended"]:
        hl = method == "Blended"
        fig.add_trace(go.Scatter(x=d["Origin"], y=d[method], name=method,
                                 mode="lines+markers" if hl else "lines",
                                 line=dict(color=METHOD_COLOURS[method], width=3.2 if hl else 1.4),
                                 marker=dict(size=6),
                                 hovertemplate=f"{method}<br>%{{x}}: %{{y:,.0f}}<extra></extra>"))
    return apply_layout(fig, 380, subtitle="Methods agree at maturity (left) and diverge for green "
                                      "periods (right)", y="Estimated ultimate", tickangle=-45,
                   origin_x=True)


def credibility_curve(blend) -> go.Figure:
    d = blend.detail.sort_values("Pct.Dev", kind="stable")
    fig = go.Figure([
        go.Scatter(x=d["Pct.Dev"], y=d["Credibility"], mode="lines", line=dict(color=GREY),
                   showlegend=False, hoverinfo="skip"),
        go.Scatter(x=d["Pct.Dev"], y=d["Credibility"], mode="markers", showlegend=False,
                   text=d["Origin"],
                   marker=dict(size=10, color=d["Credibility"], cmin=0, cmax=1,
                               colorscale=[[0, RED], [1, NAVY]]),
                   hovertemplate="%{text}<br>%{x:.1%} developed<br>Z = %{y:.1%}<extra></extra>"),
    ])
    apply_layout(fig, 320, subtitle=f"Z = (% developed)^kappa, kappa = {blend.kappa:.1f}",
            x="Maturity (% developed)", y="Credibility Z (weight on own data)", money_y=False)
    fig.update_xaxes(tickformat=".0%")
    fig.update_yaxes(tickformat=".0%", range=[0, 1.02])
    return fig


def grouped_bars(origins, series: list[tuple[str, np.ndarray, str]], y: str,
                 subtitle: str | None = None, height: int = 360) -> go.Figure:
    fig = go.Figure([go.Bar(x=list(origins), y=vals, name=name, marker_color=colour,
                            hovertemplate=f"{name}<br>%{{x}}: %{{y:,.0f}}<extra></extra>")
                     for name, vals, colour in series])
    fig.update_layout(barmode="group")
    return apply_layout(fig, height, subtitle=subtitle, y=y, tickangle=-45, origin_x=True)


def blend_ibnr(blend) -> go.Figure:
    d = blend.detail
    return grouped_bars(d["Origin"], [("Chain Ladder", d["IBNR.CL"], RED),
                                      ("Blended", d["IBNR.Blend"], NAVY)],
                        "IBNR reserve", "Identical for mature origins; blend tames the volatile "
                                        "newest periods")


# ---------------------------------------------------------------------------
# Adjustments / final summary
# ---------------------------------------------------------------------------

def adjustment_compare(ar: pd.DataFrame) -> go.Figure:
    fig = grouped_bars(ar["Origin"], [("Model", ar["Model.IBNR"], GREY),
                                      ("Booked", ar["Selected.IBNR"], NAVY)],
                       "IBNR reserve", "Grey = model position; dark = after actuarial adjustment")
    ov = ar[np.isfinite(ar["Override"].astype(float)) & (ar["Override"].astype(float) > 0)]
    if len(ov):
        fig.add_trace(go.Scatter(x=ov["Origin"], y=ov["Selected.IBNR"], mode="markers",
                                 name="Override", marker=dict(symbol="asterisk", size=12, color=BRICK,
                                                              line=dict(width=2, color=BRICK)),
                                 hovertemplate="%{x}: override<extra></extra>"))
    return fig


def reserve_walk(steps: pd.DataFrame, subtitle: str) -> go.Figure:
    colours = {"total": NAVY, "up": TEAL, "down": BRICK}
    names = {"total": "Position", "up": "Increase", "down": "Release"}
    x = list(range(len(steps)))
    fig = go.Figure()
    for typ in ("total", "up", "down"):
        s = steps[steps["Type"] == typ]
        if len(s):
            fig.add_trace(go.Bar(x=[x[i] for i in s.index], y=s["ymax"] - s["ymin"], base=s["ymin"],
                                 name=names[typ], marker_color=colours[typ], width=0.76,
                                 customdata=np.stack([s["Label"], s["Amount"]], axis=-1),
                                 hovertemplate="%{customdata[0]}<br>%{customdata[1]:,.0f}<extra></extra>"))
    for k in range(len(steps) - 1):
        row = steps.iloc[k]
        level = row["ymax"] if row["Type"] in ("total", "up") else row["ymin"]
        fig.add_shape(type="line", x0=k + 0.38, x1=k + 0.62, y0=level, y1=level,
                      line=dict(color="#7F8C8D", dash="dot"))
    fig.update_layout(barmode="overlay")
    apply_layout(fig, 380, subtitle=subtitle, y="Total IBNR", tickangle=-30)
    fig.update_xaxes(tickvals=x, ticktext=steps["Label"].tolist())
    return fig


PAID_TINT = "#8FA3B8"   # light step of the navy ramp; validated as an ordinal pair with NAVY


def ultimate_split(split: pd.DataFrame, period: str) -> go.Figure:
    """Booked ultimate per origin as paid to date (light) + IBNR (dark), with the
    share not yet paid in its own panel below (small multiples - one y-scale each)."""
    x = split["Origin"].tolist()
    share = split["IBNR.Share"].to_numpy(dtype=float)
    width = thin_width(len(x))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                        vertical_spacing=0.07)
    gap = dict(color="white", width=1)               # surface gap between stacked segments
    fig.add_trace(go.Bar(x=x, y=split["Paid"], name="Paid to date", marker=dict(color=PAID_TINT, line=gap),
                         width=width, customdata=share,
                         hovertemplate="<b>%{y:,.0f}</b> paid to date<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Bar(x=x, y=split["IBNR"], name="IBNR (booked)", marker=dict(color=NAVY, line=gap),
                         width=width, customdata=np.stack([split["Ultimate"], share], axis=-1),
                         hovertemplate="<b>%{y:,.0f}</b> IBNR (%{customdata[1]:.1%} of ultimate)"
                                       "<br><b>%{customdata[0]:,.0f}</b> booked ultimate<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=share, mode="lines+markers", name="IBNR share", showlegend=False,
                             line=dict(color=NAVY, width=2),
                             marker=dict(size=8, color=NAVY, line=dict(color="white", width=2)),
                             hovertemplate="<b>%{y:.1%}</b> of ultimate not yet paid<extra></extra>"),
                  row=2, col=1)
    if len(x) and np.isfinite(share[-1]):             # one direct label: the newest origin
        fig.add_annotation(x=x[-1], y=share[-1], xref="x2", yref="y2", text=f"{share[-1]:.0%}",
                           showarrow=False, xanchor="left", xshift=9, font=dict(size=12))
    finite = share[np.isfinite(share)]
    lo = min(0.0, finite.min() * 1.1) if finite.size else 0.0
    hi = max(1.05, finite.max() * 1.05) if finite.size else 1.05
    fig.update_layout(barmode="relative", height=540, hovermode="x unified", barcornerradius=4,
                      margin=dict(l=10, r=30, t=28, b=10),   # legend top-left, clear of the modebar
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0))
    fig.update_xaxes(type="category")
    fig.update_xaxes(tickangle=-45, title_text=f"Origin {period}", row=2, col=1)
    fig.update_yaxes(title_text="Booked ultimate", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(title_text="IBNR share", tickformat=".0%", range=[lo, hi],
                     tickvals=[0, 0.5, 1.0], row=2, col=1)
    return fig


def final_ibnr(ar: pd.DataFrame) -> go.Figure:
    return grouped_bars(ar["Origin"], [("Chain Ladder", ar["CL.IBNR"], RED),
                                       ("Model selection", ar["Model.IBNR"], GREY),
                                       ("Booked", ar["Selected.IBNR"], NAVY)], "IBNR reserve")


# ---------------------------------------------------------------------------
# Recast / seasonality
# ---------------------------------------------------------------------------

def recast_bars(detail: pd.DataFrame) -> go.Figure:
    d = detail.groupby("Origin", sort=True)[["Actual", "Expected"]].sum().reset_index()
    return grouped_bars(d["Origin"], [("Actual", d["Actual"], NAVY),
                                      ("Expected", d["Expected"], GREY)], "Held-back cumulative")


def seasonality_lines(index: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_hline(y=100, line_dash="dot", line_color="#7F8C8D")
    for basis, colour in (("Origin month", NAVY), ("Payment month", ORANGE)):
        d = index[index["Basis"] == basis]
        fig.add_trace(go.Scatter(x=d["Mnth"], y=d["Index"], name=basis, mode="lines+markers",
                                 line=dict(color=colour, width=2.5), marker=dict(size=7),
                                 hovertemplate=f"{basis}<br>%{{x}}: %{{y:.1f}}<extra></extra>"))
    apply_layout(fig, 360, y="Index (100 = avg)", money_y=False)
    fig.update_xaxes(categoryorder="array", categoryarray=MONTH_ABB)
    return fig


def payment_heatmap(heat: pd.DataFrame) -> go.Figure:
    z = heat.pivot(index="Yr", columns="Mnth", values="Amount")
    z = z.reindex(columns=[m for m in MONTH_ABB if m in z.columns])
    fig = go.Figure(go.Heatmap(z=z.to_numpy(), x=list(z.columns), y=list(z.index),
                               colorscale=[[0, "#EAF2F8"], [1, NAVY]], xgap=2, ygap=2,
                               colorbar=dict(title="Paid", tickformat=",.0f"),
                               hovertemplate="%{y} %{x}<br>Paid %{z:,.0f}<extra></extra>"))
    apply_layout(fig, 320, money_y=False)
    fig.update_yaxes(type="category")      # first year at the bottom, as in ggplot
    return fig
