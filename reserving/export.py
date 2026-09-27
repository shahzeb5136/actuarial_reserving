"""Excel export: the same workbook (sheet names, columns, order) the R app's
download button produced."""
from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

from .adjustments import adjustment_log
from .formatting import fmt_money

FINAL_SELECTION_COLS = ["Origin", "Latest", "Pct.Dev", "Basis", "Base.Ultimate", "AdjPct",
                        "AdjAmt", "Override", "Selected.Ultimate", "Selected.IBNR",
                        "Judgement", "Comment"]


def export_filename(sel: str, on: date | None = None) -> str:
    on = on or date.today()
    return f"Reserving_Results_{re.sub(r'[^A-Za-z0-9_-]+', '_', sel)}_{on.isoformat()}.xlsx"


def workbook_sheets(res, ar: pd.DataFrame | None, signoff: dict | None = None,
                    export_date: date | None = None) -> dict[str, pd.DataFrame]:
    signoff = signoff or {}
    export_date = export_date or date.today()
    sheets: dict[str, pd.DataFrame] = {
        "Cumulative Triangle": res.cum_df,
        "Incremental Triangle": res.incr_df,
        "Dev Factors": res.dev_df,
        "Reserve Estimates": res.reserve_df,
        "Method Comparison": res.method_cmp,
    }
    if res.boot is not None:
        sheets["Bootstrap Quantiles"] = res.boot.quantiles()
        sheets["Bootstrap by Origin"] = res.boot.by_origin()
    if res.bf is not None:
        sheets["Bornhuetter-Ferguson"] = res.bf
    if res.blend is not None:
        sheets["Blended by Origin"] = res.blend.detail
        sheets["Blended Method Totals"] = res.blend.totals
    if res.recast is not None:
        sheets["Recast Summary"] = res.recast.summary
        sheets["Recast Detail"] = res.recast.detail
    if ar is not None:
        sheets["Final Selection"] = ar[FINAL_SELECTION_COLS]
        log = adjustment_log(ar).rename(columns={"Last edited": "LastEdited"})
        if len(log):
            sheets["Adjustment Log"] = log
        sheets["Sign-off"] = pd.DataFrame({
            "Field": ["Reviewing actuary", "Role / credential", "Overall rationale",
                      "Model total IBNR", "Booked total IBNR", "Judgemental overlay",
                      "Export date"],
            "Value": [str(signoff.get("name") or ""), str(signoff.get("role") or ""),
                      str(signoff.get("notes") or ""),
                      fmt_money(ar["Model.IBNR"].sum(skipna=False)),
                      fmt_money(ar["Selected.IBNR"].sum(skipna=False)),
                      fmt_money(ar["Judgement"].sum(skipna=False)),
                      export_date.isoformat()],
        })
    if res.seas is not None:
        sheets["Seasonality Index"] = res.seas.index
    if res.exp_al is not None:
        sheets["Exposure Aligned"] = res.exp_al.aligned
        if res.blend is not None and res.blend.has_exposure:
            sheets["A-priori Loss Ratios"] = pd.DataFrame({
                "Basis": ["Earned premium (BF / Benktander)", "Exposure (Cape Cod)"],
                "LossRatio": [res.blend.elr_premium, res.blend.elr_capecod]})
    return sheets


def build_workbook(res, ar: pd.DataFrame | None, signoff: dict | None = None,
                   export_date: date | None = None) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in workbook_sheets(res, ar, signoff, export_date).items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for col_cells in ws.columns:          # readable column widths
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells[:200])
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(10, width + 2), 60)
    return buf.getvalue()
