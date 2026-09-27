"""Reading and validating the claims workbook and the optional exposure table.

Ports the Shiny app's raw_data_reactive, parse_period_cell,
read_exposure_table and align_exposure.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from .triangles import date_labels, floor_period

REQUIRED_COLUMNS = ("OriginDate", "PaymentDate", "ClaimAmount", "SubCat")
ALL_CATEGORIES = "All Categories"
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


class DataError(ValueError):
    """A problem with an uploaded file, phrased for the end user."""


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

def read_claims(source) -> pd.DataFrame:
    """Read the first sheet of a claims workbook and clean it."""
    try:
        raw = pd.read_excel(source, sheet_name=0)
    except Exception as exc:  # noqa: BLE001 - surface any reader problem to the user
        raise DataError(f"Could not read the claims workbook: {exc}") from exc
    return clean_claims(raw)


def clean_claims(raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce types and drop unusable rows.

    Drops rows with a missing OriginDate, PaymentDate or ClaimAmount and any
    record paid before it originated. Extra columns are kept as-is.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise DataError("Missing required columns. Need: " + ", ".join(REQUIRED_COLUMNS))
    df = raw.copy()
    df["OriginDate"] = _as_date(df["OriginDate"])
    df["PaymentDate"] = _as_date(df["PaymentDate"])
    df["ClaimAmount"] = pd.to_numeric(df["ClaimAmount"], errors="coerce").astype(float)
    df["SubCat"] = df["SubCat"].map(_subcat_label).astype(object)
    keep = (df["OriginDate"].notna() & df["PaymentDate"].notna()
            & df["ClaimAmount"].notna() & (df["PaymentDate"] >= df["OriginDate"]))
    df = df.loc[keep].reset_index(drop=True)
    if df.empty:
        raise DataError("No valid rows after cleaning.")
    return df


def _as_date(col: pd.Series) -> pd.Series:
    """Coerce a column to midnight timestamps (R's as.Date)."""
    if pd.api.types.is_datetime64_any_dtype(col):
        out = pd.to_datetime(col)
    elif pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
        # Plain numbers in a date column are Excel serial dates.
        out = pd.to_datetime(col, unit="D", origin=_EXCEL_EPOCH, errors="coerce")
    else:
        out = pd.to_datetime(col, errors="coerce", format="mixed")
    return out.dt.normalize()


def _subcat_label(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v)


def subcat_choices(df: pd.DataFrame) -> list[str]:
    """'All Categories' followed by the sub-categories present, sorted."""
    levels = {s for s in df["SubCat"] if s is not None}
    return [ALL_CATEGORIES] + sorted(levels, key=lambda s: (s.lower(), s))


def filter_subcat(df: pd.DataFrame, sel: str) -> pd.DataFrame:
    if sel == ALL_CATEGORIES:
        return df
    mask = np.array([s == sel for s in df["SubCat"]], dtype=bool)
    return df.loc[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Exposure / earned premium table
# ---------------------------------------------------------------------------

_PERIOD_COLS = ("Period", "OriginPeriod", "Origin", "OriginDate", "AccidentPeriod",
                "Month", "Quarter")
_PREMIUM_COLS = ("EarnedPremium", "EarnedPrem", "Premium", "EP")
_EXPOSURE_COLS = ("Exposure", "Exposures", "Exp", "Units", "EarnedExposure")

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]


def _month_from_name(token: str) -> int | None:
    t = token.lower().rstrip(".")
    if len(t) < 3:
        return None
    for i, name in enumerate(_MONTH_NAMES, start=1):
        if name.startswith(t):
            return i
    return None


def _year(token: str) -> int:
    y = int(token)
    if len(token) == 2:            # two-digit years: 00-68 -> 20xx, 69-99 -> 19xx
        y += 2000 if y <= 68 else 1900
    return y


def _make_date(y: int, m: int, d: int = 1) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(year=y, month=m, day=d)
    except ValueError:
        return None


def _number_text(x: float) -> str:
    """How R's as.character() prints a number (no trailing .0 on integers)."""
    x = float(x)
    return str(int(x)) if x.is_integer() else repr(x)


def parse_period_cell(x) -> pd.Timestamp | None:
    """Parse one Period cell into a date.

    Accepts real dates, Excel serial numbers, quarters ('2021 Q1', 'Q1-2021',
    '2021Q1'), month names ('Jan-2021', 'January 2021', '2021 Jan'),
    year-month ('2021-01', '2021/01', '202101') and full dates ('2021-01-15',
    '2021/1/1', '20210115'). Returns None when the cell is blank or not
    recognisable (the row is then dropped).
    """
    if x is None:
        return None
    if isinstance(x, (pd.Timestamp, datetime, date, np.datetime64)):
        ts = pd.Timestamp(x)
        return None if pd.isna(ts) else ts.normalize()
    if isinstance(x, (bool, np.bool_)):
        return None
    if isinstance(x, (int, float, np.integer, np.floating)):
        if not np.isfinite(x):
            return None
        s = _number_text(x)
    else:
        if pd.isna(x):
            return None
        s = str(x).strip()
    if not s:
        return None

    # Excel serial number (a plausible modern date)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", s):
        n = float(s)
        if 30000 < n < 80000:
            return _EXCEL_EPOCH + pd.Timedelta(days=math.floor(n))

    # Quarters: 2021 Q1, 2021-Q1, 2021Q1, Q1 2021, Q1-2021
    m = re.fullmatch(r"(\d{4})\s*[- ]?q\s*([1-4])", s, flags=re.I)
    if m:
        return _make_date(int(m.group(1)), (int(m.group(2)) - 1) * 3 + 1)
    m = re.fullmatch(r"q\s*([1-4])\s*[- ]?(\d{4})", s, flags=re.I)
    if m:
        return _make_date(int(m.group(2)), (int(m.group(1)) - 1) * 3 + 1)

    # Month names: Jan-2021, Jan 2021, January 2021, Jan2021, 2021 Jan, 2021-Jan
    m = re.fullmatch(r"([A-Za-z]+\.?)[-\s/]*(\d{4}|\d{2})", s)
    if m and _month_from_name(m.group(1)):
        return _make_date(_year(m.group(2)), _month_from_name(m.group(1)))
    m = re.fullmatch(r"(\d{4})[-\s/]*([A-Za-z]+\.?)", s)
    if m and _month_from_name(m.group(2)):
        return _make_date(int(m.group(1)), _month_from_name(m.group(2)))

    # Full dates: 2021-01-15, 2021/1/1, 2021.01.15, 20210115
    m = (re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
         or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s))
    if m:
        return _make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Year-month: 2021-01, 2021/01, 2021.01, 202101
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s) or re.fullmatch(r"(\d{4})(\d{2})", s)
    if m:
        return _make_date(int(m.group(1)), int(m.group(2)))

    # Anything else a general date parser understands (e.g. 01/02/2021).
    if re.search(r"\d{4}", s) and re.search(r"[-/.\s]", s):
        ts = pd.to_datetime(s, errors="coerce")
        if not pd.isna(ts):
            return ts.normalize()
    return None


def _norm_name(name) -> str:
    return re.sub(r"[^a-z]", "", str(name).lower())


def _find_col(columns, candidates) -> str | None:
    """First column (in file order) whose normalised name matches a candidate."""
    wanted = {_norm_name(c) for c in candidates}
    for col in columns:
        if _norm_name(col) in wanted:
            return col
    return None


def read_exposure_table(source) -> pd.DataFrame:
    """Read + validate an exposure workbook -> Period, PeriodDate, EarnedPremium, Exposure."""
    try:
        ex = pd.read_excel(source, sheet_name=0)
    except Exception as exc:  # noqa: BLE001
        raise DataError(f"Could not read the exposure workbook: {exc}") from exc
    return tidy_exposure_table(ex)


def tidy_exposure_table(ex: pd.DataFrame) -> pd.DataFrame:
    c_period = _find_col(ex.columns, _PERIOD_COLS)
    c_prem = _find_col(ex.columns, _PREMIUM_COLS)
    c_exp = _find_col(ex.columns, _EXPOSURE_COLS)
    if c_period is None:
        raise DataError("Exposure file needs a 'Period' column (origin period).")
    if c_prem is None and c_exp is None:
        raise DataError("Exposure file needs at least an 'EarnedPremium' or 'Exposure' column.")

    n = len(ex)
    prem = (pd.to_numeric(ex[c_prem], errors="coerce").to_numpy(dtype=float)
            if c_prem is not None else np.full(n, np.nan))
    expo = (pd.to_numeric(ex[c_exp], errors="coerce").to_numpy(dtype=float)
            if c_exp is not None else np.full(n, np.nan))
    # If only one of the two is given, mirror it so both methods can run.
    if np.all(np.isnan(prem)):
        prem = expo.copy()
    if np.all(np.isnan(expo)):
        expo = prem.copy()

    pdates = [parse_period_cell(v) for v in ex[c_period].tolist()]
    out = pd.DataFrame({
        "Period": [d.strftime("%Y-%m-%d") if d is not None else None for d in pdates],
        "PeriodDate": pd.to_datetime(pd.Series(pdates, dtype=object)),
        "EarnedPremium": prem,
        "Exposure": expo,
    })
    out = out[out["PeriodDate"].notna()].reset_index(drop=True)
    if out.empty:
        raise DataError("No rows in the exposure file had a recognisable Period value.")
    return out


@dataclass(frozen=True)
class ExposureAlignment:
    earned_premium: np.ndarray   # aligned to triangle rows, NaN where unmatched
    exposure: np.ndarray
    matched: int
    n_origins: int
    aligned: pd.DataFrame        # Origin, EarnedPremium, Exposure


def align_exposure(exposure_df: pd.DataFrame, origin_dates, period: str = "quarter") -> ExposureAlignment:
    """Match exposure periods to triangle origins at the triangle's granularity.

    Both sides are floored to month / quarter starts, so a monthly exposure
    table aggregates up to a quarterly triangle.
    """
    keys = floor_period(exposure_df["PeriodDate"], period)
    grouped = (pd.DataFrame({"Key": keys,
                             "EarnedPremium": exposure_df["EarnedPremium"].to_numpy(dtype=float),
                             "Exposure": exposure_df["Exposure"].to_numpy(dtype=float)})
                 .groupby("Key")[["EarnedPremium", "Exposure"]].sum())   # NaN-skipping, all-NaN -> 0
    origins = pd.DatetimeIndex(origin_dates)
    key_org = floor_period(origins, period)
    ep = grouped["EarnedPremium"].reindex(key_org).to_numpy(dtype=float)
    exo = grouped["Exposure"].reindex(key_org).to_numpy(dtype=float)
    matched = int(key_org.isin(grouped.index).sum())
    aligned = pd.DataFrame({"Origin": date_labels(origins), "EarnedPremium": ep, "Exposure": exo})
    return ExposureAlignment(ep, exo, matched, len(origins), aligned)
