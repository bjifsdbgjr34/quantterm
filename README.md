# QuantTerm

A Bloomberg-style terminal for quantitative research and trading, built to run on your own machine.
It has an amber-on-black multi-panel screen with a command line, a live ticker tape and world market clocks.
Behind the screen it runs a quant engine inspired by Goldman Sachs' [`gs-quant`](https://github.com/goldmansachs/gs-quant).
Data comes free from Yahoo Finance and FRED, so you need no API keys and no subscription.

## Quick start

**macOS / Linux**: double-click `start.command`, or run `./start.command` in a terminal.
**Windows**: double-click `start.bat`.

The first launch sets up a private Python environment, which takes about a minute. After that QuantTerm
opens at <http://127.0.0.1:8765>. You need Python 3.10 or newer.

Manual setup:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py            # auto: live data, with a synthetic fallback when offline
python run.py --demo     # synthetic data only (offline development)
python run.py --live     # live data only; errors are shown instead of falling back
python run.py --check    # test every Yahoo / FRED call once and print OK / FAIL per feed
```

Optional extras, all off by default:

| Feature | Turn it on with |
|---|---|
| AI research assistant (`ASK`, or type `?question`) | `pip install anthropic`, then set `ANTHROPIC_API_KEY` (or run `ant auth login`). Model: `QT_AI_MODEL`, default `claude-opus-5-5` |
| Alpaca broker in the `TRADE` screen | `QT_BROKER=alpaca`, `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`. It uses the paper endpoint unless `ALPACA_PAPER=0` **and** `QT_ALLOW_LIVE=1` |

When live data can't be reached, every panel shows a red **DEMO DATA** badge, so synthetic numbers are never mistaken for real ones.

## Using it

Type into the command bar, Bloomberg-style: `TICKER FUNCTION` or `FUNCTION ARGS`. A bare ticker opens its description page.
Bloomberg-style tickers also work, for example `SPX INDEX`, `EURUSD CURNCY` and `SMI`. Swiss stocks use `.SW` (for example `NESN.SW`).

| Keys | Action |
|---|---|
| Enter / Shift+Enter | run in focused panel / in next panel |
| Tab, ↑ ↓ | accept suggestion, command history |
| Ctrl+1…6 | focus panel |
| `?why is SPY vol rising` | ask the AI assistant |
| F1 | help |
| layout buttons 1·2·3·4·6, ▢ | panel layout, maximise a panel |
| `·` chip on a panel title | link group A/B/C: linked panels follow each other's ticker |
| ⤓ on a panel title | export charts to PNG, tables to Excel, or the panel to PDF |
| WORKSPACE menu | save / reload named layouts ("Morning", "Options desk") |

### Functions

| Group | Code | What it does |
|---|---|---|
| Markets | `WEI` `FX` `CMDT` `CRYPTO` `RATES` `MAG7` `SWISS` `SECTORS` | Live monitors with day / week / YTD change and sparklines |
| | `W` | Watchlist with streaming prices (saved in the browser) |
| | `NVDA LIVE` | Intraday 1m–30m candles + VWAP, last price streamed every 5s |
| | `ALRT` | Alerts: price above/below, daily move %, realized-vol spike. Toast, sound and desktop notification |
| | `SRCH name` | Security search |
| Security | `AAPL DES` | Description, fundamentals, 1-year risk profile |
| | `AAPL GP 2Y` | Candles + volume + SMA/EMA/Bollinger/VWAP/RSI/MACD/Stoch/ATR/RealVol/Drawdown |
| | `HP`, `FA`, `N` | Historical prices (CSV export), financial statements, news |
| Analytics | `G A B C` | Rebased comparison + stats table |
| | `CORR …` | Correlation matrix + rolling correlations |
| | `NVDA BETA [bench]` | Regression: alpha, beta, R², t-stats, rolling beta |
| | `SPY VOL` | Realized vol (close-to-close, EWMA, Parkinson), vol cone, VaR table |
| Derivatives | `AAPL OMON` | Option chain with IV and greeks, put/call ratios, max pain, OI by strike, smile |
| | `SPY VOLS` | 3D implied-vol surface, smiles, ATM term structure |
| | `SPY SKEW` | 25Δ risk reversal / butterfly by expiry, term-structure state, implied vs realized vol |
| | `SPY OSCAN` | Strategy scanner: 12 structures ranked by expected value under your vol forecast |
| | `OVME AAPL` | Black-Scholes-Merton + American binomial pricer, full greeks (incl. vanna/volga/charm), 10 strategy payoffs |
| Monte Carlo | `MC SPY` | 5 models: GBM, Student-t, block bootstrap, Merton jump-diffusion, GARCH. Fan chart, touch probabilities, VaR/CVaR, drawdown distribution, side-by-side model comparison |
| | `MCO` | Monte Carlo exotic option pricer: Asian, barriers, lookback, digital. Antithetic variates, confidence intervals, convergence plot |
| | `MCP SPY TLT GLD` | Portfolio / wealth planner: correlated bootstrap, contributions or withdrawals, goal and ruin probability |
| Quant Lab | `BT SPY` | Backtester: 8 built-in strategies or your own formula, costs, shorting, vol targeting, monthly heatmap, trade list, parameter-grid optimisation |
| | `SPY GARCH` | GARCH(1,1) by MLE, conditional vol, vol forecast term structure |
| | `SPY STAT` | Distribution fit (normal vs t), Q-Q, ACF, Jarque-Bera, Ljung-Box, ADF, Hurst, variance ratio, Kelly curve |
| | `PAIR KO PEP` | Engle-Granger cointegration, hedge ratio, half-life, z-score strategy backtest |
| | `PCA …` | Principal components, loadings, factor indices, absorption ratio |
| | `WF SPY` | Walk-forward backtest: refit on a rolling past window, trade the next one, compare with the hindsight-optimised curve |
| | `MBT SPY EFA EEM TLT GLD` | Multi-asset strategies: cross-sectional momentum, dual-momentum rotation, long/short, low-vol, inverse-vol |
| Portfolio & Trading | `PORT …` | Max Sharpe / min variance / risk parity / max diversification / inverse vol / equal weight. Ledoit-Wolf covariance, efficient frontier, risk contributions, VaR, stress tests, share allocation |
| | `TRADE AAPL` | Paper trading: market/limit/stop orders, long & short, P&L, blotter |
| Macro | `GC` | US Treasury curve now vs 1W/1M/1Y ago, 2s10s / 3m10y / 5s30s |
| | `ECO [FRED ids]` | Macro dashboard: Fed funds, CPI, core CPI, unemployment, payrolls, curve, HY spreads, mortgages… |
| Research | `BRIEF` | Morning briefing: biggest moves, watchlist, next 7 days of events, portfolio VaR, triggered alerts |
| | `EQS [filter]` | Equity screener over ~100 S&P large caps: `pe < 20 and mom_12_1 > 10 and sector == "Energy"`, presets |
| | `MAP` | Treemap heatmap by sector (1D / 1M / YTD / momentum), breadth |
| | `EVT` | Calendar: watchlist earnings + FOMC, CPI, payrolls, PCE, GDP |
| | `AAPL ERN` | Earnings: options-implied move vs past reactions, EPS surprises |
| | `NVDA FF` | Factor exposure (market, size, value, momentum, quality, low vol) with attribution; portfolios too |
| | `NOTE` | Research notes, per ticker, auto-saved |
| | `ASK question` | AI assistant that pulls terminal data, answers, and opens the right panels |

### Backtest formula language

The signal value is your position. `true` means 1 (long), `false` means 0, and numbers mean size. Each signal is
evaluated at the close and traded on the next bar, so it can't look ahead.

```
sma(close,50) > sma(close,200)
hold(rsi(close,14) < 30, rsi(close,14) > 55)          # enter / exit state machine
sign(ret(close,126)) * (vol(close,20) < 30)           # trend with a volatility filter
where(zscore(close,20) < -2, 1, where(zscore(close,20) > 2, -1, 0))
```

Functions: `sma ema rsi std ret zscore hhv llv lag vol macd macd_signal bb_upper bb_lower abs sign clip where hold`.
Only these names are allowed. Formulas run in a sandbox, not through Python `eval`.

## Architecture

```
quantterm/
  data/providers.py      Yahoo + FRED + deterministic demo data, TTL cache
  analytics/
    timeseries.py        returns, vol, drawdown, technicals (gs-quant conventions)
    risk.py              performance stats, VaR/CVaR (hist, parametric, Cornish-Fisher), stress
    options.py           BSM, greeks, implied vol, CRR binomial, payoff profiles
    portfolio.py         Ledoit-Wolf, optimisers, frontier, risk contributions
    backtest.py          vectorised engine + safe expression language
    montecarlo.py        path simulation (5 models), MC option pricing, portfolio MC
    quant.py             GARCH, ADF, Hurst, half-life, pairs/cointegration, PCA, Kelly
  trading/paper.py       SQLite paper broker
  server.py              FastAPI JSON API
  static/                terminal UI (vanilla JS + Plotly, works offline)
tests/                   pytest suite
```

Settings are environment variables: `QT_DATA`, `QT_PORT`, `QT_RISK_FREE`, `QT_PAPER_CASH`, `QT_SLIPPAGE_BPS` and `QT_HOME`.
User data (the paper-trading database) lives in `~/.quantterm/`.

Run the tests with `pip install pytest && pytest -q`.

## Roadmap

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the researched list of next features.

*Not investment advice. Free data sources can be delayed, incomplete or wrong. Check anything you trade on.*
