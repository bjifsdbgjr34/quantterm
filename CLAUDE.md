# QuantTerm — notes for Claude Code

The goal is a Bloomberg-class terminal for quant research and trading that runs locally. The UI must stay a
**visual terminal**: amber-on-black panels, a command line, charts and tables. Never a code-only or CLI-only experience.

## Run & test
- `python run.py --demo --no-browser` runs offline with synthetic data. Use `python run.py` for live Yahoo/FRED data.
- `python run.py --check` exercises every live data call and prints OK/FAIL. Run it first when Yahoo breaks something.
- `pytest -q` runs the suite, which must stay green. Add tests for every new analytic in `tests/`.
- Smoke-test the UI with Playwright. Load the page, set `localStorage['qt.panels']` to a JSON list of commands and
  `qt.layout` to the panel count, reload, then screenshot and check that no `.err` elements or console errors appear.

## Architecture
- `quantterm/data/providers.py` holds every external data call. Each call returns `Result(data, source)` through
  `_with_fallback(live, demo)`. New data sources need a deterministic demo implementation so the app works offline.
  The UI shows `source == "demo"` as a red DEMO DATA badge. Never present synthetic numbers as real.
- `quantterm/analytics/*` contains pure functions on pandas/numpy with no I/O. Follow gs-quant conventions:
  log returns for vol, vol in percent, 252-day annualisation.
- `quantterm/server.py` holds the FastAPI endpoints. Return plain dicts. `CleanRoute`/`clean()` serialises numpy,
  pandas and NaN, so you don't call `.tolist()` everywhere.
- `quantterm/static/app.js` is the core: panels, command parser, autocomplete, helpers, and the market/security functions.
  `quantterm/static/quant.js` holds the analytics, derivatives, Monte Carlo, portfolio, trading and macro functions.
  `quantterm/static/research.js` holds screener, heatmap, live/alerts, events, factors, walk-forward, multi-asset,
  skew/scanner, notes, workspaces, ASK and BRIEF.
- `app.js` infrastructure: `liveStream(panel, syms, cb)` (SSE quotes, closed on panel dispose), `panel.onDispose(fn)`,
  `toast()`, linked panels (`state.links`, `propagateLink`), export menu, workspaces. Use `lsGet`/`lsSet` for storage.
- User data (workspaces, notes, alerts) lives in `quantterm/store.py` (SQLite in `QT_HOME`).
- Brokers implement the `Broker` protocol in `trading/broker.py` (`paper.py`, `alpaca.py`); pick with `QT_BROKER`.
  Live-money trading needs `QT_ALLOW_LIVE=1`; the UI shows LIVE in red and confirms every order.
- `assistant.py` (ASK) calls Claude with read-only terminal tools; without credentials it falls back to a keyword
  router that says so. Never let the assistant place orders.
- Endpoints that are `async def` work with `CleanRoute`; keep long work in `run_in_threadpool`.
- Add a terminal function with `reg(CODE, group, description, async (panel, sym, args) => {...}, {sym: 1})`.
  `{sym:1}` makes the function require a ticker. Use `api()`, `plot()`, `table()`, `card()`, `kv()`, `fmt*()` and `uid()`.
  Call `panel.setSource(r.source)` after every fetch. Use `panel.every(sec, fn)` to auto-refresh.
- The backtest formula language in `analytics/backtest.py` is an AST whitelist evaluator. Never switch it to `eval`.
- Plotly is vendored in `static/vendor`, so there are no CDNs and the UI works offline.

## Conventions
- Positions are decided at the close of bar t and earn the return of bar t+1. Keep every backtest free of look-ahead.
- Show model risk and overfitting warnings in the UI wherever results can mislead (optimisation, historical drift).
- The paper broker (`trading/paper.py`) is the interface a real broker adapter should implement next.
