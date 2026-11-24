"""Option pricing: Black-Scholes-Merton (with continuous dividend yield), greeks,
implied volatility and a Cox-Ross-Rubinstein binomial tree for American exercise."""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


def _d1d2(S, K, T, r, sigma, q=0.0):
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    return d1, d1 - sigma * sqrtT


def bs_price(S: float, K: float, T: float, r: float, sigma: float, kind: str = "call", q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
        return intrinsic
    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    if kind == "call":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


def greeks(S: float, K: float, T: float, r: float, sigma: float, kind: str = "call", q: float = 0.0) -> dict:
    """Delta, gamma, vega (per 1 vol pt), theta (per calendar day), rho (per 1%), plus 2nd-order vanna/volga/charm."""
    T = max(T, 1e-8)
    sigma = max(sigma, 1e-8)
    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    sqrtT = math.sqrt(T)
    pdf = norm.pdf(d1)
    dq, dr = math.exp(-q * T), math.exp(-r * T)
    if kind == "call":
        delta = dq * norm.cdf(d1)
        theta = (-S * dq * pdf * sigma / (2 * sqrtT) - r * K * dr * norm.cdf(d2) + q * S * dq * norm.cdf(d1))
        rho = K * T * dr * norm.cdf(d2)
        charm = q * dq * norm.cdf(d1) - dq * pdf * (2 * (r - q) * T - d2 * sigma * sqrtT) / (2 * T * sigma * sqrtT)
    else:
        delta = -dq * norm.cdf(-d1)
        theta = (-S * dq * pdf * sigma / (2 * sqrtT) + r * K * dr * norm.cdf(-d2) - q * S * dq * norm.cdf(-d1))
        rho = -K * T * dr * norm.cdf(-d2)
        charm = -q * dq * norm.cdf(-d1) - dq * pdf * (2 * (r - q) * T - d2 * sigma * sqrtT) / (2 * T * sigma * sqrtT)
    gamma = dq * pdf / (S * sigma * sqrtT)
    vega = S * dq * pdf * sqrtT
    vanna = -dq * pdf * d2 / sigma
    volga = vega * d1 * d2 / sigma
    return {
        "price": bs_price(S, K, T, r, sigma, kind, q), "delta": delta, "gamma": gamma, "vega": vega / 100,
        "theta": theta / 365, "rho": rho / 100, "vanna": vanna / 100, "volga": volga / 10000, "charm": charm / 365,
        "d1": d1, "d2": d2, "prob_itm": norm.cdf(d2) if kind == "call" else norm.cdf(-d2),
    }


def implied_vol(price: float, S: float, K: float, T: float, r: float, kind: str = "call", q: float = 0.0) -> float | None:
    if T <= 0 or price <= 0:
        return None
    intrinsic = max(S * math.exp(-q * T) - K * math.exp(-r * T), 0) if kind == "call" else max(K * math.exp(-r * T) - S * math.exp(-q * T), 0)
    if price < intrinsic - 1e-9:
        return None
    try:
        return brentq(lambda v: bs_price(S, K, T, r, v, kind, q) - price, 1e-4, 6.0, xtol=1e-7, maxiter=200)
    except ValueError:
        return None


def binomial_price(S, K, T, r, sigma, kind="call", q=0.0, american=True, steps=300) -> float:
    if T <= 0:
        return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    disc = math.exp(-r * dt)
    j = np.arange(steps + 1)
    ST = S * u ** (steps - j) * d**j
    V = np.maximum(ST - K, 0) if kind == "call" else np.maximum(K - ST, 0)
    for i in range(steps - 1, -1, -1):
        V = disc * (p * V[:-1] + (1 - p) * V[1:])
        if american:
            ST = S * u ** (i - np.arange(i + 1)) * d ** np.arange(i + 1)
            V = np.maximum(V, (ST - K) if kind == "call" else (K - ST))
    return float(V[0])


def payoff_profile(legs: list[dict], S: float, r: float, T_eval: float = 0.0, n: int = 121) -> dict:
    """P&L of a multi-leg position across spot. Each leg: {kind: call|put|stock, strike, qty, T, sigma, premium}.

    T_eval = time elapsed (years) at which to evaluate; 0 -> expiry payoff using each leg's T as remaining."""
    grid = np.linspace(S * 0.6, S * 1.4, n)
    expiry = np.zeros(n)
    today = np.zeros(n)
    for leg in legs:
        qty = leg.get("qty", 1)
        if leg["kind"] == "stock":
            expiry += qty * (grid - leg.get("premium", S))
            today += qty * (grid - leg.get("premium", S))
            continue
        K, T, sig, prem = leg["strike"], leg["T"], leg["sigma"], leg["premium"]
        pay = np.maximum(grid - K, 0) if leg["kind"] == "call" else np.maximum(K - grid, 0)
        expiry += qty * (pay - prem)
        today += qty * (np.array([bs_price(s, K, T, r, sig, leg["kind"]) for s in grid]) - prem)
    return {"spot": grid.tolist(), "expiry": expiry.tolist(), "today": today.tolist()}
