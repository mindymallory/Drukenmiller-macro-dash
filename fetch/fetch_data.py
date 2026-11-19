"""Build site/data/dashboard.json for the Druckenmiller macro map.

Each indicator gets a continuous score s in [-1, +1]: +1 is a full tailwind
for risk assets, -1 a full headwind, 0 neutral. Scores are combined with fixed
weights into a composite and a stance (bullish / leaning / not playing).
The rule and the reasoning behind every threshold are written up in the Guide
tab of the site (site/index.html); keep the two in sync.

Data sources (all free):
  * FRED. Uses the official API when FRED_API_KEY is set (a GitHub Actions
    secret, never committed); otherwise FRED's public fredgraph.csv download.
  * Yahoo Finance via yfinance (no key): futures, ETFs, the S&P 500 index and
    S&P 500 constituents (for breadth).
  * S&P 500 constituent list from the datasets/s-and-p-500-companies repo,
    with Wikipedia as a fallback.

Every series is fetched independently; failures are listed in "errors" and
the affected tiles show as unavailable. The build fails only if no tile scores.

History: the composite is recomputed at past month-ends. To avoid look-ahead,
macro series are only used once they would have been published (approximate
release lag per series, see LAG).
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
MACRO_START = TODAY - pd.DateOffset(years=12)
HIST_YEARS = 5

errors: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- fetchers --

def fred(series_id: str, start: pd.Timestamp = MACRO_START) -> pd.Series:
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
            log(f"FRED {series_id}: {len(s)} obs, last {s.index[-1].date()}")
            return s
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"FRED {series_id}: {last_err}")


def yahoo_close(tickers: list[str], years: int = 6) -> pd.DataFrame:
    import yfinance as yf

    frames = []
    for i in range(0, len(tickers), 80):
        chunk = tickers[i:i + 80]
        for attempt in range(3):
            try:
                df = yf.download(chunk, period=f"{years}y", interval="1d",
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
            df = yf.download(t, period=f"{years}y", interval="1d",
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


# --------------------------------------------------------------- scorers ---
# Each scorer takes an as-of date and returns dict(s, reading) or None.

def make_scorers(F, Y, B):
    def policy_gap(asof):
        ff = avail(F["effr"], asof)
        gdp = avail(F["gdp"], asof, LAG["GDP"])
        if ff is None or gdp is None or len(gdp) < 5:
            return None
        n = float(yoy(gdp, 4).iloc[-1])
        g = float(ff.iloc[-1]) - n
        return {"s": clip(-g / 2.0),
                "reading": f"Fed funds {ff.iloc[-1]:.2f}% vs nominal GDP growth {n:.1f}%: gap {g:+.1f} pts"}

    def fed_path(asof):
        two, ff, up = avail(F["dgs2"], asof), avail(F["effr"], asof), avail(F["upper"], asof)
        if two is None or ff is None:
            return None
        spread = float(two.iloc[-1]) - float(ff.iloc[-1])
        parts, move_txt = [clip(-spread / 0.75)], ""
        if up is not None:
            prev = value_at(up, asof - pd.DateOffset(months=6))
            if prev is not None:
                move = float(up.iloc[-1]) - prev[0]
                parts.append(clip(-move / 0.5))
                move_txt = f"; target {move * 100:+.0f} bp over 6 months"
        return {"s": float(np.mean(parts)),
                "reading": f"2-yr Treasury {spread * 100:+.0f} bp vs fed funds{move_txt}"}

    def net_liq(asof):
        nl = B.get("net_liq")
        if nl is None:
            return None
        nl = nl[nl.index + pd.Timedelta(days=2) <= asof]
        chg = nl.diff(13).dropna()
        if len(chg) < 104:
            return None
        sd = float(chg.iloc[-260:].std())
        c = float(chg.iloc[-1])
        if not sd:
            return None
        z = c / sd
        return {"s": clip(z / 1.5),
                "reading": f"Net Fed liquidity {c:+,.0f} bn over 13 weeks ({z:+.1f} sd)"}

    def inflation(asof):
        p = avail(F["core_pce"], asof, LAG["PCEPILFE"])
        if p is None or len(p) < 13:
            return None
        pi = float(yoy(p, 12).iloc[-1])
        return {"s": clip((2.5 - pi) / 1.0), "reading": f"Core PCE inflation {pi:.1f}% (target 2%)"}

    def spread_trend(key, scale_bp, label):
        def f(asof):
            h = avail(F[key], asof)
            if h is None:
                return None
            prev = value_at(h, asof - pd.DateOffset(months=3))
            if prev is None:
                return None
            lvl = float(h.iloc[-1])
            chg = (lvl - prev[0]) * 100
            return {"s": clip(-chg / scale_bp),
                    "reading": f"{label} {lvl:.2f}%, {chg:+.0f} bp over 3 months"}
        return f

    def ad_confirm(asof):
        ad, ix = B.get("ad"), Y.get("^GSPC")
        if ad is None or ix is None:
            return None
        ad, ix = ad[ad.index <= asof].iloc[-252:], ix[ix.index <= asof].iloc[-252:]
        if len(ad) < 200 or len(ix) < 200:
            return None
        ad_hi, ix_hi = ad.idxmax(), ix.idxmax()
        d_ad = int((ad.index > ad_hi).sum())
        d_ix = int((ix.index > ix_hi).sum())
        s = clip(1 - d_ad / 30.0)
        if d_ix > 20:  # index itself is off its high, so the test says less
            s *= 0.5
        return {"s": s, "reading": f"A/D line last high {d(ad_hi)}; S&P 500 last high {d(ix_hi)}"}

    def pct_above(asof):
        p = B.get("pct200")
        if p is None:
            return None
        p = p[p.index <= asof]
        if p.empty:
            return None
        v = float(p.iloc[-1])
        return {"s": clip((v - 50) / 25.0), "reading": f"{v:.0f}% of S&P 500 stocks above their 200-day average"}

    def ratio_change(key, days, scale_pct, label):
        def f(asof):
            r = B.get(key)
            if r is None:
                return None
            x = r[r.index <= asof]
            if len(x) <= days:
                return None
            c = (float(x.iloc[-1]) / float(x.iloc[-1 - days]) - 1) * 100
            return {"s": clip(c / scale_pct), "reading": f"{label} {c:+.1f}% over 3 months"}
        return f

    def trend(get, scale, label, invert=False):
        def f(asof):
            series = get()
            if series is None:
                return None
            x = series[series.index <= asof]
            if len(x) < 220:
                return None
            dev = float(x.iloc[-1]) / float(x.iloc[-200:].mean()) - 1
            off = (float(x.iloc[-1]) / float(x.iloc[-252:].max()) - 1) * 100
            return {"s": clip((-dev if invert else dev) / scale),
                    "reading": f"{label} {dev * 100:+.1f}% vs its 200-day average, {off:.1f}% from 52-week high"}
        return f

    return {
        "policy_gap": policy_gap, "fed_path": fed_path, "net_liq": net_liq, "inflation": inflation,
        "hy": spread_trend("hy", 50, "High-yield spread"),
        "ig": spread_trend("ig", 20, "Investment-grade spread"),
        "ad": ad_confirm, "pct200": pct_above,
        "cycdef": trend(lambda: B.get("cycdef"), 0.04, "Cyclicals/defensives ratio"),
        "ewcw": ratio_change("rsp_spy", 63, 3.0, "Equal-weight vs cap-weight S&P 500"),
        "copper": trend(lambda: Y.get("HG=F"), 0.10, "Copper"),
        "commod": trend(lambda: Y.get("DBC"), 0.07, "Broad commodities (DBC)"),
        "cugold": trend(lambda: B.get("cugold"), 0.08, "Copper/gold ratio"),
        "dollar": trend(lambda: Y.get("DX-Y.NYB"), 0.04, "Dollar index", invert=True),
    }


# (id, group, name, weight). Weights sum to 100; rationale in the Guide tab.
TILES = [
    ("policy_gap", "Liquidity & the Fed", "Policy vs growth", 15),
    ("fed_path", "Liquidity & the Fed", "Fed direction", 12),
    ("net_liq", "Liquidity & the Fed", "Fed liquidity", 8),
    ("inflation", "Liquidity & the Fed", "Inflation pressure", 8),
    ("hy", "Credit", "High-yield spreads", 10),
    ("ig", "Credit", "Investment-grade spreads", 5),
    ("ad", "Market internals", "A/D line confirms", 8),
    ("pct200", "Market internals", "Participation", 6),
    ("cycdef", "Market internals", "Cyclicals vs defensives", 7),
    ("ewcw", "Market internals", "Equal vs cap weight", 3),
    ("copper", "Commodities & dollar", "Copper", 6),
    ("commod", "Commodities & dollar", "Broad commodities", 5),
    ("cugold", "Commodities & dollar", "Copper / gold", 3),
    ("dollar", "Commodities & dollar", "Dollar", 4),
]
assert sum(t[3] for t in TILES) == 100
STATE_CUT = 0.2


def state_of(s):
    if s is None:
        return "unavailable"
    return "tailwind" if s >= STATE_CUT else "headwind" if s <= -STATE_CUT else "neutral"


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


def composite(scorers, asof):
    tiles, num, den, tw, hw = [], 0.0, 0.0, 0.0, 0.0
    for tid, group, name, w in TILES:
        r = None
        try:
            r = scorers[tid](asof)
        except Exception as e:  # noqa: BLE001
            log(f"score {tid} @ {asof.date()}: {e}")
        s = None if r is None or r.get("s") is None or math.isnan(r["s"]) else float(r["s"])
        if s is not None:
            num += w * s
            den += w
            tw += w if s >= STATE_CUT else 0
            hw += w if s <= -STATE_CUT else 0
        tiles.append({"id": tid, "group": group, "name": name, "weight": w,
                      "s": None if s is None else round(s, 3), "state": state_of(s),
                      "reading": r["reading"] if r else "Data unavailable"})
    if den < 50:
        return None, tiles
    score = num / den
    agree = (tw if score >= 0 else hw) / den
    return {"score": round(score, 3), "tail_w": round(tw / den, 3), "head_w": round(hw / den, 3),
            "agreement": round(agree, 3), "coverage": round(den / 100, 2), **stance(score, agree)}, tiles


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
        "ig": "BAMLC0A0CM", "hy": "BAMLH0A0HYM2", "core_pce": "PCEPILFE", "cpi": "CPIAUCSL",
        "be5": "T5YIE", "walcl": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD",
        "debt_gdp": "GFDEGDQ188S", "us10_m": "IRLTLT01USM156N", "fr10_m": "IRLTLT01FRM156N",
    }
    F = {}
    for k, sid in ids.items():
        start = MACRO_START
        if k in {"debt_gdp", "gdp"}:
            start = TODAY - pd.DateOffset(years=30)
        elif k in {"sep_median", "sep_lr"}:
            start = TODAY - pd.DateOffset(years=3)
        F[k] = safe(lambda sid=sid, start=start: fred(sid, start), f"FRED {sid}")

    mkt = ["HG=F", "GC=F", "CL=F", "NG=F", "ZS=F", "ZC=F", "DBC", "^GSPC", "RSP", "SPY",
           "DX-Y.NYB", "XLY", "XLI", "XLB", "XLF", "XLP", "XLU", "XLV"]
    Y = {t: None for t in mkt}
    closes = safe(lambda: yahoo_close(mkt, years=7), "Yahoo market data")
    if closes is not None:
        for t in mkt:
            Y[t] = clean(closes[t]) if t in closes.columns else None
            if Y[t] is None:
                errors.append(f"Yahoo {t}: no data")

    # ---- derived series
    B = {}

    def build_breadth():
        tick = sp500_tickers()
        px = yahoo_close(tick, years=HIST_YEARS + 2).dropna(axis=1, how="all")
        n = px.shape[1]
        chg = px.diff()
        net = ((chg > 0).sum(axis=1) - (chg < 0).sum(axis=1))[chg.notna().sum(axis=1) >= 0.8 * n].iloc[1:]
        m200 = px.rolling(200).mean()
        valid = m200.notna().sum(axis=1)
        pct = ((px > m200).sum(axis=1) / valid * 100)[valid >= 0.8 * n]
        log(f"Breadth: {n} of {len(tick)} constituents, A/D {len(net)} days, %>200d {len(pct)} days")
        return net.cumsum(), pct, n

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

    scorers = make_scorers(F, Y, B)

    # ---- today, a year ago, monthly history
    now_comp, tiles = composite(scorers, TODAY)
    ago_comp, ago_tiles = composite(scorers, TODAY - pd.DateOffset(years=1))
    for t, a in zip(tiles, ago_tiles):
        t["s_ago"], t["state_ago"] = a["s"], a["state"]
    hist, counts = [], {t[0]: {"tailwind": 0, "neutral": 0, "headwind": 0} for t in TILES}
    dates = list(pd.date_range(TODAY - pd.DateOffset(years=HIST_YEARS), TODAY, freq="ME"))
    for asof in dates + [TODAY]:
        c, ts = composite(scorers, asof)
        for t in ts:
            if t["state"] in counts[t["id"]]:
                counts[t["id"]][t["state"]] += 1
        if c:
            hist.append({"date": asof.strftime("%Y-%m-%d"), "score": c["score"],
                         "agreement": c["agreement"], "stance": c["stance"]})
    for t in tiles:
        c = counts[t["id"]]
        n = sum(c.values())
        t["history"] = {"months": n, **{k: round(v / n, 3) for k, v in c.items()}} if n else None

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
             "series": S([ser("ICE BofA US High Yield OAS", F["hy"], d5, 2)]), "stats": S([stat("HY spread", F["hy"], "%")])},
            {"id": "ig", "tile": "ig", "title": "Investment-grade option-adjusted spread", "unit": "%",
             "series": S([ser("ICE BofA US Corporate OAS", F["ig"], d5, 2)]), "stats": S([stat("IG spread", F["ig"], "%")])},
        ]},
        {"title": "Market internals", "charts": [
            {"id": "ad", "tile": "ad", "title": "S&P 500 advance-decline line", "unit": "",
             "series": S([ser("Cumulative advances minus declines", B.get("ad"), None, 0)]),
             "note": (f"Built from the {n_const} current S&P 500 members (survivorship-biased), as a stand-in "
                      "for the NYSE A/D line, which has no reliable free feed.") if n_const else None},
            {"id": "spx", "tile": None, "title": "S&P 500 index", "unit": "",
             "series": S([ser("S&P 500", Y.get("^GSPC"), d5, 2)]), "stats": S([stat("S&P 500", Y.get("^GSPC"), "", 0)])},
            {"id": "pct200", "tile": "pct200", "title": "Share of S&P 500 stocks above their 200-day average", "unit": "%",
             "series": S([ser("% above 200-day", B.get("pct200"), None, 1)]),
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

    n_ok = sum(1 for t in tiles if t["s"] is not None)
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fred_mode": "api" if FRED_KEY else "public_csv",
           "now": now_comp, "year_ago": ago_comp, "tiles": tiles, "history": hist,
           "sections": sections, "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    log(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB): {n_ok}/{len(tiles)} tiles scored, "
        f"stance {now_comp['label'] if now_comp else 'n/a'} "
        f"(score {now_comp['score'] if now_comp else 'n/a'}), {len(errors)} errors")
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
