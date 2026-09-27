"""Number formatting that reproduces the R app's text output exactly."""
from __future__ import annotations

import math


def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def fmt_money(x) -> str:
    """R: format(round(x), big.mark = ",", scientific = FALSE); 'n/a' if not finite."""
    if not _finite(x):
        return "n/a"
    return f"{int(round(float(x))):,}"


def fmt_signed_money(x) -> str:
    """'+1,234' / '-1,234' (the sign is always shown)."""
    if not _finite(x):
        return "n/a"
    return ("+" if float(x) >= 0 else "-") + fmt_money(abs(float(x)))


def fmt_pct(x, digits: int = 1) -> str:
    """R: formatC(100 * x, format = "f", digits = d) followed by '%'."""
    if not _finite(x):
        return "n/a"
    return f"{100 * float(x):.{digits}f}%"


def fmt_count(n) -> str:
    return f"{int(n):,}"
