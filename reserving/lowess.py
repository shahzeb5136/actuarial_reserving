"""R's lowess() scatterplot smoother (Cleveland 1979), ported from R's
src/library/stats/src/lowess.c (clowess) so the trend lines on the residual
diagnostics match R's plot.MackChainLadder."""
from __future__ import annotations

import numpy as np


def _lowest(x, y, xs, nleft, nright, rw, use_rw):
    """Locally weighted linear fit at xs using points nleft..(ties past nright)."""
    n = x.size
    rng = x[-1] - x[0]
    h = max(xs - x[nleft], x[nright] - xs)
    h9, h1 = 0.999 * h, 0.001 * h
    # contiguous window starting at nleft, stopping at the first point right of xs + h9
    stop = nleft + np.searchsorted(x[nleft:], xs + h9, side="right")
    stop = min(max(stop, nleft), n)
    idx = np.arange(nleft, stop)
    r = np.abs(x[idx] - xs)
    w = np.where(r <= h1, 1.0, (1.0 - (r / h) ** 3) ** 3) if h > 0 else np.ones(idx.size)
    w = np.where(r <= h9, w, 0.0)
    if use_rw:
        w = w * rw[idx]
    a = w.sum()
    if a <= 0:
        return None
    w = w / a
    if h > 0:
        xbar = np.sum(w * x[idx])
        b = xs - xbar
        c = np.sum(w * (x[idx] - xbar) ** 2)
        if np.sqrt(c) > 0.001 * rng:
            w = w * (b / c * (x[idx] - xbar) + 1.0)
    return float(np.sum(w * y[idx]))


def lowess(x, y, f: float = 2 / 3, iterations: int = 3, delta: float | None = None):
    """Returns (x_sorted, fitted) like R's lowess(x, y, f, iter, delta)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    n = x.size
    if n < 2:
        return x, y.copy()
    if delta is None:
        delta = 0.01 * (x[-1] - x[0])
    ns = max(2, min(n, int(f * n + 1e-7)))
    ys = np.zeros(n)
    rw = np.ones(n)
    for it in range(1, iterations + 2):
        nleft, nright, last, i = 0, ns - 1, -1, 0
        while True:
            if nright < n - 1:
                d1 = x[i] - x[nleft]
                d2 = x[nright + 1] - x[i]
                if d1 > d2:
                    nleft += 1
                    nright += 1
                    continue
            fit = _lowest(x, y, x[i], nleft, nright, rw, it > 1)
            ys[i] = y[i] if fit is None else fit
            if last < i - 1:
                denom = x[i] - x[last]
                for j in range(last + 1, i):
                    alpha = (x[j] - x[last]) / denom
                    ys[j] = alpha * ys[i] + (1 - alpha) * ys[last]
            last = i
            cut = x[last] + delta
            i = last + 1
            while i < n:
                if x[i] > cut:
                    break
                if x[i] == x[last]:
                    ys[i] = ys[last]
                    last = i
                i += 1
            i = max(last + 1, i - 1)
            if last >= n - 1:
                break
        res = y - ys
        sc = np.mean(np.abs(res))
        if it > iterations:
            break
        cmad = 6 * np.median(np.abs(res))
        if cmad < 1e-7 * sc:
            break
        c9, c1 = 0.999 * cmad, 0.001 * cmad
        r = np.abs(res)
        rw = np.where(r <= c1, 1.0, np.where(r <= c9, (1 - (r / cmad) ** 2) ** 2, 0.0))
    return x, ys
