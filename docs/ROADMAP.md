# QuantTerm roadmap

## Done in this round

| # | Item | Where |
|---|---|---|
| 1 | Real-data check against live Yahoo + FRED; fixed what broke | `python run.py --check`, `providers.py` |
| 2 | Streaming quotes (SSE), intraday charts, price/move/vol alerts with notifications | `LIVE`, `W`, `ALRT` |
| 3 | Broker interface + Alpaca adapter (paper by default, live behind two switches) | `trading/broker.py`, `trading/alpaca.py` |
| 4 | Equity screener with a safe filter language and presets | `EQS` |
| 5 | Walk-forward backtests showing in-sample vs out-of-sample | `WF` |
| 6 | Multi-asset strategies: cross-sectional momentum, rotation, long/short | `MBT` |
| 7 | Factor model (ETF-proxy Fama-French + momentum/quality/low vol) | `FF` |
| 8 | Event calendar + earnings implied move vs history | `EVT`, `ERN` |
| 9 | Skew / term-structure signals + option strategy scanner | `SKEW`, `OSCAN` |
| 10 | Saved workspaces | header menu, `WS` |
| 11 | Linked panels (groups A/B/C) | chip on each panel title |
| 12 | Notes panel; export to PNG / Excel / PDF | `NOTE`, ⤓ on each panel |
| 13 | Sector treemap heatmap | `MAP` |
| 14 | AI research assistant (Claude + terminal tools) | `ASK`, `?question` |
| 15 | Morning briefing | `BRIEF` (default first panel) |

Bugs the live check found, all fixed:
- FRED's reachability probe always timed out. FRED stalls requests that send a fake browser User-Agent, so in the
  default `auto` mode the yield curve and macro panels silently fell back to DEMO data while FRED was up.
- Yahoo's `info` sometimes returns a near-empty payload on the first call while its cookie/crumb handshake is still
  pending. It now retries, then falls back to `fast_info`.
- News comes from a separate Yahoo host (`nexus-finance-prod.media.yahoo.com`). Firewalled networks must allow it.

## Next: researched candidates

Ranked by value for a daily quant workflow against effort. Everything listed has a free data source.

### High value, moderate effort
1. **Fed path / `WIRP`**: market-implied rate path. Bloomberg's WIRP shows probabilities of rate moves at each
   meeting, derived from futures. Free approximation: Yahoo has 30-day Fed funds futures (`ZQ=F` front month, plus
   dated contracts) and FRED has SOFR. Price each FOMC date's implied rate and show hike/cut probabilities next to the
   `FOMC_DATES` calendar we already have. (CME FedWatch has no free API.)
2. **Seasonality / `SEAG`**: average path by calendar month and day-of-year over N years, current year overlaid, plus
   hit rates by month. Pure analytics on existing history; also useful for commodity spreads.
3. **Dealer gamma exposure (GEX)**: sum of gamma × open interest × 100 × spot² across the chain, signed calls +/puts −.
   Shows the "gamma flip" level and where hedging flows dampen or amplify moves. Uses the option chains we already pull.
4. **Regime detection**: two- or three-state Gaussian HMM on returns and vol (or a simple vol/trend regime
   classifier). Colour the GP chart by regime, and let backtests condition on it (`regime() == 1` in the formula
   language).
5. **SEC EDGAR filings and insider trades**: EDGAR's JSON API is free and needs no key (send a descriptive
   User-Agent, about 10 requests/s max). Add 10-K/10-Q/8-K links on `DES`, plus a Form 4 insider buy/sell table.
6. **CFTC Commitments of Traders (`COT`)**: free Socrata API at publicreporting.cftc.gov. Net speculator positioning
   for rates, FX, equity index, energy, metals and ags futures, as z-scores. A classic contrarian / positioning
   signal.

### Research quality
7. **Backtest robustness pack**: deflated Sharpe ratio, probability of backtest overfitting (CSCV), bootstrap
   confidence bands on Sharpe, and Monte Carlo trade reshuffling. These complement `WF` with honest statistics.
8. **Transaction-cost model**: spread plus square-root market-impact cost as a function of ADV and volatility,
   instead of flat bps. Matters once position sizes are real.
9. **Black-Litterman and CVaR optimisation** in `PORT`: blend views with equilibrium returns; minimise tail loss
   instead of variance.
10. **Kalman-filter hedge ratio** for `PAIR`: a time-varying beta instead of a static OLS one. Usually the biggest
    single improvement to a pairs backtest.
11. **Notebook-style research panel**: run a pinned Python snippet against the analytics API and render its
    table/chart in a panel. Sandbox it (subprocess with a time limit, no network) and never `eval` in the server.

### Bloomberg feel
12. **Relative-value scatter (`RV`)**: plot peers on any two metrics (e.g. forward P/E vs revenue growth) with a
    regression line. Reuses the screener frame.
13. **Analyst estimates (`ANR` / `EE`)**: recommendation trend, target-price distribution, EPS revisions. Available
    in yfinance (`recommendations`, `analyst_price_targets`, `eps_revisions`).
14. **Holders (`HDS`)**: institutional and fund holders, also in yfinance.
15. **Dividend history and forecast (`DVD`)**: dividend history, growth, payout ratio and ex-dates.
16. **Keyboard-first navigation**: Bloomberg-style `<MENU>` back stack per panel, and `PgUp`/`PgDn` through tables.

### Infrastructure
17. **Interactive Brokers adapter** via `ib_async`, behind the same `Broker` protocol. IB brings options and
    futures, which Alpaca's equity/crypto API does not cover.
18. **Pluggable data providers**: Polygon, Tiingo or Alpha Vantage behind the `Result(data, source)` contract when
    the user has a key. Better intraday data and no scraping.
19. **Scheduled jobs**: run alerts and a daily BRIEF export server-side even when the browser is closed. Email or
    push when an alert fires.
20. **Packaging**: single-file installer (PyInstaller) or a Docker image, so non-technical users skip the Python
    setup.

## Sources
- OpenBB: TechCrunch on the move from CLI to a web workspace: https://techcrunch.com/2024/10/07/fintech-openbb-aims-to-be-more-than-an-open-source-bloomberg-terminal/
  and the OpenBB Workspace product page: https://openbb.co/products/workspace/
- Bloomberg function lists (WIRP, SEAG, EQS, BETA): https://corporatefinanceinstitute.com/resources/data/bloomberg-functions-shortcuts-list
  and https://guides.nyu.edu/bloombergguide/popular-commands
- SEAG spread-builder use by traders: https://futuresflux.substack.com/p/bloomberg-for-energy-commodities
- SEC EDGAR free JSON API guide: https://dev.to/odeeb/the-sec-edgar-api-a-practical-guide-to-free-filing-data-in-python-15b
- CFTC COT public Socrata API: https://github.com/api-evangelist/commodity-futures-trading-commission
  and https://uufinance.github.io/data/other-sources/commodities-futures/
