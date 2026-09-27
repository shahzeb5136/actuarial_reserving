"""Helpers for comparing the Python app with the R reference outputs."""
from __future__ import annotations

import html
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import openpyxl

from reserving.adjustments import (adjusted_reserves, apply_basis_to_all, apply_cell_edit,
                                   default_basis, initial_state, method_ultimates)
from reserving.bootstrap import BootstrapResult
from reserving.data import read_exposure_table
from reserving.pipeline import AnalysisSettings, method_comparison, run_analysis

TESTS_DIR = Path(__file__).resolve().parent
REF_DIR = TESTS_DIR / "reference"
PROJECT_DIR = TESTS_DIR.parent / "archive" / "r_shiny_app"

RTOL, ATOL = 1e-9, 1e-6


def load_ref(name: str) -> dict:
    return json.loads((REF_DIR / f"{name}.json").read_text(encoding="utf-8"))


def scenario_ids() -> list[str]:
    skip = ("bootstrap_", "exposure_parsing", "lowess")
    return sorted(p.stem for p in REF_DIR.glob("*.json") if not p.stem.startswith(skip))


def arr(x) -> np.ndarray:
    """JSON (with nulls) -> float array."""
    return np.array([[np.nan if v is None else v for v in row] if isinstance(row, list)
                     else (np.nan if row is None else row) for row in x], dtype=float)


# ---------------------------------------------------------------------------
# Re-running a scenario in Python
# ---------------------------------------------------------------------------

@dataclass
class Replay:
    res: object
    state: object
    ar: object
    edit_messages: list = field(default_factory=list)
    exposure: object = None


def replay(ref: dict, claims) -> Replay:
    s = ref["settings"]
    settings = AnalysisSettings(subcat=s["subcat"], period=s["period"], tail=float(s["tail"]),
                                n_sims=50, boot_dist=s["boot_dist"], n_holdback=int(s["n_holdback"]),
                                blend_prior=s["prior"], blend_kappa=float(s["kappa"]),
                                blend_seasonal=bool(s["seasonal"]), seed=0)
    exposure = None
    if s.get("exposure"):
        base = TESTS_DIR if s["exposure"].startswith("fixtures/") else PROJECT_DIR
        exposure = read_exposure_table(base / s["exposure"])
    res = run_analysis(claims, settings, exposure)

    # Swap in R's simulated IBNR so everything downstream of the bootstrap can
    # be compared exactly (the simulation engine is tested separately).
    if ref.get("boot") is not None and res.boot is not None:
        ib = arr(ref["boot"]["ibnr_by_origin"])
        res.boot = BootstrapResult(res.boot.origins, res.boot.latest, ib, ref["boot"]["process_distr"])
        res.method_cmp = method_comparison(res.mack, res.boot, settings.boot_dist, res.bf,
                                           res.bf_label, res.capecod, res.exposure_vec is not None)

    mu = method_ultimates(res)
    default = default_basis(res)
    state = initial_state(mu["Origin"], default)
    msgs = []
    if s.get("bulk_basis"):
        state, m = apply_basis_to_all(state, s["bulk_basis"])
        msgs += m
    for e in ref["edits"]:
        state, m = apply_cell_edit(state, int(e["row"]) - 1, e["col"], e["value"])
        msgs += m
    return Replay(res, state, adjusted_reserves(state, mu, default), msgs, exposure)


# ---------------------------------------------------------------------------
# Workbook comparison
# ---------------------------------------------------------------------------

def read_workbook(src) -> dict[str, list[list]]:
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    wb = openpyxl.load_workbook(src, data_only=True, read_only=True)
    out = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    wb.close()
    return out


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v == "") or (isinstance(v, float) and math.isnan(v))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass
class SheetDiff:
    sheet: str
    cells: int = 0
    numeric_cells: int = 0
    max_rel_diff: float = 0.0
    mismatches: list = field(default_factory=list)


def compare_cell(a, b) -> tuple[bool, float]:
    """(equal?, relative difference) for one R cell `a` vs Python cell `b`."""
    if _blank(a) and _blank(b):
        return True, 0.0
    if _num(a) and _num(b):
        a, b = float(a), float(b)
        if a == b:
            return True, 0.0
        scale = max(abs(a), abs(b))
        # values that are zero to within ATOL (e.g. 0 vs 7e-17) are compared absolutely
        rel = abs(a - b) / scale if scale > ATOL else 0.0
        return bool(np.isclose(b, a, rtol=RTOL, atol=ATOL)), rel
    return str(a) == str(b), (0.0 if str(a) == str(b) else math.inf)


def compare_workbooks(r_book: dict, py_book: dict, exceptions=None) -> list[SheetDiff]:
    """exceptions: {(sheet, row_label, column): callable(r_value, py_value) -> bool}."""
    exceptions = exceptions or {}
    diffs = []
    for name in r_book:
        r_rows, p_rows = r_book[name], py_book.get(name)
        d = SheetDiff(name)
        diffs.append(d)
        if p_rows is None:
            d.mismatches.append("sheet missing in Python export")
            continue
        if r_rows[0] != p_rows[0]:
            d.mismatches.append(f"header differs: R={r_rows[0]} Py={p_rows[0]}")
            continue
        if len(r_rows) != len(p_rows):
            d.mismatches.append(f"row count differs: R={len(r_rows)} Py={len(p_rows)}")
            continue
        header = r_rows[0]
        for ri, (rr, pr) in enumerate(zip(r_rows[1:], p_rows[1:]), start=1):
            for col, a, b in zip(header, rr, pr):
                d.cells += 1
                rule = exceptions.get((name, rr[0], col))
                if rule is not None:
                    if not rule(a, b):
                        d.mismatches.append(f"row {ri} ({rr[0]}) {col}: R={a!r} Py={b!r} (exception rule)")
                    continue
                ok, rel = compare_cell(a, b)
                if _num(a) and _num(b):
                    d.numeric_cells += 1
                    d.max_rel_diff = max(d.max_rel_diff, rel)
                if not ok:
                    d.mismatches.append(f"row {ri} ({rr[0]}) {col}: R={a!r} Py={b!r}")
    for name in py_book:
        if name not in r_book:
            diffs.append(SheetDiff(name, mismatches=["extra sheet in Python export"]))
    return diffs


# ---------------------------------------------------------------------------
# Text comparison
# ---------------------------------------------------------------------------

def html_text(s: str) -> str:
    """Rendered HTML -> normalised visible text."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([.,;:)])", r"\1", s)


def boxes_text(boxes) -> str:
    return " ".join(f"{b.title} {b.value} {b.sub}" for b in boxes)


def exec_text(es) -> str:
    return html_text(boxes_text(es.boxes) + " Key points for the decision "
                     + " ".join(f"{p.head} {p.body}" for p in es.points) + " " + es.footer)


def alerts_text(alerts) -> str:
    return html_text(" ".join(f"{a.title} {a.body}" for a in alerts if a is not None))


def export_date(ref: dict) -> date:
    return date.fromisoformat(ref["export_date"])
