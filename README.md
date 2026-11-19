# Druck macro map

A daily-refreshed tailwind/headwind map of the macro signals Stan Druckenmiller described watching in his November 2026 interview: fed funds vs nominal GDP growth, the Fed's direction and liquidity, inflation, credit spreads, breadth (A/D line, participation), cyclicals vs defensives, copper and commodities, and the dollar. Each signal gets a score from -1 (headwind) to +1 (tailwind); the weighted composite and how much the signals agree give an overall stance (bullish, leaning, or no clarity). The Guide tab on the site states and defends every threshold and weight.

## How it works

- `fetch/fetch_data.py` pulls every series, computes derived series, tile scores, the composite and its month-end history (macro data lagged to its release date), and writes `site/data/dashboard.json`.
- Each signal is a metric in natural units with a neutral point and a full-score distance: score = clip((metric − neutral) / distance, −1, 1). Defaults live in `TILES`.
- The JSON also carries every signal's metric at each month-end since 2000 and the realized forward 6- and 12-month S&P 500 total return (`backtest`). The fetcher logs the default score's backtest stats (correlation, rank IC, Newey-West t, non-overlapping correlation, train/test split, IC-fitted weights, walk-forward) in the Actions job log.
- `site/` is a static page (Plotly charts) that reads that JSON. Its Backtest tab recomputes the score and the stats in the browser as you move the weight and threshold sliders.
- `.github/workflows/update.yml` runs the fetcher each weekday at 22:30 UTC, on every push to `main`, and on demand, then deploys `site/` to GitHub Pages. Data is not committed to the repo.

## Data sources

| Signal | Series | Source |
|---|---|---|
| Policy stance | Effective fed funds (DFF), nominal GDP (GDP) y/y | FRED |
| Fed path | Target upper bound (DFEDTARU), 2-yr Treasury (DGS2) minus fed funds, SEP median dots (FEDTARMD, FEDTARMDLR) | FRED |
| Copper | Front-month COMEX copper (HG=F) vs 200-day average | Yahoo Finance |
| Broad commodities | Invesco DB Commodity Index Tracking Fund (DBC) | Yahoo Finance |
| Copper / gold | HG=F / GC=F | Yahoo Finance |
| Breadth | Cumulative advances minus declines across current S&P 500 members; RSP/SPY | Yahoo Finance; constituents from datasets/s-and-p-500-companies |
| Credit | ICE BofA HY OAS (BAMLH0A0HYM2; FRED serves only ~3 years), Moody's Baa minus 10-yr (BAA10Y), IG OAS (BAMLC0A0CM, chart only) | FRED |
| Forward returns (backtest) | S&P 500 total return index (^SP500TR), falling back to price (^GSPC) | Yahoo Finance |
| Cyclicals vs defensives | Equal-weight XLY, XLI, XLB, XLF vs XLP, XLU, XLV | Yahoo Finance |
| Inflation | Core PCE (PCEPILFE), CPI (CPIAUCSL) y/y, 5-yr breakeven (T5YIE) | FRED |
| Fed liquidity | Fed assets (WALCL) minus TGA (WTREGEN) minus reverse repo (RRPONTSYD) | FRED |
| Participation | Share of S&P 500 members above their 200-day average | Yahoo Finance |
| Big commodities (charts only) | WTI (CL=F), natural gas (NG=F), gold (GC=F), copper (HG=F), soybeans (ZS=F), corn (ZC=F) | Yahoo Finance |
| Dollar | DXY (DX-Y.NYB) | Yahoo Finance |
| Rates | DGS10, DGS2, T10Y2Y | FRED |
| Fiscal (charts only) | Federal debt / GDP (GFDEGDQ188S), France minus US 10-yr (IRLTLT01FRM156N, IRLTLT01USM156N) | FRED |

The breadth line is a stand-in for the NYSE A/D line, which has no reliable free feed. It uses today's S&P 500 members, so it carries survivorship bias.

## FRED API key

The fetcher works without a key by using FRED's public CSV download. To use the official API instead, add a repository secret named `FRED_API_KEY` (Settings → Secrets and variables → Actions). Never commit the key.

## Run locally

```
pip install -r fetch/requirements.txt
python fetch/fetch_data.py
cd site && python -m http.server 8000
```
