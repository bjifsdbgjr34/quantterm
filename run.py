#!/usr/bin/env python3
"""Start QuantTerm:  python run.py [--demo|--live|--check] [--port 8765] [--no-browser]"""
import argparse
import os
import threading
import webbrowser


def main():
    ap = argparse.ArgumentParser(description="QuantTerm - quant research & trading terminal")
    ap.add_argument("--demo", action="store_true", help="synthetic data only (offline)")
    ap.add_argument("--live", action="store_true", help="live data only, no demo fallback")
    ap.add_argument("--host", default=os.environ.get("QT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("QT_PORT", "8765")))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check", action="store_true", help="test every live Yahoo/FRED data call and exit")
    a = ap.parse_args()
    if a.check:
        os.environ["QT_DATA"] = "live"
        from quantterm.data.providers import diagnose

        print("\n  QuantTerm live data check (Yahoo Finance + FRED)\n")
        res = diagnose()
        bad = [r for r in res if not r["ok"]]
        print(f"\n  {len(res) - len(bad)}/{len(res)} checks passed.")
        if bad:
            print("  Paste the FAIL lines into Claude Code to get them fixed. Upgrading yfinance often helps:")
            print("    pip install -U yfinance")
        raise SystemExit(1 if bad else 0)
    if a.demo:
        os.environ["QT_DATA"] = "demo"
    elif a.live:
        os.environ["QT_DATA"] = "live"

    import uvicorn

    url = f"http://{a.host}:{a.port}"
    print(f"\n  QuantTerm running at {url}  (data mode: {os.environ.get('QT_DATA', 'auto')})\n")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("quantterm.server:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
