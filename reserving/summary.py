"""Narrative outputs: executive summary, final-summary headline, guardrail
alerts and status banners.

Kept free of UI code so the exact wording and every number in it can be
compared against the R app's rendered HTML.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .formatting import fmt_money, fmt_pct, fmt_signed_money

NAVY, RED, PURPLE, TEAL, ORANGE, GREEN, AMBER, GREY, BRICK = (
    "#2C3E50", "#E74C3C", "#8E44AD", "#16A085", "#E67E22", "#27AE60", "#F39C12",
    "#95A5A6", "#B03A2E")


@dataclass
class Box:
    title: str
    value: str
    sub: str
    accent: str = NAVY


@dataclass
class Point:
    head: str
    body: str          # may contain <b> tags
    accent: str = NAVY


@dataclass
class Alert:
    kind: str          # danger | warning | success | info | secondary
    title: str         # bold lead-in (may be empty)
    body: str


def _finite(x) -> bool:
    return x is not None and np.isfinite(x)


# ---------------------------------------------------------------------------
# Value boxes
# ---------------------------------------------------------------------------

def data_value_boxes(df: pd.DataFrame) -> dict[str, str]:
    return {"rows": f"{len(df):,}",
            "total": fmt_money(df["ClaimAmount"].sum()),
            "span": f"{df['OriginDate'].min():%Y-%m-%d} -> {df['OriginDate'].max():%Y-%m-%d}"}


def exposure_value_boxes(exposure: pd.DataFrame | None, res) -> dict[str, str]:
    matched = "-"
    if res is not None and res.exp_al is not None:
        matched = f"{res.exp_al.matched} / {res.exp_al.n_origins}"
    if exposure is None:
        return {"matched": matched, "premium": "-", "exposure": "-"}
    return {"matched": matched,
            "premium": fmt_money(exposure["EarnedPremium"].sum()),
            "exposure": fmt_money(exposure["Exposure"].sum())}


def adjustment_value_boxes(ar: pd.DataFrame) -> dict[str, str]:
    return {"base": fmt_money(ar["Model.IBNR"].sum(skipna=False)),
            "final": fmt_money(ar["Selected.IBNR"].sum(skipna=False)),
            "delta": fmt_signed_money(ar["Judgement"].sum(skipna=False))}


# ---------------------------------------------------------------------------
# Status banners
# ---------------------------------------------------------------------------

def exposure_status(exposure: pd.DataFrame | None, res) -> Alert:
    if exposure is None:
        return Alert("secondary", "No exposure / earned premium loaded. ",
                     "Upload an .xlsx with columns Period, EarnedPremium, Exposure in the "
                     "sidebar to switch the Bornhuetter-Ferguson, Benktander and Cape Cod "
                     "methods onto a proper exposure base. Without it, those methods fall "
                     "back to data-only (CL-seeded / equal-exposure) assumptions.")
    if res is None or res.exp_al is None:
        return Alert("info", "Exposure file loaded. ",
                     f"{len(exposure)} period(s) read. Press Run analysis to align it to the "
                     "triangle and apply the exposure-based methods.")
    return Alert("success", "Exposure applied. ",
                 f"{res.exp_al.matched} of {res.exp_al.n_origins} origin periods matched. "
                 "Earned premium drives the BF / Benktander a-priori; exposure drives the "
                 "Cape Cod ELR.")


def recast_status(res) -> Alert | None:
    if res.recast_error is not None:
        return Alert("warning", "Recast could not run. ", res.recast_error)
    if res.recast is None:
        return None
    return Alert("success", "", f"Back-test complete: {len(res.recast.detail)} held-back cells compared.")


# ---------------------------------------------------------------------------
# Adjustments / final summary
# ---------------------------------------------------------------------------

def guardrail_alerts(ar: pd.DataFrame, boot) -> list[Alert]:
    alerts = []
    neg = ar[ar["Selected.Ultimate"] < ar["Latest"]]
    if len(neg):
        alerts.append(Alert("danger", "Selected ultimate below paid-to-date: ",
                            ", ".join(neg["Origin"]) + ". This implies a negative reserve "
                            "(net recoveries). Confirm this is intended before booking."))
    ov = ar["Override"].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        has_ov = np.isfinite(ov) & (ov > 0)
    no_comment = ar["Comment"].fillna("").astype(str).str.len().eq(0).to_numpy()
    over = ar[has_ov & no_comment]
    if len(over):
        alerts.append(Alert("warning", "Overrides without a rationale: ",
                            ", ".join(over["Origin"]) + ". Add a Comment so the audit log "
                            "explains the judgement."))
    if boot is not None:
        tot = ar["Selected.IBNR"].sum(skipna=False)
        lo, hi = np.quantile(boot.sim_totals, [0.05, 0.95])
        if _finite(tot) and (tot < lo or tot > hi):
            alerts.append(Alert("warning", "Outside the simulated range. ",
                                f"The booked total IBNR ({fmt_money(tot)}) falls outside the "
                                f"bootstrap 5%-95% interval ({fmt_money(lo)} to {fmt_money(hi)}). "
                                "Large judgemental positions warrant explicit documentation in "
                                "the sign-off."))
        elif _finite(tot):
            alerts.append(Alert("success", "",
                                f"The booked total IBNR ({fmt_money(tot)}) sits within the "
                                f"simulated 5%-95% range ({fmt_money(lo)} to {fmt_money(hi)})."))
    return alerts


def booked_percentile(ar: pd.DataFrame, boot) -> float:
    booked = ar["Selected.IBNR"].sum(skipna=False)
    if boot is None or not _finite(booked):
        return np.nan
    return float(np.mean(boot.sim_totals <= booked))


def final_headline(ar: pd.DataFrame, boot) -> list[Box]:
    booked = ar["Selected.IBNR"].sum(skipna=False)
    model = ar["Model.IBNR"].sum(skipna=False)
    cl_tot = ar["CL.IBNR"].sum(skipna=True)
    judged = ar["Judgement"].sum(skipna=False)
    n_adj = int((ar["Judgement"].abs() > 1e-9).sum())
    pctl = booked_percentile(ar, boot)
    pctl_txt = f"{100 * pctl:.0f}% percentile of simulation" if _finite(pctl) else None
    return [
        Box("Booked IBNR reserve", fmt_money(booked),
            pctl_txt or "Final post-adjustment figure", BRICK),
        Box("Model IBNR (selected bases)", fmt_money(model), "Pre-adjustment position", NAVY),
        Box("Judgemental overlay", fmt_signed_money(judged),
            f"{n_adj} origin period(s) adjusted", TEAL),
        Box("vs pure Chain Ladder", fmt_signed_money(booked - cl_tot),
            f"CL total: {fmt_money(cl_tot)}", PURPLE),
    ]


def ultimate_split(ar: pd.DataFrame, by: str = "origin") -> pd.DataFrame:
    """Booked (post-adjustment) ultimate split into paid to date and IBNR.

    by = "origin" gives one row per origin period; "year" sums the origin
    periods within each origin year.  IBNR.Share = IBNR / Ultimate, i.e. the
    share of the booked ultimate not yet paid.
    """
    d = pd.DataFrame({"Origin": ar["Origin"].astype(str).to_numpy(),
                      "Paid": ar["Latest"].to_numpy(dtype=float),
                      "IBNR": ar["Selected.IBNR"].to_numpy(dtype=float),
                      "Ultimate": ar["Selected.Ultimate"].to_numpy(dtype=float)})
    if by == "year":
        d = (d.assign(Origin=d["Origin"].str[:4])
              .groupby("Origin", as_index=False, sort=True)[["Paid", "IBNR", "Ultimate"]].sum())
    elif by != "origin":
        raise ValueError("by must be 'origin' or 'year'")
    return _with_share(d)


def with_total_row(split: pd.DataFrame) -> pd.DataFrame:
    """Append a Total row; its share is total IBNR over total booked ultimate."""
    total = pd.DataFrame({"Origin": ["Total"], "Paid": [split["Paid"].sum()],
                          "IBNR": [split["IBNR"].sum()], "Ultimate": [split["Ultimate"].sum()]})
    return _with_share(pd.concat([split[["Origin", "Paid", "IBNR", "Ultimate"]], total],
                                 ignore_index=True))


def _with_share(d: pd.DataFrame) -> pd.DataFrame:
    ult = d["Ultimate"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        d["IBNR.Share"] = np.where(ult != 0, d["IBNR"].to_numpy(dtype=float) / ult, np.nan)
    return d


def waterfall_steps(ar: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Reserve walk: model total -> largest per-origin judgements -> booked total."""
    model_tot = ar["Model.IBNR"].sum(skipna=False)
    booked = ar["Selected.IBNR"].sum(skipna=False)
    adj = ar.loc[ar["Judgement"].abs() > 1e-9, ["Origin", "Judgement"]]
    adj = adj.iloc[np.argsort(-adj["Judgement"].abs().to_numpy(), kind="stable")]
    if len(adj) > 8:
        adj = pd.concat([adj.iloc[:7], pd.DataFrame({"Origin": ["Other adjustments"],
                                                     "Judgement": [adj["Judgement"].iloc[7:].sum()]})])
    labels = ["Model estimate"] + adj["Origin"].tolist() + ["Booked reserve"]
    amounts = [model_tot] + adj["Judgement"].tolist() + [booked]
    types = ["total"] + ["up" if j >= 0 else "down" for j in adj["Judgement"]] + ["total"]
    ymin, ymax, run = [], [], 0.0
    for amt, typ in zip(amounts, types):
        if typ == "total":
            ymin.append(min(0.0, amt)); ymax.append(max(0.0, amt)); run = amt
        else:
            ymin.append(min(run, run + amt)); ymax.append(max(run, run + amt)); run += amt
    steps = pd.DataFrame({"Label": labels, "Amount": amounts, "Type": types,
                          "ymin": ymin, "ymax": ymax})
    if len(adj) == 0:
        sub = "No judgemental adjustments applied - booked equals the model estimate"
    else:
        sub = f"Net judgemental overlay: {fmt_signed_money(booked - model_tot)}"
    return steps, sub


# ---------------------------------------------------------------------------
# Executive summary
# ---------------------------------------------------------------------------

@dataclass
class ExecSummary:
    boxes: list[Box]
    points: list[Point]
    footer: str


EXEC_FOOTER = ("Figures reflect the current filter and settings. Adjust the prior, credibility "
               "steepness, and seasonality toggle in the sidebar to test sensitivity. This "
               "summary is decision support, not a substitute for actuarial sign-off.")


def exec_summary(res, ar: pd.DataFrame | None) -> ExecSummary:
    total = res.reserve_df[res.reserve_df["Origin"] == "Total"].iloc[0]
    cl_total, cl_se = float(total["IBNR"]), float(total["Mack.S.E"])

    rec_total, rec_label, blend_delta = cl_total, "Chain Ladder", np.nan
    if res.blend is not None:
        t = res.blend.totals
        rec_total = float(t.loc[t["Method"] == "Blended (recommended)", "Total.IBNR"].iloc[0])
        rec_label = "Blended (credibility-weighted)"
        blend_delta = rec_total - cl_total

    boot_50 = boot_75 = boot_95 = np.nan
    if res.boot is not None:
        q = res.boot.quantiles().set_index("Quantile")["IBNR"]
        boot_50, boot_75, boot_95 = q["50%"], q["75%"], q["95%"]

    bt_ok = res.recast is not None
    if bt_ok:
        sm = res.recast.summary.iloc[0]
        bt_mape, bt_pct = sm["MAPE"], sm["Total.Pct"]

    green_txt = "n/a"
    if res.blend is not None:
        d = res.blend.detail
        green = d.iloc[np.argsort(d["Pct.Dev"].to_numpy(), kind="stable")].head(3)
        green_txt = ", ".join(f"{o} ({100 * p:.0f}% developed)"
                              for o, p in zip(green["Origin"], green["Pct.Dev"]))

    seas_txt = "Seasonality not available."
    if res.seas is not None:
        pay = res.seas.index[res.seas.index["Basis"] == "Payment month"]
        if len(pay):
            hi = pay.loc[pay["Index"].idxmax()]
            lo = pay.loc[pay["Index"].idxmin()]
            seas_txt = (f"Payments peak in {hi['Mnth']} (index {hi['Index']:.0f}) and trough in "
                        f"{lo['Mnth']} (index {lo['Index']:.0f}) - a "
                        f"{hi['Index'] - lo['Index']:.0f}-point swing around the average.")

    booked_box = None
    if ar is not None:
        booked = ar["Selected.IBNR"].sum(skipna=False)
        if _finite(booked) and _finite(rec_total) and abs(booked - rec_total) > 0.5:
            sign = "+" if booked >= rec_total else "-"
            booked_box = Box("Booked (after adjustments)", fmt_money(booked),
                             f"Judgemental overlay {sign}{fmt_money(abs(booked - rec_total))} "
                             "vs recommended", BRICK)

    boxes = [Box("Recommended reserve (IBNR)", fmt_money(rec_total), rec_label, NAVY),
             Box("Chain Ladder reserve", fmt_money(cl_total),
                 f"Std. error +/- {fmt_money(cl_se)}", RED)]
    if _finite(boot_95):
        boxes.append(Box("Stress level (95th pct)", fmt_money(boot_95),
                         "Bootstrap adverse scenario", PURPLE))
    if _finite(blend_delta):
        boxes.append(Box("Blend vs CL", ("+" if blend_delta >= 0 else "") + fmt_money(blend_delta),
                         "Effect of stabilising green periods", TEAL))
    if booked_box is not None:
        boxes.append(booked_box)

    pts = []
    change = ""
    if _finite(blend_delta) and abs(blend_delta) > 0:
        change = (f" (a {'reduction' if blend_delta < 0 else 'increase'} of "
                  f"{fmt_money(abs(blend_delta))})")
    pts.append(Point("1. The number to book",
                     f"Hold roughly <b>{fmt_money(rec_total)}</b> in IBNR reserves on this basis. "
                     f"This is the {rec_label} estimate, which keeps standard Chain Ladder for "
                     "mature periods but anchors the newest, most volatile periods to a stable "
                     f"prior. Plain Chain Ladder alone gives <b>{fmt_money(cl_total)}</b>{change}."))
    pts.append(Point("2. Why we don't just use Chain Ladder",
                     "Chain Ladder is dependable for older periods but unstable for the newest "
                     "ones, because it scales a tiny, noisy latest figure up by the full remaining "
                     "development pattern. The least-developed periods here are "
                     f"<b>{green_txt}</b>. These carry most of the estimate's uncertainty and are "
                     "where the blend does its work; treat their raw CL numbers with caution.",
                     ORANGE))
    if _finite(boot_95):
        pts.append(Point("3. The range of outcomes (capital / risk margin)",
                         f"Simulation puts the central estimate near <b>{fmt_money(boot_50)}</b>, "
                         f"with a 1-in-4 adverse outcome around <b>{fmt_money(boot_75)}</b> and a "
                         f"1-in-20 adverse outcome around <b>{fmt_money(boot_95)}</b>. The gap "
                         "between the central and stress figures is the cushion to weigh when "
                         "setting risk margin or capital.", PURPLE))
    if bt_ok:
        if not _finite(bt_mape):
            reliability = "unrated"
        elif bt_mape < 0.05:
            reliability = "strong"
        elif bt_mape < 0.15:
            reliability = "reasonable"
        else:
            reliability = "weak"
        acc = {"strong": GREEN, "reasonable": AMBER, "weak": RED}.get(reliability, GREY)
        if reliability == "weak":
            tail = ("The model has missed recent actuals by a wide margin - lean harder on the "
                    "blended/stabilised figure and consider expert overlay.")
        elif reliability == "reasonable":
            tail = ("The model tracks recent actuals acceptably; the headline numbers are usable "
                    "with normal review.")
        else:
            tail = ("The model has predicted recent actuals closely, which supports confidence "
                    "in the headline numbers.")
        pts.append(Point("4. How much to trust the model",
                         f"Back-testing on recently held-back data shows <b>{reliability}</b> "
                         f"reliability (average error {fmt_pct(bt_mape)}, and a net bias of "
                         f"{fmt_pct(bt_pct)} versus actuals). {tail}", acc))
    else:
        pts.append(Point("4. How much to trust the model",
                         "The back-test could not run on this dataset (the triangle is too small "
                         "once recent data is held back). Without it, treat the estimates as "
                         "directional and prioritise the stabilised blend.", GREY))
    pts.append(Point("5. Timing / cash-flow consideration", seas_txt, NAVY))
    if res.blend is not None:
        tot = res.blend.totals["Total.IBNR"]
        spread = tot.max(skipna=False) - tot.min(skipna=False)
        rel = spread / abs(rec_total) if rec_total != 0 else np.nan
        if not _finite(rel):
            agree_txt, acc = "", GREY
        elif rel < 0.10:
            agree_txt, acc = ("The methods broadly agree, which raises confidence in the "
                              "booked figure."), GREEN
        elif rel < 0.25:
            agree_txt, acc = ("The methods show moderate spread; the choice of method has a "
                              "real but manageable effect."), AMBER
        else:
            agree_txt, acc = ("The methods disagree materially - the final number is sensitive "
                              "to method choice and warrants explicit sign-off."), RED
        pts.append(Point("6. Method agreement",
                         f"Across all methods tried, total IBNR ranges over about "
                         f"<b>{fmt_money(spread)}</b> (roughly {fmt_pct(rel)} of the recommended "
                         f"figure). {agree_txt}", acc))
    return ExecSummary(boxes, pts, EXEC_FOOTER)
