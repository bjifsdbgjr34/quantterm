"""Option skew / term-structure signals and a simple option strategy scanner.

Scanner logic: build standard structures from listed strikes, price them at mid, and evaluate the
expiry payoff under a lognormal terminal distribution with a *user-supplied* volatility forecast
(default: realized vol). Expected value is only as good as that forecast, and mids are not fills.
"""
from __future__ import annotations

import math

import numpy as np

from .options import greeks


def _nearest(points: list[dict], key: str, target: float) -> dict | None:
    pts = [p for p in points if p.get(key) is not None]
    return min(pts, key=lambda p: abs(p[key] - target)) if pts else None


def skew_metrics(spot: float, T: float, r: float, calls: list[dict], puts: list[dict]) -> dict:
    """25-delta risk reversal & butterfly, ATM IV and a put-wing slope from one expiry."""
    def with_delta(rows, kind):
        out = []
        for x in rows:
            iv = x.get("iv")
            if iv and 0.02 < iv < 3:
                out.append({"strike": x["strike"], "iv": iv, "delta": greeks(spot, x["strike"], T, r, iv, kind)["delta"]})
        return out

    c, p = with_delta(calls, "call"), with_delta(puts, "put")
    c25, p25 = _nearest(c, "delta", 0.25), _nearest(p, "delta", -0.25)
    c50 = _nearest(c, "delta", 0.5)
    p10 = _nearest(p, "delta", -0.10)
    atm = c50["iv"] if c50 else None
    out = {"atm_iv": atm, "call25_iv": c25 and c25["iv"], "put25_iv": p25 and p25["iv"], "put10_iv": p10 and p10["iv"]}
    if c25 and p25 and atm:
        out["rr25"] = c25["iv"] - p25["iv"]  # negative = puts richer (normal equity skew)
        out["bf25"] = 0.5 * (c25["iv"] + p25["iv"]) - atm
        out["put_skew_ratio"] = p25["iv"] / atm
    return out


def term_structure(points: list[dict]) -> dict:
    """points: [{days, atm_iv}] sorted by days. Slope in vol points per 30 days between front and ~3M."""
    pts = [p for p in points if p.get("atm_iv")]
    if len(pts) < 2:
        return {}
    front = pts[0]
    back = min(pts[1:], key=lambda p: abs(p["days"] - 90))
    slope = (back["atm_iv"] - front["atm_iv"]) / max(back["days"] - front["days"], 1) * 30
    state = "backwardation (stress: near-term vol above longer-term)" if slope < -0.002 else \
        "contango (normal: longer-dated vol higher)" if slope > 0.002 else "flat"
    return {"front_days": front["days"], "front_iv": front["atm_iv"], "back_days": back["days"], "back_iv": back["atm_iv"],
            "slope_per_30d": slope, "state": state}


def _payoff(legs: list[dict], ST: np.ndarray) -> np.ndarray:
    v = np.zeros_like(ST)
    for leg in legs:
        intr = np.maximum(ST - leg["strike"], 0) if leg["kind"] == "call" else np.maximum(leg["strike"] - ST, 0)
        v += leg["qty"] * (intr - leg["premium"])
    return v


def evaluate(legs: list[dict], spot: float, T: float, r: float, sigma: float) -> dict:
    """Expiry P&L stats per 1 contract-unit (x100 for US equity options)."""
    # terminal lognormal grid (risk-neutral drift ~ r; we only care about the shape of the forecast)
    z = np.linspace(-6, 6, 2401)
    dz = z[1] - z[0]
    ST = spot * np.exp((r - 0.5 * sigma**2) * T + sigma * math.sqrt(T) * z)
    pdf = np.exp(-0.5 * z**2) / math.sqrt(2 * math.pi) * dz
    pnl = _payoff(legs, ST)
    grid = np.linspace(0.0, spot * 3, 3001)  # the price floor at 0 bounds the left side exactly
    gp = _payoff(legs, grid)
    signs = np.sign(gp)
    be = [float(grid[i]) for i in range(1, len(grid)) if signs[i] != signs[i - 1] and signs[i] != 0]
    net = sum(leg["qty"] * leg["premium"] for leg in legs)
    max_p, max_l = float(gp.max()), float(gp.min())
    # beyond the grid the payoff is linear: a non-zero slope at the right edge means unbounded
    if gp[-1] > gp[-2] + 1e-9:
        max_p = math.inf
    if gp[-1] < gp[-2] - 1e-9:
        max_l = -math.inf
    return {"net_premium": net, "max_profit": max_p, "max_loss": max_l, "breakevens": be[:4],
            "pop": float(pdf[pnl > 0].sum()), "ev": float((pdf * pnl).sum())}


def scan(spot: float, T: float, r: float, calls: list[dict], puts: list[dict], sigma_forecast: float,
         width_pct: float = 0.05) -> list[dict]:
    """Evaluate a menu of standard structures; rank by expected value per unit of max loss."""
    def book(rows):
        return {round(x["strike"], 4): x for x in rows if x.get("mid") and x["mid"] > 0 and x.get("iv")}

    C, P = book(calls), book(puts)
    ks = sorted(set(C) & set(P))
    if len(ks) < 3:
        return []

    def k_near(target, pool):
        return min(pool, key=lambda k: abs(k - target)) if pool else None

    atm = k_near(spot, ks)
    w = spot * width_pct
    up1 = k_near(atm + w, [k for k in C if k > atm])
    up2 = k_near(atm + 2 * w, [k for k in C if up1 is not None and k > up1])
    dn1 = k_near(atm - w, [k for k in P if k < atm])
    dn2 = k_near(atm - 2 * w, [k for k in P if dn1 is not None and k < dn1])

    def L(kind, k, q):
        src = C if kind == "call" else P
        return {"kind": kind, "strike": k, "qty": q, "premium": src[k]["mid"], "iv": src[k]["iv"]}

    menu = {
        "Long straddle": lambda: [L("call", atm, 1), L("put", atm, 1)],
        "Short straddle": lambda: [L("call", atm, -1), L("put", atm, -1)],
        "Long strangle": lambda: [L("call", up1, 1), L("put", dn1, 1)],
        "Short strangle": lambda: [L("call", up1, -1), L("put", dn1, -1)],
        "Bull call spread": lambda: [L("call", atm, 1), L("call", up1, -1)],
        "Bear put spread": lambda: [L("put", atm, 1), L("put", dn1, -1)],
        "Bull put credit spread": lambda: [L("put", dn1, -1), L("put", dn2, 1)],
        "Bear call credit spread": lambda: [L("call", up1, -1), L("call", up2, 1)],
        "Short iron condor": lambda: [L("put", dn2, 1), L("put", dn1, -1), L("call", up1, -1), L("call", up2, 1)],
        "Long call butterfly": lambda: [L("call", dn1 if dn1 in C else atm, 1), L("call", atm, -2), L("call", up1, 1)],
        "Long call": lambda: [L("call", atm, 1)],
        "Long put": lambda: [L("put", atm, 1)],
    }
    out = []
    for name, build in menu.items():
        try:
            legs = build()
        except (KeyError, TypeError):
            continue
        if any(leg["strike"] is None for leg in legs) or len({(x["kind"], x["strike"]) for x in legs}) < len(legs):
            continue  # strikes collapsed onto each other (chain too sparse for this width)
        ev = evaluate(legs, spot, T, r, sigma_forecast)
        iv_ev = evaluate(legs, spot, T, r, float(np.mean([x["iv"] for x in legs])))
        g = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        for leg in legs:
            gg = greeks(spot, leg["strike"], T, r, leg["iv"], leg["kind"])
            for k in g:
                g[k] += leg["qty"] * gg[k]
        risk = abs(ev["max_loss"]) if math.isfinite(ev["max_loss"]) and ev["max_loss"] < 0 else None
        out.append({"name": name, "legs": [{k: v for k, v in x.items()} for x in legs], **ev, "pop_iv": iv_ev["pop"],
                    "ev_iv": iv_ev["ev"], "ev_per_risk": ev["ev"] / risk if risk else None,
                    "defined_risk": risk is not None, **g})
    out.sort(key=lambda x: (x["ev_per_risk"] if x["ev_per_risk"] is not None else -1e9), reverse=True)
    return out
