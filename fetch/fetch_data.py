"""Build site/data/dashboard.json for the Druckenmiller macro dashboard.

Data sources (all free):
  * FRED (St. Louis Fed). Uses the official API when the FRED_API_KEY
    environment variable is set (store it as a GitHub Actions secret, never in
    the repo); otherwise falls back to FRED's public fredgraph.csv download.
  * Yahoo Finance via the yfinance package (no key) for futures, ETFs, the
    S&P 500 index and S&P 500 constituents (used to compute an A/D line).
  * S&P 500 constituent list from the datasets/s-and-p-500-companies repo,
    with Wikipedia as a fallback.

Every series is fetched independently. A failure is recorded in the JSON
"errors" list and the matching panel shows "data unavailable"; the build only
fails if nothing at all could be fetched.

Signal rules (Supportive / Mixed / Headwind) are simple, transparent rules
written for this dashboard, not Druckenmiller's own thresholds. They are
documented next to each rule below and on the page.
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path(__file__).resolve().parents[1] / "site" / "data" / "dashboard.json"
UA = {"User-Agent": "macro-dashboard/1.0 (GitHub Actions; personal research dashboard)"}
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
TODAY = pd.Timestamp(datetime.now(timezone.utc).date())
MACRO_START = TODAY - pd.DateOffset(years=12)
MARKET_YEARS = 6

errors: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- fetchers --

def fred(series_id: str, start: pd.Timestamp = MACRO_START) -> pd.Series:
    """One FRED series as a float Series indexed by date (NaNs dropped)."""
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
                r = requests.get(
                    "https://fred.stlouisfed.org/graph/fredgraph.csv",
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


def yahoo_close(tickers: list[str], years: int = MARKET_YEARS) -> pd.DataFrame:
    """Daily adjusted closes for tickers (columns), via yfinance."""
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
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out = out.loc[:, ~out.columns.duplicated()].sort_index()
    return out


def sp500_tickers() -> list[str]:
    urls = [
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    ]
    for u in urls:
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
    syms = tables[0]["Symbol"].astype(str).tolist()
    return [s.replace(".", "-") for s in syms]


# ----------------------------------------------------------------- helpers --

def clean(s: pd.Series | None) -> pd.Series | None:
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").dropna()
    return s if not s.empty else None


def value_at(s: pd.Series, when: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
    """Last observation on or before `when`."""
    s = s[s.index <= when]
    if s.empty:
        return None
    return float(s.iloc[-1]), s.index[-1]


def yoy(s: pd.Series, periods: int) -> pd.Series:
    return (s / s.shift(periods) - 1.0) * 100.0


def to_monthly_mean(s: pd.Series) -> pd.Series:
    m = s.resample("MS").mean().dropna()
    return m


def ser(name: str, s: pd.Series | None, start: pd.Timestamp | None = None,
        digits: int = 3, style: str = "solid") -> dict | None:
    s = clean(s)
    if s is None:
        return None
    if start is not None:
        s = s[s.index >= start]
    return {
        "name": name,
        "style": style,
        "x": [d.strftime("%Y-%m-%d") for d in s.index],
        "y": [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), digits)
              for v in s.values],
    }


def stat(label: str, s: pd.Series | None, unit: str, digits: int = 2,
         asof: pd.Timestamp | None = None) -> dict | None:
    s = clean(s)
    if s is None:
        return None
    asof = asof or s.index[-1]
    now = value_at(s, asof)
    ago = value_at(s, asof - pd.DateOffset(years=1))
    if now is None:
        return None
    out = {"label": label, "unit": unit, "digits": digits,
           "now": round(now[0], 4), "now_date": now[1].strftime("%Y-%m-%d")}
    if ago is not None:
        out["year_ago"] = round(ago[0], 4)
        out["year_ago_date"] = ago[1].strftime("%Y-%m-%d")
    return out


def signal(state: str, text: str) -> dict:
    assert state in {"supportive", "mixed", "headwind", "context", "unavailable"}
    return {"state": state, "text": text}


def safe(fn, label):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        msg = f"{label}: {e}"
        log("ERROR " + msg)
        traceback.print_exc()
        errors.append(msg)
        return None


# -------------------------------------------------------- signal rules -----
# Each rule takes data truncated at `asof` so we can show "now" and
# "a year ago" with the same rule.

def trunc(s: pd.Series | None, asof: pd.Timestamp) -> pd.Series | None:
    if s is None:
        return None
    s = s[s.index <= asof]
    return s if not s.empty else None


def rule_policy(gap: pd.Series | None, asof) -> dict:
    """Fed funds minus nominal GDP growth. Below -1 pt: loose; above +1: tight."""
    g = trunc(gap, asof)
    if g is None:
        return signal("unavailable", "No data")
    v = float(g.iloc[-1])
    if v < -1:
        return signal("supportive", f"Loose: fed funds {abs(v):.1f} pts below nominal growth")
    if v > 1:
        return signal("headwind", f"Tight: fed funds {v:.1f} pts above nominal growth")
    return signal("mixed", f"Near neutral: gap of {v:+.1f} pts")


def rule_fedpath(spread: pd.Series | None, upper: pd.Series | None, asof) -> dict:
    """2-yr Treasury minus fed funds (market-implied path), plus the target's
    6-month change. Pricing hikes or recent hikes: headwind."""
    sp = trunc(spread, asof)
    up = trunc(upper, asof)
    if sp is None:
        return signal("unavailable", "No data")
    v = float(sp.iloc[-1])
    moved = None
    if up is not None:
        prev = value_at(up, asof - pd.DateOffset(months=6))
        if prev is not None:
            moved = float(up.iloc[-1]) - prev[0]
    hiking = moved is not None and moved > 0.01
    cutting = moved is not None and moved < -0.01
    move_txt = "" if moved is None else (
        f"; target {'up' if hiking else 'down' if cutting else 'unchanged'} over 6 mo")
    if v > 0.25 or hiking:
        return signal("headwind", f"2-yr {v:+.2f} vs fed funds{move_txt}")
    if v < -0.25 or cutting:
        return signal("supportive", f"2-yr {v:+.2f} vs fed funds{move_txt}")
    return signal("mixed", f"2-yr {v:+.2f} vs fed funds{move_txt}")


def rule_trend(px: pd.Series | None, asof, name: str) -> dict:
    """Price vs 200-day average and 52-week high. Within 5% of the high and
    above a rising 200-day: breaking out. Below a falling 200-day: topping."""
    p = trunc(px, asof)
    if p is None or len(p) < 260:
        return signal("unavailable", "Not enough history")
    ma = p.rolling(200).mean()
    last, ma_now, ma_prev = float(p.iloc[-1]), float(ma.iloc[-1]), float(ma.iloc[-22])
    hi = float(p.iloc[-252:].max())
    off_hi = (last / hi - 1) * 100
    rising = ma_now > ma_prev
    if last > ma_now and rising and off_hi > -5:
        return signal("supportive", f"{name} {off_hi:.1f}% from 52-wk high, above rising 200-day")
    if last < ma_now and not rising:
        return signal("headwind", f"{name} below a falling 200-day, {off_hi:.1f}% off high")
    return signal("mixed", f"{name} {off_hi:.1f}% off 52-wk high; 200-day {'rising' if rising else 'falling'}")


def rule_breadth(ad: pd.Series | None, idx: pd.Series | None, asof) -> dict:
    """Date of the A/D line's 52-week high vs the index's. If the index made a
    new high more than 20 trading days after the A/D line's: divergence."""
    a, i = trunc(ad, asof), trunc(idx, asof)
    if a is None or i is None or len(a) < 260:
        return signal("unavailable", "No breadth data")
    a, i = a.iloc[-252:], i.iloc[-252:]
    ad_hi, ix_hi = a.idxmax(), i.idxmax()
    lag = int(((i.index > ad_hi) & (i.index <= ix_hi)).sum())
    ad_txt, ix_txt = ad_hi.strftime("%b %-d, %Y"), ix_hi.strftime("%b %-d, %Y")
    recent = int((a.index > ad_hi).sum())
    if lag > 20:
        return signal("headwind", f"A/D high {ad_txt}; S&P high {ix_txt}: divergence")
    if recent <= 10:
        return signal("supportive", f"A/D line at or near a new high ({ad_txt})")
    return signal("mixed", f"A/D high {ad_txt}; S&P high {ix_txt}")


def rule_credit(hy: pd.Series | None, asof) -> dict:
    """HY option-adjusted spread vs 3 months earlier. Wider by >30 bp:
    widening (headwind); tighter by >10 bp: tightening (supportive)."""
    h = trunc(hy, asof)
    if h is None:
        return signal("unavailable", "No data")
    prev = value_at(h, asof - pd.DateOffset(months=3))
    if prev is None:
        return signal("unavailable", "Not enough history")
    d = (float(h.iloc[-1]) - prev[0]) * 100
    lvl = float(h.iloc[-1])
    if d > 30:
        return signal("headwind", f"HY spread {lvl:.2f}%, wider by {d:.0f} bp in 3 mo")
    if d < -10:
        return signal("supportive", f"HY spread {lvl:.2f}%, tighter by {abs(d):.0f} bp in 3 mo")
    return signal("mixed", f"HY spread {lvl:.2f}%, {d:+.0f} bp in 3 mo")


def rule_cyc(ratio: pd.Series | None, asof) -> dict:
    """Cyclicals/defensives ratio vs its 200-day average and 3-month change."""
    r = trunc(ratio, asof)
    if r is None or len(r) < 220:
        return signal("unavailable", "Not enough history")
    ma = float(r.rolling(200).mean().iloc[-1])
    last = float(r.iloc[-1])
    chg = (last / float(r.iloc[-64]) - 1) * 100
    if last > ma and chg > 0:
        return signal("supportive", f"Cyclicals leading: ratio {chg:+.1f}% over 3 mo")
    if last < ma and chg < 0:
        return signal("headwind", f"Defensives leading: ratio {chg:+.1f}% over 3 mo")
    return signal("mixed", f"No clear leader: ratio {chg:+.1f}% over 3 mo")


def rule_inflation(core: pd.Series | None, asof) -> dict:
    """Core PCE inflation vs the 2% target, with the run of months above it."""
    c = trunc(core, asof)
    if c is None:
        return signal("unavailable", "No data")
    v = float(c.iloc[-1])
    above = c > 2.0
    run = 0
    for flag in reversed(above.values):
        if not flag:
            break
        run += 1
    run_txt = f"; above 2% for {run} months" if run else ""
    if v > 2.5:
        return signal("headwind", f"Core PCE {v:.1f}%{run_txt}")
    if v >= 2.0:
        return signal("mixed", f"Core PCE {v:.1f}%{run_txt}")
    return signal("supportive", f"Core PCE {v:.1f}%, below target")


# ------------------------------------------------------------------ build --

def main() -> int:
    log(f"FRED mode: {'API key' if FRED_KEY else 'public CSV (no key)'}")
    F = {}
    fred_ids = {
        "effr": "DFF", "upper": "DFEDTARU", "lower": "DFEDTARL",
        "gdp": "GDP", "dgs2": "DGS2", "dgs10": "DGS10", "t10y2y": "T10Y2Y",
        "sep_median": "FEDTARMD", "sep_lr": "FEDTARMDLR",
        "ig": "BAMLC0A0CM", "hy": "BAMLH0A0HYM2",
        "core_pce": "PCEPILFE", "cpi": "CPIAUCSL", "be5": "T5YIE",
        "broad_usd": "DTWEXBGS",
        "deficit_gdp": "FYFSGDA188S", "debt_gdp": "GFDEGDQ188S",
        "interest": "A091RC1Q027SBEA", "mts": "MTSDS133FMS",
        "us10_m": "IRLTLT01USM156N", "fr10_m": "IRLTLT01FRM156N",
    }
    long_start = TODAY - pd.DateOffset(years=30)
    for k, sid in fred_ids.items():
        start = long_start if k in {"deficit_gdp", "debt_gdp", "interest", "gdp"} else MACRO_START
        if k in {"sep_median", "sep_lr"}:
            start = TODAY - pd.DateOffset(years=3)
        F[k] = safe(lambda sid=sid, start=start: fred(sid, start), f"FRED {sid}")

    Y = {}
    mkt = ["HG=F", "GC=F", "DBC", "^GSPC", "RSP", "SPY", "DX-Y.NYB",
           "XLY", "XLI", "XLB", "XLF", "XLP", "XLU", "XLV"]
    closes = safe(lambda: yahoo_close(mkt), "Yahoo market data")
    if closes is not None:
        for t in mkt:
            if t in closes.columns:
                s = clean(closes[t])
                if s is None:
                    errors.append(f"Yahoo {t}: no data")
                Y[t] = s
            else:
                errors.append(f"Yahoo {t}: missing")
                Y[t] = None

    # Breadth from S&P 500 constituents (current members, so survivorship-biased).
    def build_ad():
        tick = sp500_tickers()
        px = yahoo_close(tick, years=3)
        px = px.dropna(axis=1, how="all")
        chg = px.diff()
        counted = chg.notna().sum(axis=1)
        adv = (chg > 0).sum(axis=1)
        dec = (chg < 0).sum(axis=1)
        net = (adv - dec)[counted >= 0.8 * px.shape[1]]
        net = net.iloc[1:]
        return net.cumsum(), net, int(px.shape[1])

    ad_res = safe(build_ad, "S&P 500 A/D line")
    ad_line = ad_res[0] if ad_res else None
    n_const = ad_res[2] if ad_res else None

    now = TODAY
    year_ago = TODAY - pd.DateOffset(years=1)
    disp5 = TODAY - pd.DateOffset(years=5)
    disp10 = TODAY - pd.DateOffset(years=10)

    panels = []

    # 1. Policy stance: fed funds vs nominal GDP growth --------------------
    ngdp = yoy(F["gdp"], 4).dropna() if F["gdp"] is not None else None
    effr_q = F["effr"].resample("QS").mean() if F["effr"] is not None else None
    gap = None
    if ngdp is not None and effr_q is not None:
        gap = (effr_q.reindex(ngdp.index) - ngdp).dropna()
        # Latest reading uses today's fed funds against the latest GDP print,
        # which is how Druckenmiller frames it.
        if F["effr"] is not None:
            gap.loc[F["effr"].index[-1]] = float(F["effr"].iloc[-1]) - float(ngdp.iloc[-1])
            gap = gap.sort_index()
    panels.append({
        "id": "policy", "group": "Monetary policy",
        "title": "Fed funds vs nominal GDP growth",
        "read": "His test of how loose policy is: in January fed funds were 3.5% against 6% nominal growth, which he called ridiculously loose.",
        "unit": "%",
        "series": [s for s in [
            ser("Fed funds (effective)", F["effr"], disp10, 2),
            ser("Nominal GDP growth, y/y", ngdp, disp10, 2),
            ser("Gap (fed funds minus NGDP)", gap, disp10, 2, "dot"),
        ] if s],
        "refs": [{"y": 0, "label": ""}],
        "stats": [s for s in [stat("Fed funds", F["effr"], "%"),
                              stat("Nominal GDP y/y", ngdp, "%", 1),
                              stat("Gap", gap, "pts", 1)] if s],
        "signal_now": rule_policy(gap, now), "signal_ago": rule_policy(gap, year_ago),
        "rule": "Gap below -1 pt: Supportive (loose). Above +1 pt: Headwind (tight). Otherwise Mixed.",
        "source": "FRED: DFF, GDP",
        "scored": True,
    })

    # 2. Fed path ------------------------------------------------------------
    spread2 = None
    if F["dgs2"] is not None and F["effr"] is not None:
        spread2 = (F["dgs2"] - F["effr"].reindex(F["dgs2"].index).ffill()).dropna()
    sep = []
    if F["sep_median"] is not None:
        latest = F["sep_median"]
        sep = [{"year": d.year, "median": round(float(v), 3)} for d, v in latest.items()
               if d.year >= TODAY.year - 1][-4:]
    if F["sep_lr"] is not None:
        sep.append({"year": "Longer run", "median": round(float(F["sep_lr"].iloc[-1]), 3)})
    panels.append({
        "id": "fedpath", "group": "Monetary policy",
        "title": "Where the Fed is headed",
        "read": "He watches whether the Fed is cutting or hiking. In January the dot plot showed more cuts; now he says the Fed is hiking.",
        "unit": "%",
        "series": [s for s in [
            ser("Fed funds target, upper", F["upper"], disp5, 2),
            ser("2-yr Treasury", F["dgs2"], disp5, 2),
        ] if s],
        "refs": [],
        "stats": [s for s in [stat("2-yr minus fed funds", spread2, "pts"),
                              stat("Target, upper bound", F["upper"], "%")] if s],
        "sep": sep,
        "signal_now": rule_fedpath(spread2, F["upper"], now),
        "signal_ago": rule_fedpath(spread2, F["upper"], year_ago),
        "rule": "2-yr more than 25 bp above fed funds, or the target raised in the last 6 months: Headwind. More than 25 bp below, or the target cut: Supportive.",
        "source": "FRED: DFEDTARU, DGS2, DFF, FEDTARMD (SEP median dots)",
        "scored": True,
    })

    # 3. Copper -------------------------------------------------------------
    cu = Y.get("HG=F")
    panels.append({
        "id": "copper", "group": "Commodities",
        "title": "Copper futures",
        "read": "Copper was breaking out to new highs in January. He now thinks commodities have topped and is short copper.",
        "unit": "$/lb",
        "series": [s for s in [ser("Copper (front month)", cu, disp5, 4),
                               ser("200-day average", cu.rolling(200).mean() if cu is not None else None, disp5, 4, "dash")] if s],
        "refs": [],
        "stats": [s for s in [stat("Copper", cu, "$/lb", 3)] if s],
        "signal_now": rule_trend(cu, now, "Copper"), "signal_ago": rule_trend(cu, year_ago, "Copper"),
        "rule": "Above a rising 200-day and within 5% of the 52-week high: Supportive (breaking out). Below a falling 200-day: Headwind (topping). Otherwise Mixed.",
        "source": "Yahoo Finance: HG=F",
        "scored": True,
    })

    # 4. Broad commodities ---------------------------------------------------
    dbc = Y.get("DBC")
    panels.append({
        "id": "commod", "group": "Commodities",
        "title": "Broad commodities",
        "read": "Commodities broadly were breaking out to new highs in January; now they look like they've topped and are going down.",
        "unit": "$",
        "series": [s for s in [ser("Invesco DB Commodity Index (DBC)", dbc, disp5, 3),
                               ser("200-day average", dbc.rolling(200).mean() if dbc is not None else None, disp5, 3, "dash")] if s],
        "refs": [],
        "stats": [s for s in [stat("DBC", dbc, "$")] if s],
        "signal_now": rule_trend(dbc, now, "DBC"), "signal_ago": rule_trend(dbc, year_ago, "DBC"),
        "rule": "Same trend rule as copper.",
        "source": "Yahoo Finance: DBC (total-return adjusted)",
        "scored": True,
    })

    # 5. Breadth -------------------------------------------------------------
    spx = Y.get("^GSPC")
    rsp_spy = None
    if Y.get("RSP") is not None and Y.get("SPY") is not None:
        rsp_spy = (Y["RSP"] / Y["SPY"]).dropna()
        rsp_spy = rsp_spy / rsp_spy[rsp_spy.index >= disp5].iloc[0] * 100
    breadth_note = (f"A/D line computed from daily closes of the {n_const} current S&P 500 members "
                    "(so it carries survivorship bias); it is a stand-in for the NYSE A/D line, which "
                    "has no reliable free feed.") if n_const else (
                    "S&P 500 A/D line unavailable this run; equal-weight vs cap-weight ratio shown.")
    panels.append({
        "id": "breadth", "group": "Equities",
        "title": "Breadth: advance-decline line vs the S&P 500",
        "read": "His tell: the A/D line peaked on August 14 while the averages kept making highs into October. Narrowing breadth under new index highs is a warning.",
        "unit": "",
        "subplots": [
            {"title": "S&P 500 A/D line (cumulative advances minus declines)", "unit": "",
             "series": [s for s in [ser("A/D line", ad_line, None, 0)] if s]},
            {"title": "S&P 500 index", "unit": "",
             "series": [s for s in [ser("S&P 500", spx[spx.index >= (ad_line.index[0] if ad_line is not None else disp5)] if spx is not None else None, None, 2)] if s]},
            {"title": "Equal-weight vs cap-weight S&P 500 (RSP/SPY, indexed)", "unit": "",
             "series": [s for s in [ser("RSP / SPY", rsp_spy, disp5, 2)] if s]},
        ],
        "stats": [s for s in [stat("S&P 500", spx, "", 0), stat("RSP/SPY index", rsp_spy, "", 1)] if s],
        "signal_now": rule_breadth(ad_line, spx, now), "signal_ago": rule_breadth(ad_line, spx, year_ago),
        "rule": "S&P 500 makes its 52-week high more than 20 trading days after the A/D line's high: Headwind (divergence). A/D line at a new high within the last 10 days: Supportive.",
        "note": breadth_note,
        "source": "Yahoo Finance: S&P 500 constituents, ^GSPC, RSP, SPY",
        "scored": True,
    })

    # 6. Credit ----------------------------------------------------------------
    panels.append({
        "id": "credit", "group": "Credit",
        "title": "Corporate credit spreads",
        "read": "Credit spreads were very, very tight in January. Now he says they are widening.",
        "unit": "%",
        "subplots": [
            {"title": "High yield option-adjusted spread", "unit": "%",
             "series": [s for s in [ser("High yield OAS", F["hy"], disp5, 2)] if s]},
            {"title": "Investment grade option-adjusted spread", "unit": "%",
             "series": [s for s in [ser("Investment grade OAS", F["ig"], disp5, 2)] if s]},
        ],
        "stats": [s for s in [stat("HY OAS", F["hy"], "%"), stat("IG OAS", F["ig"], "%")] if s],
        "signal_now": rule_credit(F["hy"], now), "signal_ago": rule_credit(F["hy"], year_ago),
        "rule": "HY spread wider by more than 30 bp over 3 months: Headwind. Tighter by more than 10 bp: Supportive.",
        "source": "FRED: BAMLH0A0HYM2, BAMLC0A0CM (ICE BofA)",
        "scored": True,
    })

    # 7. Cyclicals vs defensives ------------------------------------------------
    cyc_ratio = None
    cyc, dfn = ["XLY", "XLI", "XLB", "XLF"], ["XLP", "XLU", "XLV"]
    if all(Y.get(t) is not None for t in cyc + dfn):
        px = pd.concat({t: Y[t] for t in cyc + dfn}, axis=1).dropna()
        rel = px / px.iloc[0]
        cyc_ratio = rel[cyc].mean(axis=1) / rel[dfn].mean(axis=1)
        cyc_ratio = cyc_ratio / cyc_ratio[cyc_ratio.index >= disp5].iloc[0] * 100
    panels.append({
        "id": "cyc", "group": "Equities",
        "title": "Cyclicals vs defensives",
        "read": "A year ago cyclical stocks were outperforming defensives; now defensives lead, which makes him alert to a disappointing second half of 2027.",
        "unit": "",
        "series": [s for s in [ser("Cyclicals / defensives (indexed)", cyc_ratio, disp5, 2),
                               ser("200-day average", cyc_ratio.rolling(200).mean() if cyc_ratio is not None else None, disp5, 2, "dash")] if s],
        "refs": [{"y": 100, "label": ""}],
        "stats": [s for s in [stat("Ratio", cyc_ratio, "", 1)] if s],
        "signal_now": rule_cyc(cyc_ratio, now), "signal_ago": rule_cyc(cyc_ratio, year_ago),
        "rule": "Ratio above its 200-day and up over 3 months: Supportive. Below and down: Headwind.",
        "note": "Cyclicals = equal-weight XLY, XLI, XLB, XLF. Defensives = equal-weight XLP, XLU, XLV.",
        "source": "Yahoo Finance: sector SPDR ETFs",
        "scored": True,
    })

    # 8. Inflation ----------------------------------------------------------------
    core = yoy(F["core_pce"], 12).dropna() if F["core_pce"] is not None else None
    cpi = yoy(F["cpi"], 12).dropna() if F["cpi"] is not None else None
    panels.append({
        "id": "inflation", "group": "Inflation",
        "title": "Inflation vs the 2% target",
        "read": "He stresses that inflation has been above target for five years, a reason the new Fed is tightening.",
        "unit": "%",
        "series": [s for s in [ser("Core PCE, y/y", core, disp10, 2),
                               ser("CPI, y/y", cpi, disp10, 2),
                               ser("5-yr breakeven", F["be5"], disp10, 2)] if s],
        "refs": [{"y": 2, "label": "2% target"}],
        "stats": [s for s in [stat("Core PCE y/y", core, "%", 1), stat("CPI y/y", cpi, "%", 1),
                              stat("5-yr breakeven", F["be5"], "%")] if s],
        "signal_now": rule_inflation(core, now), "signal_ago": rule_inflation(core, year_ago),
        "rule": "Core PCE above 2.5%: Headwind. 2 to 2.5%: Mixed. Below 2%: Supportive.",
        "source": "FRED: PCEPILFE, CPIAUCSL, T5YIE",
        "scored": True,
    })

    # 9. Dollar (context) ---------------------------------------------------------
    dxy = Y.get("DX-Y.NYB")
    panels.append({
        "id": "dollar", "group": "Currencies",
        "title": "The dollar",
        "read": "The one place he still has positions, small ones. He notes currencies and bonds tend to make their biggest moves in equity bear markets.",
        "unit": "",
        "subplots": [
            {"title": "US Dollar Index (DXY)", "unit": "",
             "series": [s for s in [ser("DXY", dxy, disp5, 2)] if s]},
            {"title": "Fed broad trade-weighted dollar", "unit": "",
             "series": [s for s in [ser("Broad dollar index", F["broad_usd"], disp5, 2)] if s]},
        ],
        "stats": [s for s in [stat("DXY", dxy, "", 1), stat("Broad dollar", F["broad_usd"], "", 1)] if s],
        "signal_now": signal("context", "Context only; not scored"),
        "signal_ago": signal("context", "Context only; not scored"),
        "rule": "Not scored: he gives no direction for the dollar.",
        "source": "Yahoo Finance: DX-Y.NYB; FRED: DTWEXBGS",
        "scored": False,
    })

    # 10. Rates (context) ---------------------------------------------------------
    panels.append({
        "id": "rates", "group": "Rates",
        "title": "Treasury yields and the curve",
        "read": "With defensives leading he says he is thinking about a weaker second half of 2027 in terms of interest rates.",
        "unit": "%",
        "series": [s for s in [ser("10-yr Treasury", F["dgs10"], disp5, 2),
                               ser("2-yr Treasury", F["dgs2"], disp5, 2),
                               ser("10-yr minus 2-yr", F["t10y2y"], disp5, 2, "dot")] if s],
        "refs": [{"y": 0, "label": ""}],
        "stats": [s for s in [stat("10-yr", F["dgs10"], "%"), stat("10s minus 2s", F["t10y2y"], "pts")] if s],
        "signal_now": signal("context", "Context only; not scored"),
        "signal_ago": signal("context", "Context only; not scored"),
        "rule": "Not scored.",
        "source": "FRED: DGS10, DGS2, T10Y2Y",
        "scored": False,
    })

    # 11. Fiscal (context) --------------------------------------------------------
    roll_def = None
    if F["mts"] is not None and F["gdp"] is not None:
        r12 = F["mts"].rolling(12).sum().dropna() / 1000.0  # $ billions
        g = F["gdp"].resample("MS").ffill().reindex(r12.index, method="ffill")
        roll_def = (r12 / g * 100).dropna()
    int_gdp = None
    if F["interest"] is not None and F["gdp"] is not None:
        int_gdp = (F["interest"] / F["gdp"].reindex(F["interest"].index) * 100).dropna()
    us_fr = None
    if F["us10_m"] is not None and F["fr10_m"] is not None:
        us_fr = (F["fr10_m"] - F["us10_m"]).dropna()
    disp30 = TODAY - pd.DateOffset(years=30)
    panels.append({
        "id": "fiscal", "group": "Fiscal",
        "title": "Deficits, debt and the bond market",
        "read": "His slow-moving worry: deficits and entitlements, 'gradually and then suddenly'. He expects fiscal reform within 10 years, forced by a market crisis like France's.",
        "unit": "%",
        "subplots": [
            {"title": "Federal deficit, % of GDP (12-month rolling; negative = deficit)", "unit": "%",
             "series": [s for s in [ser("Rolling 12-mo balance / GDP", roll_def, disp10, 2),
                                    ser("Fiscal-year balance / GDP", F["deficit_gdp"], disp30, 2, "dot")] if s],
             "refs": [{"y": 0, "label": ""}]},
            {"title": "Federal debt and interest, % of GDP", "unit": "%",
             "series": [s for s in [ser("Federal debt / GDP", F["debt_gdp"], disp30, 1)] if s]},
            {"title": "Federal interest payments, % of GDP", "unit": "%",
             "series": [s for s in [ser("Interest payments / GDP", int_gdp, disp30, 2)] if s]},
            {"title": "10-yr yields: France minus US (monthly)", "unit": "pts",
             "series": [s for s in [ser("France minus US 10-yr", us_fr, disp10, 2)] if s],
             "refs": [{"y": 0, "label": ""}]},
        ],
        "stats": [s for s in [stat("Debt / GDP", F["debt_gdp"], "%", 1),
                              stat("12-mo deficit / GDP", roll_def, "%", 1),
                              stat("Interest / GDP", int_gdp, "%", 1)] if s],
        "signal_now": signal("context", "Context only; not scored"),
        "signal_ago": signal("context", "Context only; not scored"),
        "rule": "Not scored: a 10-year risk, not a 12-month signal.",
        "source": "FRED: MTSDS133FMS, FYFSGDA188S, GFDEGDQ188S, A091RC1Q027SBEA, GDP, IRLTLT01USM156N, IRLTLT01FRM156N",
        "scored": False,
    })

    # Copper/gold as a related growth gauge, placed with commodities.
    cg = None
    if Y.get("HG=F") is not None and Y.get("GC=F") is not None:
        cg = (Y["HG=F"] / Y["GC=F"] * 1000).dropna()
    panels.insert(4, {
        "id": "cugold", "group": "Commodities",
        "title": "Copper / gold ratio",
        "read": "Not named by him, but a cheap companion to his copper call: copper (growth) relative to gold (safety) tends to track growth expectations and yields.",
        "unit": "",
        "series": [s for s in [ser("Copper / gold (x1000)", cg, disp5, 3)] if s],
        "refs": [],
        "stats": [s for s in [stat("Copper/gold x1000", cg, "", 2)] if s],
        "signal_now": signal("context", "Context only; not scored"),
        "signal_ago": signal("context", "Context only; not scored"),
        "rule": "Not scored.",
        "source": "Yahoo Finance: HG=F, GC=F",
        "scored": False,
    })

    n_ok = sum(1 for p in panels if p.get("series") or any(sp["series"] for sp in p.get("subplots", [])))
    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fred_mode": "api" if FRED_KEY else "public_csv",
        "errors": errors,
        "panels": panels,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    log(f"Wrote {OUT} ({OUT.stat().st_size/1e6:.2f} MB), {n_ok}/{len(panels)} panels with data, {len(errors)} errors")
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
