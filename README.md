# Macro checklist

A daily-refreshed dashboard of the macro signals Stan Druckenmiller described watching in his November 2026 interview: fed funds vs nominal GDP growth, the Fed's path, copper and broad commodities, the advance-decline line vs the S&P 500, credit spreads, cyclicals vs defensives, and inflation vs target, with the dollar, Treasury yields and the fiscal backdrop as context.

## How it works

- `fetch/fetch_data.py` pulls every series, computes derived series and the signal labels, and writes `site/data/dashboard.json`.
- `site/` is a static page (Plotly charts) that reads that JSON.
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
| Credit | ICE BofA HY OAS (BAMLH0A0HYM2), IG OAS (BAMLC0A0CM) | FRED |
| Cyclicals vs defensives | Equal-weight XLY, XLI, XLB, XLF vs XLP, XLU, XLV | Yahoo Finance |
| Inflation | Core PCE (PCEPILFE), CPI (CPIAUCSL) y/y, 5-yr breakeven (T5YIE) | FRED |
| Dollar | DXY (DX-Y.NYB), broad dollar (DTWEXBGS) | Yahoo Finance, FRED |
| Rates | DGS10, DGS2, T10Y2Y | FRED |
| Fiscal | Monthly Treasury statement balance (MTSDS133FMS, 12-mo sum / GDP), FYFSGDA188S, GFDEGDQ188S, interest payments (A091RC1Q027SBEA) / GDP, France minus US 10-yr (IRLTLT01FRM156N, IRLTLT01USM156N) | FRED |

The breadth line is a stand-in for the NYSE A/D line, which has no reliable free feed. It uses today's S&P 500 members, so it carries survivorship bias.

## FRED API key

The fetcher works without a key by using FRED's public CSV download. To use the official API instead, add a repository secret named `FRED_API_KEY` (Settings → Secrets and variables → Actions). Never commit the key.

## Run locally

```
pip install -r fetch/requirements.txt
python fetch/fetch_data.py
cd site && python -m http.server 8000
```
