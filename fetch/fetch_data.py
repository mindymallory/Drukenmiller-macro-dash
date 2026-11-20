"""Build site/data/dashboard.json for the Druckenmiller macro map.

Each indicator is reduced to one metric m in natural units (a rate gap in
points, a spread change in bp, a % distance from the 200-day average, ...).
Its score is s = clip((m - center) / scale, -1, 1): +1 is a full tailwind for
risk assets, -1 a full headwind. Scores are combined with weights into a
composite and a stance. TILES below holds the default center, scale and
weight of every signal; the Guide tab on the site explains each one, and the
Backtest tab lets you change them in the browser.

History: every metric is recomputed at each month-end since 2000 using only
data that would have been published by then (approximate release lags, see
LAG). Those monthly metrics, plus realized forward S&P 500 total returns, are
written to the JSON so the site can recompute scores and backtests client-side.

Data sources (all free):
  * FRED. Uses the official API when FRED_API_KEY is set (a GitHub Actions
    secret, never committed); otherwise FRED's public fredgraph.csv download.
  * Yahoo Finance via yfinance (no key): futures, ETFs, indices, S&P 500
    total return index and S&P 500 constituents (for breadth).
  * S&P 500 constituent list from the datasets/s-and-p-500-companies repo,
    with Wikipedia as a fallback.

Every series is fetched independently; failures are listed in "errors" and the
affected tiles show as unavailable. The build fails only if no tile scores.
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path(__file__).resolve().parents[1] / "site" / "data" / "dashboard.json"
UA = {"User-Agent": "macro-dashboard/1.0 (GitHub Actions; personal research dashboard)"}
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
TODAY = pd.Timestamp(datetime.now(timezone.utc).date())
FRED_START = pd.Timestamp("1997-01-01")
MKT_START = "1998-01-01"
BT_START = pd.Timestamp("2000-01-01")

errors: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- fetchers --

def fred(series_id: str, start: pd.Timestamp = FRED_START) -> pd.Series:
    start_s = start.strftime("%Y-%m-%d")
    last_err = None
    for attempt in range(3):
        try:
            if FRED_KEY:
                r = requests.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={"series_id": series_id, "api_key": FRED_KEY,
                            "file_type": "json", "observation_start": start_s},
                    headers=UA, timeout=30)
                r.raise_for_status()
                obs = r.json()["observations"]
                s = pd.Series({pd.Timestamp(o["date"]): o["value"] for o in obs})
            else:
                r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                                 params={"id": series_id, "cosd": start_s},
                                 headers=UA, timeout=30)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                s = pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]))
            s = pd.to_numeric(s.replace(".", np.nan), errors="coerce").dropna().sort_index()
            if s.empty:
                raise ValueError("no observations returned")
            s.name = series_id
            log(f"FRED {series_id}: {len(s)} obs, {s.index[0].date()} to {s.index[-1].date()}")
            return s
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"FRED {series_id}: {last_err}")


def yahoo_close(tickers: list[str], start: str = MKT_START) -> pd.DataFrame:
    import yfinance as yf

    frames = []
    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start, interval="1d",
                                 auto_adjust=True, progress=False, threads=True)
                close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
                if not isinstance(df.columns, pd.MultiIndex):
                    close.columns = chunk
                frames.append(close)
                break
            except Exception as e:  # noqa: BLE001
                log(f"yahoo chunk {i} attempt {attempt}: {e}")
                time.sleep(5 * (attempt + 1))
        time.sleep(1.5)
    if not frames:
        raise RuntimeError("Yahoo: no data returned")
    out = pd.concat(frames, axis=1)
    out = out.loc[:, ~out.columns.duplicated()]
    # Threaded downloads occasionally drop a ticker ("database is locked" from
    # yfinance's cache). Retry those one at a time.
    missing = [t for t in tickers if t not in out.columns or out[t].dropna().empty]
    for t in missing:
        try:
            df = yf.download(t, start=start, interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            c = df["Close"]
            c = c[t] if isinstance(c, pd.DataFrame) else c
            if not c.dropna().empty:
                out[t] = c
                log(f"yahoo retry {t}: ok")
        except Exception as e:  # noqa: BLE001
            log(f"yahoo retry {t}: {e}")
        time.sleep(0.5)
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    return out.sort_index()


CBOE_YAHOO = {"VIX": "^VIX", "VIX3M": "^VIX3M", "VIX6M": "^VIX6M", "VIX1Y": "^VIX1Y"}


def cboe_index(name: str) -> pd.Series:
    """Daily close of a Cboe volatility index from Cboe's own history file, Yahoo as fallback."""
    try:
        r = requests.get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv",
                         headers=UA, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        cols = {c.strip().upper(): c for c in df.columns}
        vcol = cols.get("CLOSE") or cols.get(name.upper()) or df.columns[-1]
        s = pd.Series(pd.to_numeric(df[vcol], errors="coerce").values,
                      index=pd.to_datetime(df[df.columns[0]], errors="coerce"))
        s = s[s.index.notna()].dropna().sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if s.empty:
            raise ValueError("empty file")
        log(f"Cboe {name}: {len(s)} obs, {s.index[0].date()} to {s.index[-1].date()}")
        return s
    except Exception as e:  # noqa: BLE001
        log(f"Cboe {name} file failed ({e}); trying Yahoo {CBOE_YAHOO[name]}")
    c = yahoo_close([CBOE_YAHOO[name]], start="2000-01-01")
    s = clean(c[CBOE_YAHOO[name]])
    if s is None:
        raise RuntimeError(f"{name}: no data from Cboe or Yahoo")
    log(f"Yahoo {CBOE_YAHOO[name]}: {len(s)} obs, {s.index[0].date()} to {s.index[-1].date()}")
    return s


def sp500_tickers() -> list[str]:
    u = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    try:
        r = requests.get(u, headers=UA, timeout=30)
        r.raise_for_status()
        syms = pd.read_csv(io.StringIO(r.text))["Symbol"].astype(str).tolist()
        if len(syms) > 400:
            return [s.replace(".", "-") for s in syms]
    except Exception as e:  # noqa: BLE001
        log(f"constituents {u}: {e}")
    tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                          storage_options=UA)
    return [s.replace(".", "-") for s in tables[0]["Symbol"].astype(str).tolist()]


# ----------------------------------------------------------------- helpers --

def safe(fn, label):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        msg = f"{label}: {e}"
        log("ERROR " + msg)
        traceback.print_exc()
        errors.append(msg)
        return None


def clean(s):
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return s if not s.empty else None


def clip(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def yoy(s, periods):
    return None if s is None else ((s / s.shift(periods) - 1.0) * 100.0).dropna()


def ma(s, n=200):
    return None if s is None else s.rolling(n).mean()


# Approximate publication lag in days from the date FRED stamps on the value
# (start of the month/quarter for monthly/quarterly data).
LAG = {"GDP": 120, "PCEPILFE": 60}


def avail(s, asof, lag_days=1):
    """Observations that would have been public on `asof`."""
    if s is None:
        return None
    s = s[s.index + pd.Timedelta(days=lag_days) <= asof]
    return s if not s.empty else None


def value_at(s, when):
    s = s[s.index <= when]
    return None if s.empty else (float(s.iloc[-1]), s.index[-1])


def d(ts):
    return f"{ts:%b} {ts.day}, {ts.year}"


# ------------------------------------------------------------- the signals --
# (id, group, name, weight, center, scale, unit, slider range for center, for |scale|)
# score = clip((metric - center) / scale). A negative scale means a higher
# metric is a headwind. Weights sum to 100. Rationale in the Guide tab.
TILES = [
    ("policy_gap", "Liquidity & the Fed", "Policy vs growth", 15, 0.0, -2.0, "pts", (-3, 3), (0.5, 5)),
    ("fed_path", "Liquidity & the Fed", "Fed direction", 12, 0.0, -1.0, "pts", (-1.5, 1.5), (0.25, 3)),
    ("net_liq", "Liquidity & the Fed", "Fed liquidity", 8, 0.0, 1.5, "sd", (-1.5, 1.5), (0.5, 4)),
    ("inflation", "Liquidity & the Fed", "Inflation pressure", 8, 2.5, -1.0, "%", (1.5, 4), (0.25, 3)),
    ("hy", "Credit", "High-yield spreads", 10, 0.0, -50.0, "bp", (-50, 50), (10, 200)),
    ("baa", "Credit", "Corporate (Baa) spreads", 5, 0.0, -25.0, "bp", (-40, 40), (5, 100)),
    ("ad", "Market internals", "A/D line confirms", 8, 30.0, -30.0, "days", (5, 120), (10, 120)),
    ("pct200", "Market internals", "Participation", 6, 50.0, 25.0, "%", (30, 70), (5, 50)),
    ("cycdef", "Market internals", "Cyclicals vs defensives", 7, 0.0, 4.0, "%", (-5, 5), (1, 15)),
    ("ewcw", "Market internals", "Equal vs cap weight", 3, 0.0, 3.0, "%", (-4, 4), (0.5, 10)),
    ("copper", "Commodities & dollar", "Copper", 6, 0.0, 10.0, "%", (-10, 10), (2, 30)),
    ("commod", "Commodities & dollar", "Broad commodities", 5, 0.0, 7.0, "%", (-10, 10), (2, 25)),
    ("cugold", "Commodities & dollar", "Copper / gold", 3, 0.0, 8.0, "%", (-10, 10), (2, 30)),
    ("dollar", "Commodities & dollar", "Dollar", 4, 0.0, -4.0, "%", (-5, 5), (1, 12)),
]
assert sum(t[3] for t in TILES) == 100
STATE_CUT = 0.2
TILE_IDS = [t[0] for t in TILES]


def score_of(tid, m):
    t = next(x for x in TILES if x[0] == tid)
    return None if m is None else clip((m - t[4]) / t[5])


def state_of(s):
    if s is None:
        return "unavailable"
    return "tailwind" if s >= STATE_CUT else "headwind" if s <= -STATE_CUT else "neutral"


def make_metrics(F, Y, B):
    """Each function returns (metric, reading) as of a date, or None."""

    def policy_gap(asof):
        ff = avail(F["effr"], asof)
        gdp = avail(F["gdp"], asof, LAG["GDP"])
        if ff is None or gdp is None or len(gdp) < 5:
            return None
        ffm = float(ff.iloc[-30:].mean())
        n = float(yoy(gdp, 4).iloc[-1])
        g = ffm - n
        return g, f"Fed funds {ffm:.2f}% vs nominal GDP growth {n:.1f}%: gap {g:+.1f} pts"

    def fed_path(asof):
        two, ff = avail(F["dgs2"], asof), avail(F["effr"], asof)
        if two is None or ff is None:
            return None
        ffm = float(ff.iloc[-30:].mean())
        prev = ff[ff.index <= asof - pd.DateOffset(months=6)]
        if len(prev) < 30:
            return None
        move = ffm - float(prev.iloc[-30:].mean())
        spread = float(two.iloc[-1]) - ffm
        m = spread + move
        return m, f"2-yr Treasury {spread * 100:+.0f} bp vs fed funds; fed funds {move * 100:+.0f} bp over 6 months"

    def net_liq(asof):
        nl = B.get("net_liq")
        if nl is None:
            return None
        nl = nl[nl.index + pd.Timedelta(days=2) <= asof]
        chg = nl.diff(13).dropna()
        if len(chg) < 104:
            return None
        sd = float(chg.iloc[-260:].std())
        if not sd:
            return None
        c = float(chg.iloc[-1])
        z = c / sd
        return z, f"Net Fed liquidity {c:+,.0f} bn over 13 weeks ({z:+.1f} sd)"

    def inflation(asof):
        p = avail(F["core_pce"], asof, LAG["PCEPILFE"])
        if p is None or len(p) < 13:
            return None
        pi = float(yoy(p, 12).iloc[-1])
        return pi, f"Core PCE inflation {pi:.1f}% (target 2%)"

    def spread_chg(key, label):
        def f(asof):
            h = avail(F[key], asof)
            if h is None:
                return None
            prev = value_at(h, asof - pd.DateOffset(months=3))
            if prev is None or (asof - pd.DateOffset(months=3) - prev[1]).days > 10:
                return None
            lvl = float(h.iloc[-1])
            chg = (lvl - prev[0]) * 100
            return chg, f"{label} {lvl:.2f}%, {chg:+.0f} bp over 3 months"
        return f

    def ad_days(asof):
        ad, ix = B.get("ad"), Y.get("^GSPC")
        if ad is None:
            return None
        a = ad[ad.index <= asof].iloc[-252:]
        if len(a) < 200 or (asof - a.index[-1]).days > 10:
            return None
        hi = a.idxmax()
        days = int((a.index > hi).sum())
        txt = f"A/D line last 52-week high {d(hi)}"
        if ix is not None:
            x = ix[ix.index <= asof].iloc[-252:]
            if len(x):
                txt += f"; S&P 500 last high {d(x.idxmax())}"
        return float(days), txt

    def pct_above(asof):
        p = B.get("pct200")
        if p is None:
            return None
        p = p[p.index <= asof]
        if p.empty or (asof - p.index[-1]).days > 10:
            return None
        v = float(p.iloc[-1])
        return v, f"{v:.0f}% of S&P 500 stocks above their 200-day average"

    def ratio_chg(key, days, label):
        def f(asof):
            r = B.get(key)
            if r is None:
                return None
            x = r[r.index <= asof]
            if len(x) <= days or (asof - x.index[-1]).days > 10:
                return None
            c = (float(x.iloc[-1]) / float(x.iloc[-1 - days]) - 1) * 100
            return c, f"{label} {c:+.1f}% over 3 months"
        return f

    def trend(get, label):
        def f(asof):
            series = get()
            if series is None:
                return None
            x = series[series.index <= asof]
            if len(x) < 220 or (asof - x.index[-1]).days > 10:
                return None
            dev = (float(x.iloc[-1]) / float(x.iloc[-200:].mean()) - 1) * 100
            off = (float(x.iloc[-1]) / float(x.iloc[-252:].max()) - 1) * 100
            return dev, f"{label} {dev:+.1f}% vs its 200-day average, {off:.1f}% from 52-week high"
        return f

    return {
        "policy_gap": policy_gap, "fed_path": fed_path, "net_liq": net_liq, "inflation": inflation,
        "hy": spread_chg("hy", "High-yield spread"),
        "baa": spread_chg("baa", "Baa corporate spread over 10-yr Treasury"),
        "ad": ad_days, "pct200": pct_above,
        "cycdef": trend(lambda: B.get("cycdef"), "Cyclicals/defensives ratio"),
        "ewcw": ratio_chg("rsp_spy", 63, "Equal-weight vs cap-weight S&P 500"),
        "copper": trend(lambda: Y.get("HG=F"), "Copper"),
        "commod": trend(lambda: Y.get("DBC"), "Broad commodities (DBC)"),
        "cugold": trend(lambda: B.get("cugold"), "Copper/gold ratio"),
        "dollar": trend(lambda: Y.get("DX-Y.NYB"), "Dollar index"),
    }


def eval_metrics(metrics, asof):
    out = {}
    for tid in TILE_IDS:
        try:
            out[tid] = metrics[tid](asof)
        except Exception as e:  # noqa: BLE001
            log(f"metric {tid} @ {asof.date()}: {e}")
            out[tid] = None
    return out


def stance(score, agree):
    """Full bullish/bearish needs a strong average AND agreement ("clarity")."""
    if score >= 0.35 and agree >= 0.6:
        return {"stance": "bullish", "label": "Bullish",
                "line": "Clarity: the signals line up behind risk. This is the setup where he sizes up."}
    if score <= -0.35 and agree >= 0.6:
        return {"stance": "bearish", "label": "Bearish",
                "line": "Clarity on the downside: the signals line up against risk."}
    if score >= 0.15:
        return {"stance": "lean_bull", "label": "Leaning bullish",
                "line": "More tailwinds than headwinds, but not enough agreement to bet big."}
    if score <= -0.15:
        return {"stance": "lean_bear", "label": "Leaning bearish",
                "line": "More headwinds than tailwinds, but not enough agreement to press shorts."}
    return {"stance": "flat", "label": "No clarity",
            "line": "The signals conflict. Without clarity he preserves capital and waits."}


def composite(mvals, weights=None):
    """mvals: {tid: (metric, reading) or None}. Returns (summary or None, tiles)."""
    tiles, num, den, tw, hw = [], 0.0, 0.0, 0.0, 0.0
    for tid, group, name, w, *_ in TILES:
        w = w if weights is None else weights[tid]
        r = mvals.get(tid)
        s = score_of(tid, r[0]) if r else None
        if s is not None and math.isnan(s):
            s = None
        if s is not None and w > 0:
            num += w * s
            den += w
            tw += w if s >= STATE_CUT else 0
            hw += w if s <= -STATE_CUT else 0
        tiles.append({"id": tid, "group": group, "name": name, "weight": w,
                      "s": None if s is None else round(s, 3), "state": state_of(s),
                      "reading": r[1] if r else "Data unavailable"})
    total = sum((w if weights is None else weights[t]) for t, _, _, w, *_ in TILES)
    if den < 0.5 * total or den == 0:
        return None, tiles
    score = num / den
    agree = (tw if score >= 0 else hw) / den
    return {"score": round(score, 3), "tail_w": round(tw / den, 3), "head_w": round(hw / den, 3),
            "agreement": round(agree, 3), "coverage": round(den / total, 2), **stance(score, agree)}, tiles


# --------------------------------------------------------------- backtest --
# Mirrors the client-side code in site/app.js; used to log the default fit.

def nw_t(x, y, lags):
    """t-stat of the slope in y = a + b x with Newey-West (Bartlett) errors."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    X = np.column_stack([np.ones_like(x), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    b = XtX_inv @ X.T @ y
    u = (y - X @ b)[:, None] * X
    S = u.T @ u
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        G = u[l:].T @ u[:-l]
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return float(b[1] / math.sqrt(V[1, 1]))


def corr(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 else float("nan")


def rank_ic(a, b):
    return corr(pd.Series(a).rank().values, pd.Series(b).rank().values)


def bt_scores(bt, weights):
    out = []
    for i in range(len(bt["dates"])):
        num = den = 0.0
        for tid, *_ in TILES:
            m = bt["metrics"][tid][i]
            w = weights[tid]
            if m is None or w <= 0:
                continue
            num += w * score_of(tid, m)
            den += w
        tot = sum(weights.values())
        out.append(num / den if den and den >= 0.5 * tot else None)
    return out


# Backtest targets: key prefix in the JSON and the sign of a "good" correlation
# (a useful score predicts higher returns but lower volatility and shallower drawdowns).
TARGETS = {"fwd": 1, "fvol": -1, "fdd": -1}


def ic_weights(bt, rows, h, key="fwd"):
    ws = {}
    fwd = bt[f"{key}{h}"]
    sign = TARGETS[key]
    for tid, *_ in TILES:
        pairs = [(score_of(tid, bt["metrics"][tid][i]), fwd[i]) for i in rows
                 if bt["metrics"][tid][i] is not None and fwd[i] is not None]
        if len(pairs) < 24:
            ws[tid] = 0.0
            continue
        c = corr([p[0] for p in pairs], [p[1] for p in pairs])
        ws[tid] = max(0.0, sign * c) if not math.isnan(c) else 0.0
    tot = sum(ws.values())
    return {k: (v / tot * 100 if tot else 100 / len(ws)) for k, v in ws.items()}


def walk_forward(bt, idx, h, key="fwd"):
    dates = [pd.Timestamp(x) for x in bt["dates"]]
    wf, cur = [None] * len(dates), None
    for i, dt in enumerate(dates):
        if dt.month == 1 or cur is None:
            rows = [j for j in idx if dates[j] + pd.DateOffset(months=h) <= dt]
            cur = ic_weights(bt, rows, h, key) if len(rows) >= 60 else None
        if cur is not None:
            wf[i] = bt_scores({"dates": [bt["dates"][i]], "metrics": {k: [v[i]] for k, v in bt["metrics"].items()}}, cur)[0]
    return wf


def timing_stats(bt, sc, cut, rows):
    """In the S&P 500 next month when score >= cut, in T-bills otherwise."""
    r, rf = bt["m1"], bt["rf1"]
    rows = [i for i in rows if sc[i] is not None and r[i] is not None]
    if len(rows) < 24:
        return None

    def st(rets, ex):
        rets, ex = np.array(rets) / 100, np.array(ex) / 100
        w = np.cumprod(1 + rets)
        dd = (1 - (w / np.maximum.accumulate(np.concatenate([[1.0], w]))[1:]).min()) * 100
        sd = ex.std(ddof=1)
        return {"cagr": round(float(w[-1] ** (12 / len(rets)) - 1) * 100, 2), "vol": round(float(rets.std(ddof=1)) * math.sqrt(12) * 100, 2),
                "sharpe": round(float(ex.mean() / sd) * math.sqrt(12), 2) if sd else None, "maxdd": round(float(dd), 1)}
    bh = [r[i] for i in rows]
    tm = [r[i] if sc[i] >= cut else rf[i] for i in rows]
    diff = np.array(tm) - np.array(bh)
    out = {"n": len(rows), "invested": round(sum(sc[i] >= cut for i in rows) / len(rows), 3),
           "buy_hold": st(bh, [r[i] - rf[i] for i in rows]), "timed": st(tm, [t - rf[i] for t, i in zip(tm, rows)])}
    out["excess_ann"] = round(float(diff.mean()) * 12, 2)
    out["excess_nw_t"] = round(mean_nw_t(diff, 6), 2)
    return out


def mean_nw_t(x, lags):
    x = np.asarray(x, float)
    n, e = len(x), x - x.mean()
    s = (e @ e) / n
    for l in range(1, lags + 1):
        s += 2 * (1 - l / (lags + 1)) * (e[l:] @ e[:-l]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


# ------------------------------------------------------------------ HAR ----
# Does the score add to a volatility forecast built from lagged realized vol
# and from option-implied vol at the same horizon?
# y = realized vol of daily S&P total returns over the next h months (annualized %).
# HAR regressors at each month-end: RV over the last 1, 5 and 22 trading days
# (Corsi 2009). Implied vol: Cboe VIX6M for 6 months, VIX1Y for 12 months.

def ols_fit(X, y):
    X1 = np.column_stack([np.ones(len(X)), X])
    b, *_ = np.linalg.lstsq(X1, y, rcond=None)
    return b


def ols_nw(X, y, lags):
    """Coefficients, Newey-West (Bartlett) standard errors and R² for y on [1, X]."""
    X1 = np.column_stack([np.ones(len(X)), X])
    XtX_inv = np.linalg.pinv(X1.T @ X1)
    b = XtX_inv @ X1.T @ y
    e = y - X1 @ b
    u = e[:, None] * X1
    S_ = u.T @ u
    for l in range(1, lags + 1):
        G = u[l:].T @ u[:-l]
        S_ += (1 - l / (lags + 1)) * (G + G.T)
    V = XtX_inv @ S_ @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(V), 1e-300))
    r2 = 1 - (e @ e) / (((y - y.mean()) ** 2).sum())
    return b, se, float(r2)


def har_panel(bt, feats, models, need, h, split):
    """Fit each model on the common sample where every column in `need` exists."""
    dates = [pd.Timestamp(x) for x in bt["dates"]]
    y_all = bt[f"fvol{h}"]
    idx = [i for i in range(len(dates)) if y_all[i] is not None and all(feats[c][i] is not None for c in need)]
    if len(idx) < 48:
        return None
    y = np.array([y_all[i] for i in idx], float)
    tr = [k for k, i in enumerate(idx) if dates[i] + pd.DateOffset(months=h) <= split]
    te = [k for k, i in enumerate(idx) if dates[i] >= split]
    out = {"start": bt["dates"][idx[0]], "n": len(idx), "n_train": len(tr), "n_test": len(te), "models": []}
    for name, cols in models:
        X = np.array([[feats[c][i] for c in cols] for i in idx], float)
        b, se, r2 = ols_nw(X, y, h - 1)
        row = {"model": name, "r2": round(r2, 3)}
        if "score" in cols:
            k = cols.index("score") + 1
            row["score_b"], row["score_t"] = round(float(b[k]), 2), round(float(b[k] / se[k]), 2)
        if "iv" in cols:
            k = cols.index("iv") + 1
            row["iv_b"], row["iv_t"] = round(float(b[k]), 2), round(float(b[k] / se[k]), 2)
        if len(tr) >= 30 and len(te) >= 12:
            bb = ols_fit(X[tr], y[tr])
            pred = bb[0] + X[te] @ bb[1:]
            bench = y[tr].mean()
            row["r2_test"] = round(1 - ((y[te] - pred) ** 2).sum() / ((y[te] - bench) ** 2).sum(), 3)
        # walk-forward: refit each January on months whose h-month window had closed
        preds, ys, bens, cur = [], [], [], None
        for k, i in enumerate(idx):
            dt = dates[i]
            if dt.month == 1 or cur is None:
                rows = [q for q, j in enumerate(idx) if dates[j] + pd.DateOffset(months=h) <= dt]
                cur = (ols_fit(X[rows], y[rows]), y[rows].mean()) if len(rows) >= 48 else None
            if cur is not None:
                preds.append(cur[0][0] + X[k] @ cur[0][1:]); ys.append(y[k]); bens.append(cur[1])
        if len(ys) >= 24:
            ys_, pr_, be_ = np.array(ys), np.array(preds), np.array(bens)
            row["r2_wf"] = round(1 - ((ys_ - pr_) ** 2).sum() / ((ys_ - be_) ** 2).sum(), 3)
            out["n_wf"] = len(ys)
        out["models"].append(row)
    return out


def mincer_zarnowitz(bt, iv, h):
    """Realized vol over the next h months regressed on implied vol at the same horizon."""
    y_all = bt[f"fvol{h}"]
    idx = [i for i in range(len(y_all)) if y_all[i] is not None and iv[i] is not None]
    if len(idx) < 48:
        return None
    y = np.array([y_all[i] for i in idx], float)
    x = np.array([iv[i] for i in idx], float)
    b, se, r2 = ols_nw(x[:, None], y, h - 1)
    return {"n": len(idx), "start": bt["dates"][idx[0]], "a": round(float(b[0]), 2), "a_t": round(float(b[0] / se[0]), 2),
            "b": round(float(b[1]), 2), "b_se": round(float(se[1]), 3), "b_t1": round(float((b[1] - 1) / se[1]), 2),
            "r2": round(r2, 3), "iv_mean": round(float(x.mean()), 2), "rv_mean": round(float(y.mean()), 2),
            "iv_above": round(float((x > y).mean() * 100), 0)}


def har_tests(bt, score, h, split):
    iv = bt["iv6"] if h == 6 else bt["iv12"]
    feats = {"rvd": bt["rvd"], "rvw": bt["rvw"], "rvm": bt["rvm"], "iv": iv, "score": score}
    H = ["rvd", "rvw", "rvm"]
    long = har_panel(bt, feats, [("HAR", H), ("HAR + score", H + ["score"])], H + ["score"], h, split)
    ivp = har_panel(bt, feats, [("HAR", H), ("HAR + score", H + ["score"]), ("Implied vol only", ["iv"]),
                                ("HAR + implied vol", H + ["iv"]), ("HAR + implied vol + score", H + ["iv", "score"])],
                    H + ["iv", "score"], h, split)
    return {"split": split.strftime("%Y-%m-%d"), "iv_name": "VIX6M" if h == 6 else "VIX1Y",
            "long": long, "iv": ivp, "mz": mincer_zarnowitz(bt, iv, h)}

def bt_summary(bt, h, key="fwd"):
    dates = [pd.Timestamp(x) for x in bt["dates"]]
    fwd = bt[f"{key}{h}"]
    w0 = {t[0]: t[3] for t in TILES}
    sc = bt_scores(bt, w0)
    idx = [i for i in range(len(dates)) if sc[i] is not None and fwd[i] is not None]
    if len(idx) < 36:
        return None
    x, y = [sc[i] for i in idx], [fwd[i] for i in idx]
    split = dates[idx[len(idx) // 2]]
    split = pd.Timestamp(year=split.year, month=1, day=1)
    train = [i for i in idx if dates[i] + pd.DateOffset(months=h) <= split]
    test = [i for i in idx if dates[i] >= split]
    non = idx[::h]
    fit = ic_weights(bt, train, h, key)
    sc_fit = bt_scores(bt, fit)
    # walk-forward: refit each January on rows whose forward window has closed
    wf = walk_forward(bt, idx, h, key)
    wfi = [i for i in idx if wf[i] is not None]
    r = lambda ii, s: round(corr([s[i] for i in ii], [fwd[i] for i in ii]), 3) if len(ii) > 5 else None
    return {
        "target": key, "h": h, "n": len(idx), "start": bt["dates"][idx[0]], "end": bt["dates"][idx[-1]],
        "corr": round(corr(x, y), 3), "rank_ic": round(rank_ic(x, y), 3), "nw_t": round(nw_t(x, y, h - 1), 2),
        "nonoverlap_corr": r(non, sc), "nonoverlap_n": len(non),
        "split": split.strftime("%Y-%m-%d"), "train_corr": r(train, sc), "test_corr": r(test, sc),
        "fitted_test_corr": r(test, sc_fit), "fitted_weights": {k: round(v, 1) for k, v in fit.items()},
        "walkforward_corr": r(wfi, wf), "walkforward_n": len(wfi),
    }


# --------------------------------------------------------------- charts ----

def ser(name, s, start=None, digits=3, style="solid"):
    s = clean(s)
    if s is None:
        return None
    if start is not None:
        s = s[s.index >= start]
    if s.empty:
        return None
    return {"name": name, "style": style, "x": [x.strftime("%Y-%m-%d") for x in s.index],
            "y": [round(float(v), digits) for v in s.values]}


def stat(label, s, unit, digits=2):
    s = clean(s)
    if s is None:
        return None
    out = {"label": label, "unit": unit, "digits": digits, "now": round(float(s.iloc[-1]), 4),
           "now_date": s.index[-1].strftime("%Y-%m-%d")}
    ago = value_at(s, s.index[-1] - pd.DateOffset(years=1))
    if ago is not None:
        out["year_ago"] = round(ago[0], 4)
    return out


def trend_state(s):
    s = clean(s)
    if s is None or len(s) < 220:
        return None
    dev = float(s.iloc[-1]) / float(s.iloc[-200:].mean()) - 1
    return {"dev": round(dev * 100, 1), "state": "up" if dev > 0.02 else "down" if dev < -0.02 else "flat"}


def S(lst):
    return [x for x in lst if x]


# ------------------------------------------------------------------ build --

def main() -> int:
    log(f"FRED mode: {'API key' if FRED_KEY else 'public CSV (no key)'}")
    ids = {
        "effr": "DFF", "upper": "DFEDTARU", "gdp": "GDP", "dgs2": "DGS2", "dgs10": "DGS10",
        "t10y2y": "T10Y2Y", "sep_median": "FEDTARMD", "sep_lr": "FEDTARMDLR",
        "ig": "BAMLC0A0CM", "hy": "BAMLH0A0HYM2", "baa": "BAA10Y",
        "core_pce": "PCEPILFE", "cpi": "CPIAUCSL", "tbill": "DTB3",
        "be5": "T5YIE", "walcl": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD",
        "debt_gdp": "GFDEGDQ188S", "us10_m": "IRLTLT01USM156N", "fr10_m": "IRLTLT01FRM156N",
    }
    F = {}
    for k, sid in ids.items():
        start = FRED_START
        if k in {"debt_gdp", "gdp"}:
            start = pd.Timestamp("1990-01-01")
        elif k in {"sep_median", "sep_lr"}:
            start = TODAY - pd.DateOffset(years=3)
        F[k] = safe(lambda sid=sid, start=start: fred(sid, start), f"FRED {sid}")

    mkt = ["HG=F", "GC=F", "CL=F", "NG=F", "ZS=F", "ZC=F", "DBC", "^GSPC", "^SP500TR", "RSP", "SPY",
           "DX-Y.NYB", "XLY", "XLI", "XLB", "XLF", "XLP", "XLU", "XLV"]
    Y = {t: None for t in mkt}
    closes = safe(lambda: yahoo_close(mkt), "Yahoo market data")
    if closes is not None:
        for t in mkt:
            Y[t] = clean(closes[t]) if t in closes.columns else None
            if Y[t] is None:
                errors.append(f"Yahoo {t}: no data")

    # ---- Cboe volatility indices (30-day, 3-month, 6-month and 1-year implied vol)
    V = {k: safe(lambda k=k: cboe_index(k), f"Cboe {k}") for k in ("VIX", "VIX3M", "VIX6M", "VIX1Y")}

    # ---- derived series
    B = {}

    def build_breadth():
        tick = sp500_tickers()
        px = yahoo_close(tick, start=MKT_START).dropna(axis=1, how="all")
        chg = px.diff()
        n_ok = chg.notna().sum(axis=1)
        net = ((chg > 0).sum(axis=1) - (chg < 0).sum(axis=1))[n_ok >= 300].iloc[1:]
        m200 = px.rolling(200).mean()
        valid = m200.notna().sum(axis=1)
        pct = ((px > m200).sum(axis=1) / valid * 100)[valid >= 300]
        log(f"Breadth: {px.shape[1]} of {len(tick)} constituents; A/D from {net.index[0].date()}, "
            f"%>200d from {pct.index[0].date()}")
        return net.cumsum(), pct, int(px.shape[1])

    br = safe(build_breadth, "S&P 500 breadth")
    B["ad"], B["pct200"], n_const = br if br else (None, None, None)

    if Y["RSP"] is not None and Y["SPY"] is not None:
        B["rsp_spy"] = (Y["RSP"] / Y["SPY"]).dropna()
    cyc, dfn = ["XLY", "XLI", "XLB", "XLF"], ["XLP", "XLU", "XLV"]
    if all(Y[t] is not None for t in cyc + dfn):
        px = pd.concat({t: Y[t] for t in cyc + dfn}, axis=1).dropna()
        rel = px / px.iloc[0]
        B["cycdef"] = rel[cyc].mean(axis=1) / rel[dfn].mean(axis=1) * 100
    if Y["HG=F"] is not None and Y["GC=F"] is not None:
        B["cugold"] = (Y["HG=F"] / Y["GC=F"] * 1000).dropna()

    def build_net_liq():
        # WALCL is in $ millions; WTREGEN and RRPONTSYD are in $ billions.
        w = F["walcl"].resample("W-WED").last() / 1000.0
        t = F["tga"].resample("W-WED").last().reindex(w.index).ffill()
        r = F["rrp"].resample("W-WED").mean().reindex(w.index).ffill().fillna(0.0)
        return (w - t - r).dropna()
    if all(F[k] is not None for k in ("walcl", "tga", "rrp")):
        B["net_liq"] = safe(build_net_liq, "Net liquidity")

    metrics = make_metrics(F, Y, B)

    # ---- today and a year ago
    now_m = eval_metrics(metrics, TODAY)
    now_comp, tiles = composite(now_m)
    ago_comp, ago_tiles = composite(eval_metrics(metrics, TODAY - pd.DateOffset(years=1)))
    for t, a in zip(tiles, ago_tiles):
        t["s_ago"], t["state_ago"] = a["s"], a["state"]
        t["metric"] = None if now_m[t["id"]] is None else round(now_m[t["id"]][0], 4)

    # ---- monthly history since 2000 (metrics, forward returns)
    dates = list(pd.date_range(BT_START, TODAY, freq="ME"))
    if dates[-1] != TODAY:
        dates.append(TODAY)
    bt = {"dates": [x.strftime("%Y-%m-%d") for x in dates], "metrics": {t: [] for t in TILE_IDS}}
    for asof in dates:
        mv = eval_metrics(metrics, asof)
        for tid in TILE_IDS:
            bt["metrics"][tid].append(None if mv[tid] is None else round(float(mv[tid][0]), 4))
    ret_name = "S&P 500 total return"
    px = Y.get("^SP500TR")
    if px is None:
        px, ret_name = Y.get("^GSPC"), "S&P 500 price (total return unavailable this run)"
    for h in (6, 12):
        fw, fv, fd = [], [], []
        for asof in dates:
            end = asof + pd.DateOffset(months=h)
            if px is None or end > px.index[-1]:
                fw.append(None), fv.append(None), fd.append(None)
                continue
            a, b = value_at(px, asof), value_at(px, end)
            if a is None or b is None:
                fw.append(None), fv.append(None), fd.append(None)
                continue
            fw.append(round((b[0] / a[0] - 1) * 100, 3))
            path = px[(px.index >= a[1]) & (px.index <= end)]
            dr = path.pct_change().dropna()
            fv.append(round(float(dr.std() * math.sqrt(252) * 100), 3) if len(dr) > 20 else None)
            fd.append(round(float((1 - (path / path.cummax()).min()) * 100), 3))
        bt[f"fwd{h}"], bt[f"fvol{h}"], bt[f"fdd{h}"] = fw, fv, fd
    # one-month-ahead stock and T-bill returns for the timing rule
    m1, rf1 = [], []
    for asof in dates:
        end = asof + pd.DateOffset(months=1)
        a = value_at(px, asof) if px is not None else None
        b = value_at(px, end) if px is not None and end <= px.index[-1] else None
        m1.append(None if a is None or b is None else round((b[0] / a[0] - 1) * 100, 4))
        tb = value_at(F["tbill"], asof) if F.get("tbill") is not None else None
        rf1.append(round(tb[0] / 12, 4) if tb else 0.0)
    bt["m1"], bt["rf1"] = m1, rf1
    # HAR inputs at each month-end: realized vol over the last 1, 5 and 22 trading days,
    # and implied vol matched to the horizon (VIX6M, VIX1Y)
    rvd, rvw, rvm = [], [], []
    ivs = {"iv6": [], "iv12": [], "vix": []}
    dr = px.pct_change().dropna() if px is not None else None
    for asof in dates:
        w = dr[dr.index <= asof] if dr is not None else None
        ok = w is not None and len(w) >= 22 and (asof - w.index[-1]).days <= 5
        rvd.append(round(float(abs(w.iloc[-1]) * math.sqrt(252) * 100), 3) if ok else None)
        rvw.append(round(float(math.sqrt((w.iloc[-5:] ** 2).mean() * 252) * 100), 3) if ok else None)
        rvm.append(round(float(math.sqrt((w.iloc[-22:] ** 2).mean() * 252) * 100), 3) if ok else None)
        for key, nm in (("iv6", "VIX6M"), ("iv12", "VIX1Y"), ("vix", "VIX")):
            v = value_at(V[nm], asof) if V.get(nm) is not None else None
            ivs[key].append(round(v[0], 2) if v and (asof - v[1]).days <= 5 else None)
    bt["rvd"], bt["rvw"], bt["rvm"] = rvd, rvw, rvm
    bt.update(ivs)
    bt["ret_name"] = ret_name
    bt["params"] = [{"id": t[0], "name": t[2], "group": t[1], "weight": t[3], "center": t[4], "scale": t[5],
                     "unit": t[6], "center_range": list(t[7]), "scale_range": list(t[8]),
                     "first": next((bt["dates"][i] for i, v in enumerate(bt["metrics"][t[0]]) if v is not None), None)}
                    for t in TILES]
    w0 = {t[0]: t[3] for t in TILES}
    default_scores = bt_scores(bt, w0)
    hist = []
    for i, x in enumerate(default_scores):
        if x is not None:
            hist.append({"date": bt["dates"][i], "score": round(x, 3)})
    summaries = {}
    for key in TARGETS:
        for h in (6, 12):
            sm = safe(lambda h=h, key=key: bt_summary(bt, h, key), f"backtest {key} {h}m")
            if sm:
                summaries[f"{key}{h}" if key != "fwd" else str(h)] = sm
                log(f"Backtest {key} {h}m ({sm['start']}..{sm['end']}, n={sm['n']}): corr {sm['corr']}, rank IC {sm['rank_ic']}, "
                    f"NW t {sm['nw_t']}, non-overlap corr {sm['nonoverlap_corr']} (n={sm['nonoverlap_n']}); "
                    f"split {sm['split']}: train {sm['train_corr']}, test {sm['test_corr']}, "
                    f"IC-fitted on train -> test {sm['fitted_test_corr']}; walk-forward {sm['walkforward_corr']} "
                    f"(n={sm['walkforward_n']})")
    # how often a deep drawdown followed a bearish-leaning (<= -0.15) default score
    for h in (6, 12):
        dd = bt[f"fdd{h}"]
        ii = [i for i in range(len(dates)) if default_scores[i] is not None and dd[i] is not None]
        lo = [i for i in ii if default_scores[i] <= -0.15]
        hi = [i for i in ii if default_scores[i] > -0.15]
        if lo and hi:
            msg = []
            for th in (10, 20):
                p_lo = sum(dd[i] > th for i in lo) / len(lo)
                p_hi = sum(dd[i] > th for i in hi) / len(hi)
                msg.append(f">{th}%: {p_lo:.0%} after score<=-0.15 (n={len(lo)}) vs {p_hi:.0%} otherwise (n={len(hi)})")
            log(f"Drawdown odds {h}m: " + "; ".join(msg))
    split_y = int(summaries["12"]["split"][:4]) if "12" in summaries else 2013
    rows_all = list(range(len(dates)))
    for label, rows in (("full", rows_all), (f"from {split_y}", [i for i in rows_all if dates[i].year >= split_y])):
        tst = safe(lambda rows=rows: timing_stats(bt, default_scores, -0.15, rows), f"timing {label}")
        if tst:
            log(f"Timing (in stocks when score >= -0.15), {label}, n={tst['n']}, invested {tst['invested']:.0%}: "
                f"buy&hold {tst['buy_hold']}; timed {tst['timed']}; excess {tst['excess_ann']}%/yr, NW t {tst['excess_nw_t']}")
    bt["summary"] = summaries
    har = {}
    for h in (6, 12):
        sp = pd.Timestamp(summaries[str(h)]["split"]) if str(h) in summaries else pd.Timestamp("2013-01-01")
        res = safe(lambda h=h, sp=sp: har_tests(bt, default_scores, h, sp), f"HAR {h}m")
        if not res:
            continue
        har[str(h)] = res
        for pk in ("long", "iv"):
            P = res[pk]
            if not P:
                log(f"HAR {h}m {pk}: too few months")
                continue
            log(f"HAR {h}m [{pk} sample] from {P['start']}, n={P['n']}, train {P['n_train']}, test {P['n_test']}, wf {P.get('n_wf')}")
            for r in P["models"]:
                log(f"   {r['model']}: R2 {r['r2']}, test R2 {r.get('r2_test')}, walk-forward R2 {r.get('r2_wf')}"
                    + (f", score b {r['score_b']} t {r['score_t']}" if "score_b" in r else "")
                    + (f", IV b {r['iv_b']} t {r['iv_t']}" if "iv_b" in r else ""))
        if res["mz"]:
            log(f"MZ {h}m {res['iv_name']}: {res['mz']}")
    bt["har"] = har

    # per-tile share of months in each state since the tile's data starts
    for t in tiles:
        ss = [score_of(t["id"], m) for m in bt["metrics"][t["id"]] if m is not None]
        if ss:
            st = [state_of(x) for x in ss]
            first = next(bt["dates"][i] for i, v in enumerate(bt["metrics"][t["id"]]) if v is not None)
            t["history"] = {"months": len(st), "since": first[:4],
                            **{k: round(st.count(k) / len(st), 3) for k in ("tailwind", "neutral", "headwind")}}
        else:
            t["history"] = None

    # ---- charts
    d5, d10, d30 = (TODAY - pd.DateOffset(years=n) for n in (5, 10, 30))
    ngdp = yoy(F["gdp"], 4)
    gap = None
    if ngdp is not None and F["effr"] is not None:
        gap = (F["effr"].resample("QS").mean().reindex(ngdp.index) - ngdp).dropna()
    spread2 = None
    if F["dgs2"] is not None and F["effr"] is not None:
        spread2 = (F["dgs2"] - F["effr"].reindex(F["dgs2"].index).ffill()).dropna()
    core, cpi = yoy(F["core_pce"], 12), yoy(F["cpi"], 12)
    sep = []
    if F["sep_median"] is not None:
        sep = [{"year": str(i.year), "median": round(float(v), 3)}
               for i, v in F["sep_median"].items() if i.year >= TODAY.year][:3]
    if F["sep_lr"] is not None:
        sep.append({"year": "Longer run", "median": round(float(F["sep_lr"].iloc[-1]), 3)})
    nl13 = B["net_liq"].diff(13) if B.get("net_liq") is not None else None
    fr_us = (F["fr10_m"] - F["us10_m"]).dropna() if F["fr10_m"] is not None and F["us10_m"] is not None else None

    vix_ratio = (V["VIX"] / V["VIX3M"]).dropna() if V.get("VIX") is not None and V.get("VIX3M") is not None else None
    _px = Y.get("^SP500TR") if Y.get("^SP500TR") is not None else Y.get("^GSPC")
    rv22 = (_px.pct_change().rolling(22).std() * math.sqrt(252) * 100).dropna() if _px is not None else None

    def trended(cid, tile, title, s, digits, unit_label=None):
        return {"id": cid, "tile": tile, "title": title, "unit": "",
                "series": S([ser(title.split(" (")[0], s, d5, digits),
                             ser("200-day average", ma(s), d5, digits, "dash")]),
                "stats": S([stat(unit_label or title.split(" (")[0], s, "", digits)]),
                "trend": trend_state(s)}

    sections = [
        {"title": "Liquidity & the Fed", "charts": [
            {"id": "policy_gap", "tile": "policy_gap", "title": "Fed funds vs nominal GDP growth", "unit": "%",
             "series": S([ser("Fed funds (effective)", F["effr"], d10, 2),
                          ser("Nominal GDP growth, y/y", ngdp, d10, 2),
                          ser("Gap (fed funds minus growth)", gap, d10, 2, "dot")]),
             "refs": [{"y": 0}], "stats": S([stat("Gap", gap, "pts", 1)])},
            {"id": "fed_path", "tile": "fed_path", "title": "Fed target vs 2-year Treasury", "unit": "%",
             "series": S([ser("Fed funds target, upper", F["upper"], d5, 2), ser("2-yr Treasury", F["dgs2"], d5, 2)]),
             "stats": S([stat("2-yr minus fed funds", spread2, "pts")]), "sep": sep},
            {"id": "net_liq", "tile": "net_liq", "title": "Net Fed liquidity, 13-week change ($ bn)", "unit": "",
             "series": S([ser("Fed assets minus TGA minus reverse repo", nl13, d5, 0)]),
             "refs": [{"y": 0}], "stats": S([stat("13-week change, $ bn", nl13, "", 0)])},
            {"id": "inflation", "tile": "inflation", "title": "Inflation vs the 2% target", "unit": "%",
             "series": S([ser("Core PCE, y/y", core, d10, 2), ser("CPI, y/y", cpi, d10, 2),
                          ser("5-yr breakeven", F["be5"], d10, 2)]),
             "refs": [{"y": 2, "label": "2% target"}], "stats": S([stat("Core PCE y/y", core, "%", 1)])},
        ]},
        {"title": "Credit", "charts": [
            {"id": "hy", "tile": "hy", "title": "High-yield option-adjusted spread", "unit": "%",
             "series": S([ser("ICE BofA US High Yield OAS", F["hy"], d5, 2)]), "stats": S([stat("HY spread", F["hy"], "%")]),
             "note": "FRED carries only about the last three years of the ICE BofA spread series."},
            {"id": "baa", "tile": "baa", "title": "Moody's Baa corporate yield minus 10-yr Treasury", "unit": "%",
             "series": S([ser("Baa minus 10-yr", F["baa"], d10, 2)]), "stats": S([stat("Baa spread", F["baa"], "%")])},
            {"id": "ig", "tile": None, "title": "Investment-grade option-adjusted spread (context)", "unit": "%",
             "series": S([ser("ICE BofA US Corporate OAS", F["ig"], d5, 2)]), "stats": S([stat("IG spread", F["ig"], "%")])},
        ]},
        {"title": "Market internals", "charts": [
            {"id": "ad", "tile": "ad", "title": "S&P 500 advance-decline line", "unit": "",
             "series": S([ser("Cumulative advances minus declines", B.get("ad"), d10, 0)]),
             "note": (f"Built from the {n_const} current S&P 500 members (survivorship-biased, more so further back), "
                      "as a stand-in for the NYSE A/D line, which has no reliable free feed.") if n_const else None},
            {"id": "spx", "tile": None, "title": "S&P 500 index", "unit": "",
             "series": S([ser("S&P 500", Y.get("^GSPC"), d5, 2)]), "stats": S([stat("S&P 500", Y.get("^GSPC"), "", 0)])},
            {"id": "pct200", "tile": "pct200", "title": "Share of S&P 500 stocks above their 200-day average", "unit": "%",
             "series": S([ser("% above 200-day", B.get("pct200"), d10, 1)]),
             "refs": [{"y": 50}], "stats": S([stat("% above 200-day", B.get("pct200"), "%", 0)])},
            {"id": "cycdef", "tile": "cycdef", "title": "Cyclicals vs defensives", "unit": "",
             "series": S([ser("XLY+XLI+XLB+XLF vs XLP+XLU+XLV", B.get("cycdef"), d5, 2),
                          ser("200-day average", ma(B.get("cycdef")), d5, 2, "dash")])},
            {"id": "ewcw", "tile": "ewcw", "title": "Equal-weight vs cap-weight S&P 500 (RSP/SPY)", "unit": "",
             "series": S([ser("RSP / SPY", B.get("rsp_spy"), d5, 4)])},
        ]},
        {"title": "Commodities & the dollar", "charts": [
            trended("copper", "copper", "Copper (COMEX, $/lb)", Y["HG=F"], 3),
            trended("commod", "commod", "Broad commodities (DBC ETF)", Y["DBC"], 2),
            trended("cugold", "cugold", "Copper / gold ratio (x1000)", B.get("cugold"), 3),
            trended("dollar", "dollar", "US Dollar Index (DXY)", Y["DX-Y.NYB"], 2),
        ]},
        {"title": "The big commodities",
         "note": "The biggest single commodities by weight in the major commodity indices: crude, natural gas, gold, copper and the two largest grains. Shown against their 200-day average; not scored.",
         "charts": [trended(f"c_{k}", None, n, Y[t], 2) for k, t, n in [
             ("cl", "CL=F", "WTI crude oil ($/bbl)"), ("ng", "NG=F", "Natural gas ($/MMBtu)"),
             ("gc", "GC=F", "Gold ($/oz)"), ("hg", "HG=F", "Copper ($/lb)"),
             ("zs", "ZS=F", "Soybeans (¢/bu)"), ("zc", "ZC=F", "Corn (¢/bu)")]]},
        {"title": "Volatility (context, not scored)",
         "note": "Cboe implied-vol indices, priced off S&P 500 options at each horizon, against the volatility the S&P actually delivered over the past month. Implied vol usually sits above realized vol because option sellers charge a premium for insurance. Not in the score; the Backtest tab tests whether the score adds anything to them.",
         "charts": [
            {"id": "vix_term", "tile": None, "title": "VIX term structure: implied vol by horizon", "unit": "",
             "series": S([ser("VIX (30 days)", V.get("VIX"), d5, 2), ser("VIX3M (3 months)", V.get("VIX3M"), d5, 2),
                          ser("VIX6M (6 months)", V.get("VIX6M"), d5, 2), ser("VIX1Y (1 year)", V.get("VIX1Y"), d5, 2)]),
             "stats": S([stat("VIX", V.get("VIX"), "", 1), stat("VIX6M", V.get("VIX6M"), "", 1), stat("VIX1Y", V.get("VIX1Y"), "", 1)])},
            {"id": "vix_ratio", "tile": None, "title": "VIX / VIX3M (above 1 means near-term stress: the curve is inverted)", "unit": "",
             "series": S([ser("VIX / VIX3M", vix_ratio, d10, 3)]), "refs": [{"y": 1}],
             "stats": S([stat("VIX / VIX3M", vix_ratio, "", 2)])},
            {"id": "rv_iv", "tile": None, "title": "Realized vs implied: past-month S&P vol and the VIX", "unit": "",
             "series": S([ser("Realized vol, past 22 trading days", rv22, d10, 2), ser("VIX", V.get("VIX"), d10, 2)]),
             "stats": S([stat("Realized vol, 1 month", rv22, "", 1)])},
        ]},
        {"title": "Rates and the fiscal backdrop (context, not scored)", "charts": [
            {"id": "rates", "tile": None, "title": "Treasury yields", "unit": "%",
             "series": S([ser("10-yr", F["dgs10"], d5, 2), ser("2-yr", F["dgs2"], d5, 2),
                          ser("10-yr minus 2-yr", F["t10y2y"], d5, 2, "dot")]), "refs": [{"y": 0}]},
            {"id": "debt", "tile": None, "title": "Federal debt, % of GDP", "unit": "%",
             "series": S([ser("Federal debt / GDP", F["debt_gdp"], d30, 1)])},
            {"id": "france", "tile": None, "title": "10-yr yields: France minus US", "unit": "",
             "series": S([ser("France minus US 10-yr, pts", fr_us, d10, 2)]), "refs": [{"y": 0}]},
        ]},
    ]

    # ---- small chart for each map tile: last 3 years of the series the tile scores
    d3 = TODAY - pd.DateOffset(years=3)

    def fmtv(v, kind):
        return {"pts": f"{v:+.1f} pts", "%": f"{v:.1f}%", "lvl": f"{v:.2f}%", "bn": f"{v:+,.0f} bn",
                "$": f"${v:,.2f}", "int": f"{v:,.0f}", "r4": f"{v:.3f}"}.get(kind, f"{v:,.2f}")

    def spark(main_s, kind, ref=None, with_ma=False, digits=3, label=None):
        m = clean(main_s)
        if m is None:
            return None
        sr = S([ser(label or "Value", m, d3, digits),
                ser("200-day average", ma(m), d3, digits, "dash") if with_ma else None])
        if not sr:
            return None
        return {"series": sr, "ref": ref, "value": fmtv(float(m.iloc[-1]), kind),
                "date": m.index[-1].strftime("%Y-%m-%d")}

    SPARK = {
        "policy_gap": lambda: spark(gap, "pts", 0, label="Fed funds minus nominal growth"),
        "fed_path": lambda: spark(spread2, "pts", 0, label="2-yr minus fed funds"),
        "net_liq": lambda: spark(nl13, "bn", 0, digits=0, label="13-wk change, $ bn"),
        "inflation": lambda: spark(core, "%", 2.5, label="Core PCE y/y"),
        "hy": lambda: spark(F["hy"], "lvl", label="HY spread"),
        "baa": lambda: spark(F["baa"], "lvl", label="Baa spread"),
        "ad": lambda: spark(B.get("ad"), "int", digits=0, label="A/D line"),
        "pct200": lambda: spark(B.get("pct200"), "%", 50, digits=1, label="% above 200-day"),
        "cycdef": lambda: spark(B.get("cycdef"), "", with_ma=True, label="Cyclicals/defensives"),
        "ewcw": lambda: spark(B.get("rsp_spy"), "r4", digits=4, label="RSP/SPY"),
        "copper": lambda: spark(Y["HG=F"], "$", with_ma=True, label="Copper $/lb"),
        "commod": lambda: spark(Y["DBC"], "$", with_ma=True, label="DBC"),
        "cugold": lambda: spark(B.get("cugold"), "", with_ma=True, label="Copper/gold x1000"),
        "dollar": lambda: spark(Y["DX-Y.NYB"], "", with_ma=True, label="DXY"),
    }
    for t in tiles:
        t["spark"] = safe(SPARK[t["id"]], f"spark {t['id']}")

    n_ok = sum(1 for t in tiles if t["s"] is not None)
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fred_mode": "api" if FRED_KEY else "public_csv",
           "now": now_comp, "year_ago": ago_comp, "tiles": tiles, "history": hist,
           "backtest": bt, "sections": sections, "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    log(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB): {n_ok}/{len(tiles)} tiles scored, "
        f"stance {now_comp['label'] if now_comp else 'n/a'} "
        f"(score {now_comp['score'] if now_comp else 'n/a'}), {len(errors)} errors")
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
