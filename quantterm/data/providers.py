"""Market data layer.

Sources
-------
* Yahoo Finance (via ``yfinance``): prices, quotes, fundamentals, option chains, news.
* FRED (public CSV endpoint, no API key): rates & macro series.
* Demo: deterministic synthetic data so the terminal is usable offline. Every
  response carries ``source`` so the UI can flag synthetic data loudly.

All public functions return plain pandas / python objects and are cached with a TTL.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger("quantterm.data")

# --------------------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------------------
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cached(ttl: float) -> Callable:
    def deco(fn):
        def wrapper(*args, **kwargs):
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            now = time.time()
            with _cache_lock:
                hit = _cache.get(key)
                if hit and now - hit[0] < ttl:
                    return hit[1]
            val = fn(*args, **kwargs)
            with _cache_lock:
                _cache[key] = (now, val)
            return val

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


class DataError(Exception):
    pass


@dataclass
class Result:
    data: Any
    source: str  # 'yahoo' | 'fred' | 'demo'


# FRED's homepage can stall for non-browser clients; the CSV endpoint the app actually uses answers fast.
PROBES = {"yahoo": "https://query1.finance.yahoo.com/",
          "fred": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2026-01-01"}
_online_cache: dict[str, tuple[float, bool]] = {}


def online(name: str) -> bool:
    """Is a live source reachable? (any HTTP answer counts; cached for 2 minutes)."""
    hit = _online_cache.get(name)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    import httpx

    try:
        # FRED stalls requests with a fake browser User-Agent; Yahoo wants one
        ua = "quantterm/1.0" if name == "fred" else "Mozilla/5.0"
        httpx.get(PROBES[name], timeout=10, follow_redirects=True, headers={"User-Agent": ua})
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    _online_cache[name] = (time.time(), ok)
    if not ok:
        log.warning("%s unreachable - serving synthetic demo data", name)
    return ok


def _with_fallback(name: str, live: Callable[[], Any], demo: Callable[[], Any], retries: int = 1) -> Result:
    if config.DATA_MODE == "demo" or (config.DATA_MODE == "auto" and not online(name)):
        return Result(demo(), "demo")
    for attempt in range(retries + 1):
        try:
            data = live()
            break
        except Exception as exc:  # noqa: BLE001
            # Yahoo intermittently answers 401 "Invalid Crumb" or an empty payload under concurrent load;
            # one short retry fixes almost all of these. Genuine "no data" errors simply fail twice.
            if attempt < retries:
                log.info("%s call failed (%s), retrying", name, exc)
                time.sleep(0.8 * (attempt + 1))
                continue
            if isinstance(exc, DataError):
                raise
            raise DataError(f"{name}: {exc}") from exc
    if data is None or (hasattr(data, "empty") and data.empty):
        raise DataError(f"{name}: empty response")
    return Result(data, name)


# --------------------------------------------------------------------------------------
# symbol normalisation (accepts a few Bloomberg-ish spellings)
# --------------------------------------------------------------------------------------
ALIASES = {
    "SPX": "^GSPC", "SPX INDEX": "^GSPC", "NDX": "^NDX", "INDU": "^DJI", "DJI": "^DJI", "RTY": "^RUT",
    "SX5E": "^STOXX50E", "DAX": "^GDAXI", "UKX": "^FTSE", "SMI": "^SSMI", "CAC": "^FCHI", "NKY": "^N225",
    "HSI": "^HSI", "VIX": "^VIX", "TNX": "^TNX", "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "GOLD": "GC=F", "WTI": "CL=F", "BRENT": "BZ=F", "SILVER": "SI=F", "NATGAS": "NG=F", "COPPER": "HG=F",
    "EURUSD": "EURUSD=X", "USDCHF": "USDCHF=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "EURCHF": "EURCHF=X", "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X", "DXY": "DX-Y.NYB",
}


def norm(symbol: str) -> str:
    s = symbol.strip().upper()
    for suffix in (" US EQUITY", " EQUITY", " INDEX", " CURNCY", " COMDTY", " US"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return ALIASES.get(s, s)


PERIOD_DAYS = {"1d": 1, "5d": 5, "1mo": 31, "3mo": 92, "6mo": 183, "ytd": None, "1y": 365, "2y": 730,
               "5y": 1826, "10y": 3652, "max": 9000}


def period_start(period: str) -> date:
    today = date.today()
    if period == "ytd":
        return date(today.year, 1, 1)
    return today - timedelta(days=PERIOD_DAYS.get(period, 365))


# --------------------------------------------------------------------------------------
# DEMO data generator
# --------------------------------------------------------------------------------------
DEMO_META = {
    "AAPL": ("Apple Inc.", "Technology", "Consumer Electronics", 230, 0.27),
    "MSFT": ("Microsoft Corporation", "Technology", "Software - Infrastructure", 430, 0.24),
    "NVDA": ("NVIDIA Corporation", "Technology", "Semiconductors", 140, 0.50),
    "AMZN": ("Amazon.com, Inc.", "Consumer Cyclical", "Internet Retail", 200, 0.32),
    "GOOGL": ("Alphabet Inc.", "Communication Services", "Internet Content", 175, 0.30),
    "META": ("Meta Platforms, Inc.", "Communication Services", "Internet Content", 580, 0.38),
    "TSLA": ("Tesla, Inc.", "Consumer Cyclical", "Auto Manufacturers", 250, 0.60),
    "JPM": ("JPMorgan Chase & Co.", "Financial Services", "Banks - Diversified", 230, 0.22),
    "GS": ("The Goldman Sachs Group, Inc.", "Financial Services", "Capital Markets", 560, 0.26),
    "XOM": ("Exxon Mobil Corporation", "Energy", "Oil & Gas Integrated", 115, 0.24),
    "NESN.SW": ("Nestle S.A.", "Consumer Defensive", "Packaged Foods", 80, 0.16),
    "NOVN.SW": ("Novartis AG", "Healthcare", "Drug Manufacturers", 95, 0.18),
    "UBSG.SW": ("UBS Group AG", "Financial Services", "Banks - Diversified", 27, 0.30),
    "SPY": ("SPDR S&P 500 ETF Trust", "ETF", "Large Blend", 590, 0.16),
    "QQQ": ("Invesco QQQ Trust", "ETF", "Large Growth", 510, 0.21),
    "TLT": ("iShares 20+ Year Treasury Bond ETF", "ETF", "Long Government", 92, 0.15),
    "GLD": ("SPDR Gold Shares", "ETF", "Commodities", 245, 0.14),
    "^GSPC": ("S&P 500", "Index", "Equity Index", 5900, 0.16),
    "^NDX": ("NASDAQ 100", "Index", "Equity Index", 20800, 0.21),
    "^DJI": ("Dow Jones Industrial Average", "Index", "Equity Index", 43500, 0.15),
    "^RUT": ("Russell 2000", "Index", "Equity Index", 2350, 0.22),
    "^STOXX50E": ("EURO STOXX 50", "Index", "Equity Index", 4800, 0.17),
    "^GDAXI": ("DAX", "Index", "Equity Index", 19300, 0.17),
    "^FTSE": ("FTSE 100", "Index", "Equity Index", 8200, 0.13),
    "^SSMI": ("Swiss Market Index", "Index", "Equity Index", 11800, 0.13),
    "^FCHI": ("CAC 40", "Index", "Equity Index", 7300, 0.17),
    "^N225": ("Nikkei 225", "Index", "Equity Index", 38500, 0.22),
    "^HSI": ("Hang Seng", "Index", "Equity Index", 19500, 0.25),
    "^VIX": ("CBOE Volatility Index", "Index", "Volatility", 16, 0.90),
    "^TNX": ("US 10Y Treasury Yield", "Rates", "Yield", 4.3, 0.25),
    "^IRX": ("US 13W T-Bill Yield", "Rates", "Yield", 4.4, 0.10),
    "^FVX": ("US 5Y Treasury Yield", "Rates", "Yield", 4.1, 0.28),
    "^TYX": ("US 30Y Treasury Yield", "Rates", "Yield", 4.5, 0.20),
    "EURUSD=X": ("EUR/USD", "FX", "Currency", 1.05, 0.07),
    "USDCHF=X": ("USD/CHF", "FX", "Currency", 0.88, 0.07),
    "EURCHF=X": ("EUR/CHF", "FX", "Currency", 0.93, 0.05),
    "GBPUSD=X": ("GBP/USD", "FX", "Currency", 1.26, 0.07),
    "USDJPY=X": ("USD/JPY", "FX", "Currency", 154, 0.10),
    "AUDUSD=X": ("AUD/USD", "FX", "Currency", 0.65, 0.09),
    "USDCAD=X": ("USD/CAD", "FX", "Currency", 1.40, 0.06),
    "DX-Y.NYB": ("US Dollar Index", "FX", "Currency", 106, 0.07),
    "GC=F": ("Gold Futures", "Commodity", "Metals", 2650, 0.15),
    "SI=F": ("Silver Futures", "Commodity", "Metals", 31, 0.28),
    "HG=F": ("Copper Futures", "Commodity", "Metals", 4.1, 0.22),
    "CL=F": ("WTI Crude Oil Futures", "Commodity", "Energy", 70, 0.33),
    "BZ=F": ("Brent Crude Oil Futures", "Commodity", "Energy", 74, 0.30),
    "NG=F": ("Natural Gas Futures", "Commodity", "Energy", 3.2, 0.60),
    "ZC=F": ("Corn Futures", "Commodity", "Agriculture", 430, 0.22),
    "BTC-USD": ("Bitcoin USD", "Crypto", "Crypto", 95000, 0.55),
    "ETH-USD": ("Ethereum USD", "Crypto", "Crypto", 3400, 0.70),
    "SOL-USD": ("Solana USD", "Crypto", "Crypto", 240, 0.90),
}


def _seed(symbol: str) -> int:
    return int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)


def _demo_meta(symbol: str):
    if symbol in DEMO_META:
        return DEMO_META[symbol]
    rng = np.random.default_rng(_seed(symbol))
    return (f"{symbol} (demo)", "Unknown", "Unknown", float(rng.uniform(20, 400)), float(rng.uniform(0.18, 0.45)))


_MARKET_FACTOR_CACHE: dict[int, np.ndarray] = {}


def _demo_history(symbol: str, start: date, end: date, interval: str = "1d") -> pd.DataFrame:
    """Deterministic GBM with a shared market factor so correlations look realistic."""
    _, sector, _, last_px, vol = _demo_meta(symbol)
    epoch = pd.bdate_range("2000-01-03", date.today())
    n = len(epoch)
    if n not in _MARKET_FACTOR_CACHE:
        _MARKET_FACTOR_CACHE[n] = np.random.default_rng(42).standard_normal(n)
    mkt = _MARKET_FACTOR_CACHE[n]
    rng = np.random.default_rng(_seed(symbol))
    idio = rng.standard_normal(n)
    beta_corr = {"Index": 0.95, "ETF": 0.85, "FX": 0.1, "Commodity": 0.2, "Crypto": 0.35, "Rates": -0.2}.get(sector, 0.6)
    if symbol in ("^VIX",):
        beta_corr = -0.75
    if symbol in ("TLT", "GLD"):
        beta_corr = -0.15
    z = beta_corr * mkt + math.sqrt(1 - beta_corr**2) * idio
    # fat tails + volatility clustering (cheap GARCH-ish multiplier)
    regime = np.exp(0.35 * pd.Series(rng.standard_normal(n)).rolling(20, min_periods=1).mean().to_numpy() * 3)
    dvol = vol / math.sqrt(252) * regime / regime.mean()
    drift = 0.07 / 252 if sector not in ("FX", "Rates") else 0.0
    if symbol == "^VIX":  # mean reverting
        lvl = np.empty(n)
        lvl[0] = 18
        for i in range(1, n):
            lvl[i] = max(9, lvl[i - 1] + 0.05 * (18 - lvl[i - 1]) + 1.2 * z[i])
        close = lvl * (last_px / lvl[-1])
    else:
        logret = drift - 0.5 * dvol**2 + dvol * z
        path = np.exp(np.cumsum(logret))
        close = path * (last_px / path[-1])
    rng2 = np.random.default_rng(_seed(symbol) + 1)
    intrarange = np.abs(rng2.standard_normal(n)) * dvol * close
    open_ = np.r_[close[0], close[:-1]] * (1 + rng2.standard_normal(n) * dvol * 0.3)
    high = np.maximum(open_, close) + intrarange * 0.6
    low = np.minimum(open_, close) - intrarange * 0.6
    base_vol = 1e6 * (last_px ** -0.3) * 300 if sector not in ("FX", "Rates", "Index") else 0
    volume = (base_vol * np.exp(rng2.standard_normal(n) * 0.35) * (1 + 3 * np.abs(z) / 3)).round()
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=epoch)
    df = df.loc[pd.Timestamp(start): pd.Timestamp(end)]
    if interval in ("1wk", "1mo"):
        rule = "W-FRI" if interval == "1wk" else "ME"
        df = df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    df.index.name = "Date"
    return df


# --------------------------------------------------------------------------------------
# Yahoo
# --------------------------------------------------------------------------------------
# yfinance shares one cookie/crumb session. A 401/404 on one call makes it refresh the crumb, and calls in
# flight at that moment come back empty ("Available expirations are: []"). Serialise the crumb-sensitive
# endpoints (options, earnings) so they never race with each other.
_YF_LOCK = threading.RLock()

NON_EQUITY_SECTORS = ("ETF", "Index", "FX", "Commodity", "Crypto", "Rates")


def is_single_stock(sym: str) -> bool:
    """Cheap check (no network) used to avoid asking Yahoo for earnings/fundamentals it doesn't have."""
    s = norm(sym)
    if s.startswith("^") or "=" in s or s.endswith("-USD") or s in MONITORS.get("SECTORS", []):
        return False
    return (DEMO_META.get(s, (None, "Equity"))[1] not in NON_EQUITY_SECTORS) and s not in ETF_HINTS


ETF_HINTS = {"SPY", "QQQ", "IWM", "DIA", "TLT", "IEF", "SHY", "GLD", "SLV", "USO", "UNG", "DBC", "VNQ", "EFA", "EEM",
             "VTI", "VOO", "VEA", "VWO", "AGG", "BND", "LQD", "HYG", "IWD", "IWF", "MTUM", "QUAL", "USMV", "XLK", "XLF",
             "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "ARKK", "GDX", "TQQQ", "SQQQ"}


def _yf():
    import yfinance as yf  # imported lazily: slow import, optional in demo mode

    return yf


def _clean_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "Date"
    return df.dropna(subset=["Close"])


@cached(ttl=300)
def history(symbol: str, period: str = "1y", interval: str = "1d") -> Result:
    sym = norm(symbol)
    start = period_start(period)

    def live():
        df = _yf().Ticker(sym).history(period=period, interval=interval, auto_adjust=True)
        df = _clean_hist(df)
        if df.empty:
            raise DataError(f"No price data for {sym}")
        return df

    return _with_fallback("yahoo", live, lambda: _demo_history(sym, start, date.today(), interval))


@cached(ttl=300)
def closes(symbols: tuple[str, ...], period: str = "1y") -> Result:
    """Aligned close-price frame for many symbols (one network round-trip)."""
    syms = [norm(s) for s in symbols]
    start = period_start(period)

    def live():
        raw = _yf().download(syms, period=period, interval="1d", auto_adjust=True, progress=False,
                             group_by="ticker", threads=True)
        out = {}
        for s in syms:
            try:
                sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                out[s] = sub["Close"]
            except KeyError:
                continue
        df = pd.DataFrame(out)
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        df = df.dropna(how="all")
        if df.empty:
            raise DataError("No data returned")
        return df

    def demo():
        return pd.DataFrame({s: _demo_history(s, start, date.today())["Close"] for s in syms})

    return _with_fallback("yahoo", live, demo)


@cached(ttl=20)
def quotes(symbols: tuple[str, ...]) -> Result:
    """Last price / change for a list of symbols."""
    syms = [norm(s) for s in symbols]

    def build(frame: pd.DataFrame, vols: pd.DataFrame | None) -> list[dict]:
        rows = []
        for s in syms:
            if s not in frame:
                rows.append({"symbol": s, "name": _demo_meta(s)[0] if s in DEMO_META else s, "last": None})
                continue
            ser = frame[s].dropna()
            if len(ser) < 2:
                continue
            last, prev = float(ser.iloc[-1]), float(ser.iloc[-2])
            first_ytd = ser[ser.index >= pd.Timestamp(date.today().year, 1, 1)]
            ytd = (last / float(first_ytd.iloc[0]) - 1) * 100 if len(first_ytd) else None
            w = ser.iloc[-6] if len(ser) > 5 else ser.iloc[0]
            rows.append({
                "symbol": s,
                "name": DEMO_META.get(s, (s,))[0],
                "last": last,
                "chg": last - prev,
                "pct": (last / prev - 1) * 100,
                "wtd": (last / float(w) - 1) * 100,
                "ytd": ytd,
                "volume": float(vols[s].iloc[-1]) if vols is not None and s in vols and not np.isnan(vols[s].iloc[-1]) else None,
                "time": ser.index[-1].strftime("%Y-%m-%d"),
                "spark": [round(float(x), 6) for x in ser.iloc[-30:].to_numpy()],
            })
        return rows

    def live():
        raw = _yf().download(syms, period="1y", interval="1d", auto_adjust=True, progress=False,
                             group_by="ticker", threads=True)
        if raw is None or raw.empty:
            raise DataError("no quotes")
        if isinstance(raw.columns, pd.MultiIndex):
            c = pd.DataFrame({s: raw[s]["Close"] for s in syms if s in raw.columns.get_level_values(0)})
            v = pd.DataFrame({s: raw[s]["Volume"] for s in syms if s in raw.columns.get_level_values(0)})
        else:
            c = pd.DataFrame({syms[0]: raw["Close"]})
            v = pd.DataFrame({syms[0]: raw["Volume"]})
        return build(c, v)

    def demo():
        start = period_start("1y")
        hs = {s: _demo_history(s, start, date.today()) for s in syms}
        return build(pd.DataFrame({s: h["Close"] for s, h in hs.items()}),
                     pd.DataFrame({s: h["Volume"] for s, h in hs.items()}))

    return _with_fallback("yahoo", live, demo)


INFO_FIELDS = [
    "longName", "shortName", "quoteType", "exchange", "currency", "sector", "industry", "country", "website",
    "fullTimeEmployees", "longBusinessSummary", "marketCap", "enterpriseValue", "trailingPE", "forwardPE",
    "pegRatio", "priceToBook", "priceToSalesTrailing12Months", "enterpriseToEbitda", "trailingEps", "forwardEps",
    "dividendYield", "payoutRatio", "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage",
    "twoHundredDayAverage", "averageVolume", "sharesOutstanding", "floatShares", "shortPercentOfFloat",
    "profitMargins", "operatingMargins", "grossMargins", "returnOnEquity", "returnOnAssets", "revenueGrowth",
    "earningsGrowth", "totalRevenue", "ebitda", "totalCash", "totalDebt", "debtToEquity", "currentRatio",
    "freeCashflow", "recommendationKey", "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "heldPercentInstitutions", "heldPercentInsiders",
]


@cached(ttl=3600)
def info(symbol: str) -> Result:
    sym = norm(symbol)

    def live():
        # Yahoo's quoteSummary intermittently returns a near-empty payload when its cookie/crumb
        # handshake fails; retry once, then fall back to the lighter fast_info endpoint.
        t = _yf().Ticker(sym)
        raw: dict = {}
        out: dict = {}
        for attempt in range(3):
            try:
                raw = t.info or {}
            except Exception as e:  # noqa: BLE001
                log.warning("info(%s) attempt %d failed: %s", sym, attempt + 1, e)
                raw = {}
            cand = {k: raw.get(k) for k in INFO_FIELDS if raw.get(k) is not None}
            if len(cand) > len(out):
                out = cand
            if len(out) >= 25 or raw.get("quoteType") not in (None, "EQUITY"):  # ETFs/indices legitimately have fewer
                break
            time.sleep(0.6)
        if len(out) < 5:
            try:
                fi = t.fast_info
                for key, field in (("marketCap", "market_cap"), ("currency", "currency"), ("exchange", "exchange"),
                                   ("quoteType", "quote_type"), ("fiftyTwoWeekHigh", "year_high"),
                                   ("fiftyTwoWeekLow", "year_low"), ("fiftyDayAverage", "fifty_day_average"),
                                   ("twoHundredDayAverage", "two_hundred_day_average"), ("sharesOutstanding", "shares")):
                    v = fi.get(field)
                    if v is not None:
                        out.setdefault(key, v)
                out["_partial"] = True  # UI can say fundamentals were unavailable
            except Exception:  # noqa: BLE001
                pass
        if not out:
            raise DataError(f"No reference data for {sym}")
        return out | {"symbol": sym}

    def demo():
        name, sector, industry, px, vol = _demo_meta(sym)
        rng = np.random.default_rng(_seed(sym) + 7)
        shares = float(rng.uniform(0.5e9, 16e9))
        return {
            "symbol": sym, "longName": name, "sector": sector, "industry": industry, "currency": "USD",
            "exchange": "DEMO", "quoteType": "EQUITY" if sector not in ("Index", "FX", "Commodity", "Crypto", "Rates") else sector.upper(),
            "longBusinessSummary": "Synthetic demo data. Connect to the internet (QT_DATA=auto or live) for real reference data.",
            "marketCap": px * shares, "sharesOutstanding": shares, "trailingPE": float(rng.uniform(10, 45)),
            "forwardPE": float(rng.uniform(9, 35)), "priceToBook": float(rng.uniform(1, 15)), "beta": float(rng.uniform(0.6, 1.8)),
            "dividendYield": float(rng.uniform(0, 3.5)), "profitMargins": float(rng.uniform(0.05, 0.35)),
            "returnOnEquity": float(rng.uniform(0.05, 0.5)), "revenueGrowth": float(rng.uniform(-0.05, 0.3)),
            "totalRevenue": px * shares * float(rng.uniform(0.1, 0.5)), "recommendationKey": "hold",
            "targetMeanPrice": px * float(rng.uniform(0.9, 1.25)), "numberOfAnalystOpinions": int(rng.integers(5, 50)),
        }

    return _with_fallback("yahoo", live, demo)


KEY_ROWS = ["Total Revenue", "Gross Profit", "Operating Income", "EBITDA", "Net Income", "Diluted EPS",
            "Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity", "Cash And Cash Equivalents",
            "Total Debt", "Net Debt", "Operating Cash Flow", "Capital Expenditure", "Free Cash Flow",
            "Repurchase Of Capital Stock", "Cash Dividends Paid"]


def _df_to_records(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"columns": [], "rows": []}
    df = df.copy()
    # headline lines first (yfinance >= 1.x returns rows in an arbitrary order), then the rest as delivered
    first = [r for r in KEY_ROWS if r in df.index]
    df = df.loc[first + [r for r in df.index if r not in first]]
    df.columns = [c.strftime("%Y-%m-%d") if hasattr(c, "strftime") else str(c) for c in df.columns]
    rows = []
    for idx, r in df.iterrows():
        rows.append({"item": str(idx), "values": [None if pd.isna(v) else float(v) for v in r.to_numpy()]})
    return {"columns": list(df.columns), "rows": rows}


@cached(ttl=6 * 3600)
def financials(symbol: str, quarterly: bool = False) -> Result:
    sym = norm(symbol)

    def live():
        t = _yf().Ticker(sym)
        if quarterly:
            out = {"income": t.quarterly_income_stmt, "balance": t.quarterly_balance_sheet, "cashflow": t.quarterly_cashflow}
        else:
            out = {"income": t.income_stmt, "balance": t.balance_sheet, "cashflow": t.cashflow}
        if all(v is None or v.empty for v in out.values()):
            raise DataError(f"No financial statements for {sym}")
        return {k: _df_to_records(v) for k, v in out.items()}

    def demo():
        rng = np.random.default_rng(_seed(sym) + 3)
        cols = [date(date.today().year - i, 12, 31) for i in range(1, 5)]
        rev = float(rng.uniform(5e9, 300e9))
        g = [1, 0.92, 0.85, 0.80]
        inc = pd.DataFrame({c: {"Total Revenue": rev * x, "Gross Profit": rev * x * 0.45, "Operating Income": rev * x * 0.25,
                                "Net Income": rev * x * 0.18, "EBITDA": rev * x * 0.3, "Diluted EPS": 5 * x} for c, x in zip(cols, g)})
        bal = pd.DataFrame({c: {"Total Assets": rev * 1.6 * x, "Total Liabilities Net Minority Interest": rev * x,
                                "Stockholders Equity": rev * 0.6 * x, "Cash And Cash Equivalents": rev * 0.2 * x,
                                "Total Debt": rev * 0.4 * x} for c, x in zip(cols, g)})
        cf = pd.DataFrame({c: {"Operating Cash Flow": rev * 0.28 * x, "Capital Expenditure": -rev * 0.06 * x,
                               "Free Cash Flow": rev * 0.22 * x, "Repurchase Of Capital Stock": -rev * 0.08 * x} for c, x in zip(cols, g)})
        return {"income": _df_to_records(inc), "balance": _df_to_records(bal), "cashflow": _df_to_records(cf)}

    return _with_fallback("yahoo", live, demo)


@cached(ttl=900)
def news(symbol: str) -> Result:
    sym = norm(symbol)

    def live():
        items = _yf().Ticker(sym).news or []
        out = []
        for it in items:
            c = it.get("content", it)
            url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link")
            prov = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher")
            ts = c.get("pubDate") or c.get("providerPublishTime")
            if isinstance(ts, (int, float)):
                ts = datetime.utcfromtimestamp(ts).isoformat() + "Z"
            out.append({"title": c.get("title"), "publisher": prov, "url": url, "time": ts, "summary": c.get("summary")})
        if not out:
            raise DataError("no news")
        return out

    def demo():
        return [{"title": f"[DEMO] No live news feed available for {sym}", "publisher": "QuantTerm", "url": None,
                 "time": datetime.utcnow().isoformat() + "Z", "summary": "Run the terminal with internet access to see Yahoo Finance headlines."}]

    return _with_fallback("yahoo", live, demo)


@cached(ttl=120)
def option_expiries(symbol: str) -> Result:
    sym = norm(symbol)

    def live():
        with _YF_LOCK:
            exps = list(_yf().Ticker(sym).options)
        if not exps:
            raise DataError(f"No listed options for {sym}")
        return exps

    def demo():
        today = date.today()
        out, d = [], today
        # weeklies for 5 weeks, then 3rd-Friday monthlies for a year
        while len(out) < 5:
            d += timedelta(days=1)
            if d.weekday() == 4:
                out.append(d.isoformat())
        for m in range(1, 13):
            y, mo = today.year + (today.month + m - 1) // 12, (today.month + m - 1) % 12 + 1
            first = date(y, mo, 1)
            third_fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
            if third_fri.isoformat() not in out and third_fri > today:
                out.append(third_fri.isoformat())
        return sorted(out)

    return _with_fallback("yahoo", live, demo, retries=2)


@cached(ttl=120)
def option_chain(symbol: str, expiry: str) -> Result:
    sym = norm(symbol)
    cols = ["contractSymbol", "strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility", "inTheMoney"]

    def live():
        with _YF_LOCK:
            ch = _yf().Ticker(sym).option_chain(expiry)
        calls, puts = ch.calls[cols].copy(), ch.puts[cols].copy()
        if calls.empty and puts.empty:
            raise DataError("empty chain")
        return {"calls": calls, "puts": puts}

    def demo():
        from ..analytics.options import bs_price

        spot = float(_demo_history(sym, period_start("1mo"), date.today())["Close"].iloc[-1])
        atm_vol = _demo_meta(sym)[4]
        T = max((date.fromisoformat(expiry) - date.today()).days, 1) / 365
        step = min([0.5, 1, 2.5, 5, 10, 25, 50, 100, 250, 500, 1000], key=lambda x: abs(math.log(x / (spot * 0.0125))))
        strikes = np.arange(math.floor(spot * 0.7 / step) * step, spot * 1.3 + step, step)
        rng = np.random.default_rng(_seed(sym + expiry))
        frames = {}
        for kind in ("call", "put"):
            rows = []
            for k in strikes:
                m = math.log(k / spot)
                iv = max(0.05, atm_vol * (1 - 0.6 * m / math.sqrt(max(T, 0.02)) * 0.15 + 1.2 * m * m) + 0.02 / math.sqrt(T + 0.05) * 0.1)
                px = bs_price(spot, k, T, config.RISK_FREE, iv, kind)
                spr = max(0.01, px * 0.03)
                oi = int(abs(rng.normal(1, 0.5)) * 5000 * math.exp(-8 * m * m))
                rows.append({"contractSymbol": f"{sym}{expiry.replace('-', '')[2:]}{kind[0].upper()}{int(k * 1000):08d}",
                             "strike": float(k), "lastPrice": round(px, 2), "bid": round(max(px - spr, 0), 2),
                             "ask": round(px + spr, 2), "volume": int(oi * rng.uniform(0.05, 0.4)), "openInterest": oi,
                             "impliedVolatility": iv, "inTheMoney": (k < spot) if kind == "call" else (k > spot)})
            frames[kind + "s"] = pd.DataFrame(rows)
        return frames

    return _with_fallback("yahoo", live, demo, retries=2)


# --------------------------------------------------------------------------------------
# FRED
# --------------------------------------------------------------------------------------
FRED_SERIES = {
    "DGS1MO": "1M", "DGS3MO": "3M", "DGS6MO": "6M", "DGS1": "1Y", "DGS2": "2Y", "DGS3": "3Y", "DGS5": "5Y",
    "DGS7": "7Y", "DGS10": "10Y", "DGS20": "20Y", "DGS30": "30Y",
}
MACRO_SERIES = {
    "FEDFUNDS": "Fed Funds Rate", "CPIAUCSL": "CPI (All Urban)", "CPILFESL": "Core CPI", "PCEPILFE": "Core PCE",
    "UNRATE": "Unemployment Rate", "PAYEMS": "Nonfarm Payrolls", "GDPC1": "Real GDP", "T10Y2Y": "10Y-2Y Spread",
    "T10YIE": "10Y Breakeven Inflation", "BAMLH0A0HYM2": "HY OAS", "BAMLC0A0CM": "IG OAS", "M2SL": "M2 Money Stock",
    "UMCSENT": "UMich Sentiment", "INDPRO": "Industrial Production", "HOUST": "Housing Starts", "VIXCLS": "VIX",
    "DCOILWTICO": "WTI Spot", "DTWEXBGS": "Broad USD Index", "SOFR": "SOFR", "MORTGAGE30US": "30Y Mortgage Rate",
}


@cached(ttl=3600)
def fred(series_id: str, start: str = "2000-01-01") -> Result:
    sid = series_id.upper()

    def live():
        import httpx

        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "quantterm/1.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        s = pd.to_numeric(df["value"], errors="coerce")
        s.index = df["date"]
        s = s.dropna()
        if s.empty:
            raise DataError(f"FRED series {sid} empty")
        return s

    def demo():
        rng = np.random.default_rng(_seed(sid))
        idx = pd.bdate_range(start, date.today())
        if sid in FRED_SERIES:
            # level + slope factors shared across tenors so curves stay smooth
            yrs = {"1M": 1 / 12, "3M": .25, "6M": .5, "1Y": 1, "2Y": 2, "3Y": 3, "5Y": 5, "7Y": 7, "10Y": 10, "20Y": 20, "30Y": 30}[FRED_SERIES[sid]]
            f = np.random.default_rng(99)
            level = np.cumsum(f.standard_normal(len(idx)) * 0.035)
            slope = np.cumsum(f.standard_normal(len(idx)) * 0.02)
            load = 1 - np.exp(-yrs / 3)
            base = 4.35 - 0.45 * load + 0.25 * (yrs / 30)
            vals = base + (level - level[-1]) + (slope - slope[-1]) * load + rng.standard_normal(len(idx)) * 0.004
        else:
            walk = np.cumsum(rng.standard_normal(len(idx)) * 0.02)
            vals = 100 + walk * 10
        return pd.Series(vals, index=idx)

    return _with_fallback("fred", live, demo)


def yield_curve() -> Result:
    """Today's UST curve plus 1w / 1m / 1y-ago snapshots."""
    sources, out = set(), {}
    for sid, tenor in FRED_SERIES.items():
        r = fred(sid, (date.today() - timedelta(days=500)).isoformat())
        sources.add(r.source)
        out[tenor] = r.data
    frame = pd.DataFrame(out).ffill().dropna(how="all")
    last = frame.index[-1]

    def snap(days):
        sub = frame.loc[: last - pd.Timedelta(days=days)]
        return sub.iloc[-1] if len(sub) else frame.iloc[0]

    curves = {
        "Current": frame.iloc[-1], "1W ago": snap(7), "1M ago": snap(30), "1Y ago": snap(365),
    }
    return Result({"asof": last.strftime("%Y-%m-%d"), "tenors": list(FRED_SERIES.values()),
                   "curves": {k: [None if pd.isna(x) else float(x) for x in v.to_numpy()] for k, v in curves.items()}},
                  "demo" if "demo" in sources else "fred")


# --------------------------------------------------------------------------------------
# market monitors
# --------------------------------------------------------------------------------------
MONITORS = {
    "WEI": ["^GSPC", "^NDX", "^DJI", "^RUT", "^VIX", "^STOXX50E", "^GDAXI", "^FTSE", "^FCHI", "^SSMI", "^N225", "^HSI"],
    "FX": ["EURUSD=X", "USDCHF=X", "EURCHF=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "DX-Y.NYB"],
    "CMDT": ["GC=F", "SI=F", "HG=F", "CL=F", "BZ=F", "NG=F", "ZC=F"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD"],
    "RATES": ["^IRX", "^FVX", "^TNX", "^TYX"],
    "MAG7": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"],
    "SWISS": ["NESN.SW", "NOVN.SW", "ROG.SW", "UBSG.SW", "ZURN.SW", "ABBN.SW", "CFR.SW", "SIKA.SW"],
    "SECTORS": ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"],
}


@cached(ttl=60)
def search(query: str) -> Result:
    q = query.strip()

    def live():
        res = _yf().Search(q, max_results=10, news_count=0)
        out = [{"symbol": x.get("symbol"), "name": x.get("shortname") or x.get("longname"),
                "type": x.get("quoteType"), "exchange": x.get("exchange")} for x in res.quotes]
        if not out:
            raise DataError("no matches")
        return out

    def demo():
        qu = q.upper()
        return [{"symbol": s, "name": m[0], "type": m[1], "exchange": "DEMO"} for s, m in DEMO_META.items()
                if qu in s or qu in m[0].upper()][:10]

    return _with_fallback("yahoo", live, demo)


# --------------------------------------------------------------------------------------
# screener / heatmap universe (S&P 500 large caps by sector; extend freely)
# --------------------------------------------------------------------------------------
UNIVERSE = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO", "ACN", "IBM", "INTU", "QCOM", "TXN", "AMAT", "MU", "NOW", "PANW"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "TMUS", "VZ", "T", "CMCSA"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG", "TJX", "CMG"],
    "Consumer Defensive": ["WMT", "PG", "COST", "KO", "PEP", "PM", "MDLZ", "CL", "MO"],
    "Financial Services": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BLK", "SCHW", "C", "SPGI"],
    "Healthcare": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "PFE", "AMGN", "ISRG", "DHR", "GILD"],
    "Industrials": ["CAT", "GE", "HON", "UNP", "RTX", "BA", "DE", "LMT", "UPS", "ETN"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC"],
    "Utilities": ["NEE", "SO", "DUK", "AEP"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG"],
    "Basic Materials": ["LIN", "SHW", "FCX", "NEM", "APD"],
}
UNIVERSE_SECTOR = {s: sec for sec, syms in UNIVERSE.items() for s in syms}


def universe(name: str = "SPX100") -> list[str]:
    if name.upper() in MONITORS:
        return MONITORS[name.upper()]
    return [s for syms in UNIVERSE.values() for s in syms]


def _demo_info_for(sym: str) -> dict:
    """Deterministic demo fundamentals that respect the universe's sector map."""
    rng = np.random.default_rng(_seed(sym) + 7)
    sector = UNIVERSE_SECTOR.get(sym) or _demo_meta(sym)[1]
    px = _demo_meta(sym)[3]
    return {"symbol": sym, "longName": _demo_meta(sym)[0] if sym in DEMO_META else f"{sym} (demo)", "sector": sector,
            "marketCap": px * float(rng.uniform(0.3e9, 8e9)), "trailingPE": float(rng.uniform(8, 60)),
            "forwardPE": float(rng.uniform(7, 45)), "priceToBook": float(rng.uniform(0.8, 20)),
            "dividendYield": float(rng.uniform(0, 4)), "profitMargins": float(rng.uniform(-0.05, 0.4)),
            "returnOnEquity": float(rng.uniform(-0.05, 0.6)), "revenueGrowth": float(rng.uniform(-0.1, 0.4))}


@cached(ttl=6 * 3600)
def bulk_info(symbols: tuple[str, ...]) -> Result:
    """Reference/fundamental data for many symbols (threaded; failures are skipped, not fatal)."""
    syms = [norm(s) for s in symbols]

    def live():
        from concurrent.futures import ThreadPoolExecutor

        def one(s):
            try:
                return s, info(s).data
            except Exception:  # noqa: BLE001
                return s, None

        with ThreadPoolExecutor(max_workers=4) as ex:  # more threads trigger Yahoo crumb errors
            out = {s: d for s, d in ex.map(one, syms) if d}
        if not out:
            raise DataError("no fundamentals returned")
        for s, d in out.items():
            d.setdefault("sector", UNIVERSE_SECTOR.get(s))
        return out

    return _with_fallback("yahoo", live, lambda: {s: _demo_info_for(s) for s in syms})


# --------------------------------------------------------------------------------------
# intraday bars and streaming quotes
# --------------------------------------------------------------------------------------
def _demo_intraday(sym: str, interval: str = "5m", days: int = 1) -> pd.DataFrame:
    """Synthetic intraday bars ending now, anchored to the demo daily close. Changes every bar."""
    step = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60}.get(interval, 5)
    daily = _demo_history(sym, period_start("1mo"), date.today())
    prev_close = float(daily["Close"].iloc[-2]) if len(daily) > 1 else float(daily["Close"].iloc[-1])
    vol = _demo_meta(sym)[4]
    now = pd.Timestamp.now().floor(f"{step}min")
    idx = pd.date_range(end=now, periods=max(int(days * 390 / step), 10), freq=f"{step}min")
    rng = np.random.default_rng(_seed(sym) + int(now.normalize().value // 10**9))
    sig = vol / math.sqrt(252 * 390 / step)
    path = prev_close * np.exp(np.cumsum(rng.standard_normal(len(idx)) * sig))
    hi = path * (1 + np.abs(rng.standard_normal(len(idx))) * sig * 0.5)
    lo = path * (1 - np.abs(rng.standard_normal(len(idx))) * sig * 0.5)
    op_ = np.r_[prev_close, path[:-1]]
    vol_ = np.abs(rng.standard_normal(len(idx))) * 5e4
    return pd.DataFrame({"Open": op_, "High": np.maximum(hi, np.maximum(op_, path)), "Low": np.minimum(lo, np.minimum(op_, path)),
                         "Close": path, "Volume": vol_}, index=idx)


@cached(ttl=30)
def intraday(symbol: str, interval: str = "5m", days: int = 1) -> Result:
    sym = norm(symbol)
    period = {1: "1d", 5: "5d"}.get(days, "5d")

    def live():
        df = _yf().Ticker(sym).history(period=period, interval=interval, prepost=False, auto_adjust=True)
        df = _clean_hist(df)
        if df.empty:
            raise DataError(f"No intraday data for {sym} (market closed or unsupported)")
        return df

    return _with_fallback("yahoo", live, lambda: _demo_intraday(sym, interval, days))


@cached(ttl=4)
def live_quotes(symbols: tuple[str, ...]) -> Result:
    """Near-real-time last price (Yahoo's free feed is delayed ~15 min for some exchanges)."""
    syms = [norm(s) for s in symbols]

    def live():
        yf = _yf()
        out = []
        for s in syms:
            try:
                fi = yf.Ticker(s).fast_info
                last, prev = fi.get("lastPrice") or fi.get("last_price"), fi.get("previousClose") or fi.get("previous_close")
                if last is None:
                    continue
                out.append({"symbol": s, "last": float(last), "prev": float(prev) if prev else None,
                            "pct": (float(last) / float(prev) - 1) * 100 if prev else None,
                            "time": datetime.now().isoformat(timespec="seconds")})
            except Exception:  # noqa: BLE001
                continue
        if not out:
            raise DataError("no live quotes")
        return out

    def demo():
        out = []
        for s in syms:
            bars = _demo_intraday(s, "1m", 1)
            prev = float(bars["Open"].iloc[0])
            # jitter within the current minute so the stream visibly ticks
            jit = 1 + np.random.default_rng(int(time.time()) + _seed(s)).standard_normal() * _demo_meta(s)[4] / math.sqrt(252 * 390 * 60) * 4
            last = float(bars["Close"].iloc[-1]) * jit
            out.append({"symbol": s, "last": last, "prev": prev, "pct": (last / prev - 1) * 100,
                        "time": datetime.now().isoformat(timespec="seconds")})
        return out

    return _with_fallback("yahoo", live, demo)


# --------------------------------------------------------------------------------------
# events: earnings and macro calendar
# --------------------------------------------------------------------------------------
@cached(ttl=6 * 3600)
def earnings_date(symbol: str) -> Result:
    """Next (and recent) earnings dates with EPS estimate / actual where available."""
    sym = norm(symbol)

    if not is_single_stock(sym):
        raise DataError(f"{sym} has no earnings (not a single stock)")

    def live():
        t = _yf().Ticker(sym)
        rows = []
        try:
            with _YF_LOCK:
                ed = t.get_earnings_dates(limit=12)
            if ed is not None and not ed.empty:
                for ts_, r in ed.iterrows():
                    d = pd.Timestamp(ts_)
                    if d.tzinfo is not None:
                        d = d.tz_convert(None)
                    rows.append({"date": d.strftime("%Y-%m-%d"), "eps_est": r.get("EPS Estimate"),
                                 "eps_act": r.get("Reported EPS"), "surprise_pct": r.get("Surprise(%)")})
        except Exception:  # noqa: BLE001
            pass
        if not rows:
            cal = t.calendar or {}
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            for d in dates or []:
                rows.append({"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "eps_est": cal.get("Earnings Average"),
                             "eps_act": None, "surprise_pct": None})
        if not rows:
            raise DataError(f"no earnings dates for {sym}")
        return sorted(rows, key=lambda r: r["date"])

    def demo():
        sector = UNIVERSE_SECTOR.get(sym) or _demo_meta(sym)[1]
        if sector in ("ETF", "Index", "FX", "Commodity", "Crypto", "Rates") or sym.startswith("^") or "=" in sym:
            raise DataError(f"{sym} has no earnings (not a single stock)")
        rng = np.random.default_rng(_seed(sym) + 11)
        anchor = date.today() + timedelta(days=int(rng.integers(3, 80)))
        rows = []
        for q in range(-4, 2):
            d = anchor + timedelta(days=91 * q)
            est = float(rng.uniform(0.5, 4))
            act = est * (1 + float(rng.normal(0.03, 0.06))) if d < date.today() else None
            rows.append({"date": d.isoformat(), "eps_est": est, "eps_act": act,
                         "surprise_pct": (act / est - 1) * 100 if act else None})
        return rows

    return _with_fallback("yahoo", live, demo)


# Scheduled FOMC decision days (second day of each meeting). Source: federalreserve.gov calendars.
FOMC_DATES = ["2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29",
              "2025-12-10", "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
              "2026-10-28", "2026-12-09"]


def macro_calendar(days_ahead: int = 45) -> list[dict]:
    """Recurring US macro releases. FOMC dates are published; CPI/NFP/PCE/GDP dates are rule-based estimates
    (NFP = first Friday) and can be off by a few days around holidays."""
    today = date.today()
    end = today + timedelta(days=days_ahead)
    out = [{"date": d, "event": "FOMC rate decision", "kind": "central bank", "estimated": False}
           for d in FOMC_DATES if today.isoformat() <= d <= end.isoformat()]
    d = today.replace(day=1)
    while d <= end:
        first_fri = d + timedelta(days=(4 - d.weekday()) % 7)
        for dt, ev in [(first_fri, "Nonfarm payrolls / unemployment"),
                       (d + timedelta(days=11), "CPI inflation (approx.)"),
                       (d + timedelta(days=26), "PCE inflation (approx.)")]:
            if dt.weekday() >= 5:
                dt += timedelta(days=7 - dt.weekday())
            if today <= dt <= end:
                out.append({"date": dt.isoformat(), "event": ev, "kind": "macro", "estimated": True})
        if d.month in (1, 4, 7, 10):
            gdp = d + timedelta(days=28)
            if gdp.weekday() >= 5:
                gdp -= timedelta(days=gdp.weekday() - 3)
            if today <= gdp <= end:
                out.append({"date": gdp.isoformat(), "event": "GDP advance estimate (approx.)", "kind": "macro", "estimated": True})
        d = (d + timedelta(days=32)).replace(day=1)
    return sorted(out, key=lambda x: x["date"])


# --------------------------------------------------------------------------------------
# diagnostics: `python run.py --check`
# --------------------------------------------------------------------------------------
def diagnose(verbose: bool = True) -> list[dict]:
    """Exercise every live data call once and report which ones work. Used to debug Yahoo/FRED changes."""
    if config.DATA_MODE == "demo":
        raise SystemExit("--check needs live data; run without --demo")
    checks = [
        ("Yahoo reachable", lambda: online("yahoo") or (_ for _ in ()).throw(DataError("unreachable"))),
        ("FRED reachable", lambda: online("fred") or (_ for _ in ()).throw(DataError("unreachable"))),
        ("history AAPL 1y", lambda: f"{len(history('AAPL', '1y').data)} bars"),
        ("closes SPY,TLT", lambda: f"{closes(('SPY', 'TLT'), '1y').data.shape}"),
        ("quotes WEI", lambda: f"{sum(q.get('last') is not None for q in quotes(tuple(MONITORS['WEI'])).data)}/{len(MONITORS['WEI'])} quoted"),
        ("info AAPL", lambda: f"{len(info('AAPL').data)} fields, PE={info('AAPL').data.get('trailingPE')}"),
        ("financials MSFT", lambda: f"{len(financials('MSFT').data['income']['rows'])} income rows"),
        ("news NVDA", lambda: f"{len(news('NVDA').data)} items"),
        ("option expiries SPY", lambda: f"{len(option_expiries('SPY').data)} expiries"),
        ("option chain SPY", lambda: f"{len(option_chain('SPY', option_expiries('SPY').data[2]).data['calls'])} calls"),
        ("intraday SPY 5m", lambda: f"{len(intraday('SPY', '5m', 1).data)} bars"),
        ("live quote AAPL", lambda: f"{live_quotes(('AAPL',)).data[0]['last']}"),
        ("earnings AAPL", lambda: f"next/last: {earnings_date('AAPL').data[-1]['date']}"),
        ("search 'nestle'", lambda: f"{len(search('nestle').data)} matches"),
        ("FRED DGS10", lambda: f"last {fred('DGS10').data.iloc[-1]:.2f}%"),
        ("yield curve", lambda: f"as of {yield_curve().data['asof']}"),
    ]
    results = []
    for name, fn in checks:
        t0 = time.time()
        try:
            msg, ok = str(fn()), True
        except Exception as e:  # noqa: BLE001
            msg, ok = f"{type(e).__name__}: {e}", False
        results.append({"check": name, "ok": ok, "detail": msg, "secs": round(time.time() - t0, 2)})
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'}  {name:<22} {msg[:90]}  ({results[-1]['secs']}s)")
    return results
