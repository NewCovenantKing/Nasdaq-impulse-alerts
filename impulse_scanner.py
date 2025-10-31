#!/usr/bin/env python3
"""
impulse_scanner.py

Full scanner script:
- pulls live prices (yfinance)
- simple impulse vs correction detection (last-close vs prior)
- simple "wave" label using short momentum (5-bar lookback)
- 44% Fibonacci retracement check over recent swing
- Silver Bullet session label (London / NY / Asia) inferred from SAST (UTC+2) or from env RUN_SESSION
- Sends a compact Telegram message to your CHAT_ID via BOT_TOKEN

Usage:
 - Put BOT_TOKEN and CHAT_ID in your GitHub Actions secrets (or env locally).
 - Workflow should trigger this script at the scheduled killzone times (we include session label logic below).
"""

import os
import datetime
import pytz
import time
from statistics import mean
from typing import Dict, Tuple, Optional

import yfinance as yf
from telegram import Bot

# --------------------------
# Configuration
# --------------------------
# Tickers (Yahoo)
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F",
}

# How many bars to pull (15m bars); keep small to be fast in CI
PERIOD = "5d"          # 5 days
INTERVAL = "15m"       # 15-minute bars

# Momentum / wave settings
MOMENTUM_LOOKBACK = 5  # bars to decide impulse vs correction
IMPULSE_THRESHOLD = 0.0008  # relative price move threshold to flag impulse

# Fibonacci check settings
FIB_LOOKBACK_BARS = 48  # roughly 12 hours of 15m bars
FIB_TARGET = 0.44       # 44%

# Timezone for session inference (SAST = UTC+2)
SAST = pytz.timezone("Africa/Johannesburg")

# Telegram (must be provided via env / secrets)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")  # numeric string

if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

bot = Bot(token=BOT_TOKEN)

# --------------------------
# Utility functions
# --------------------------
def now_sast() -> datetime.datetime:
    return datetime.datetime.now(pytz.utc).astimezone(SAST)

def infer_session_from_time(dt: Optional[datetime.datetime] = None) -> str:
    """Return 'LONDON', 'NY', 'ASIA' or 'UNKNOWN' based on SAST local time.
    These boundaries are intentionally broad and can be overridden with env RUN_SESSION.
    """
    if dt is None:
        dt = now_sast()
    h = dt.hour
    # London killzone roughly 08:00-10:00 SAST (market overlap variable)
    if 7 <= h <= 10:
        return "LONDON"
    # NY overlap roughly 13:00-17:00 SAST
    if 12 <= h <= 17:
        return "NY"
    # Asia session earlier
    if 0 <= h <= 6:
        return "ASIA"
    return "UNKNOWN"

def safe_download(ticker: str, retries: int = 2, wait_sec: float = 1.0):
    """Download price data with retries; returns dataframe or None."""
    for attempt in range(retries + 1):
        try:
            df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)
            if df is None or df.empty:
                raise ValueError("No data")
            return df
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(wait_sec)
                wait_sec *= 1.5
                continue
            return None

def fib_44_check(df):
    """Check whether price sits near 44% retracement between recent swing high/low."""
    if df is None or df.empty or len(df) < 6:
        return None
    window = df[-FIB_LOOKBACK_BARS:] if len(df) >= FIB_LOOKBACK_BARS else df
    high = window["High"].max()
    low = window["Low"].min()
    if high == low:
        return None
    retracement_level = high - (high - low) * FIB_TARGET
    last_close = window["Close"].iloc[-1]
    # proximity within 0.4% relative
    proximity = abs(last_close - retracement_level) / retracement_level
    return {"high": high, "low": low, "level": retracement_level, "last": last_close, "prox": proximity}

def detect_impulse_vs_correction(df) -> Tuple[str, float]:
    """
    Very simple rule:
     - compute percent change from previous close to last close.
     - compute short momentum = last / mean(last N) - 1
     - classify 'Impulse' when momentum above threshold; otherwise 'Correction'
    """
    if df is None or df.empty or len(df) < MOMENTUM_LOOKBACK + 1:
        return ("UNKNOWN", 0.0)
    closes = df["Close"].dropna()
    last = closes.iloc[-1]
    prior = closes.iloc[-2]
    pct = (last - prior) / prior if prior else 0.0
    short_mean = mean(closes.iloc[-MOMENTUM_LOOKBACK:])
    momentum = (last / short_mean) - 1 if short_mean else 0.0
    label = "Impulse" if abs(momentum) >= IMPULSE_THRESHOLD else "Correction"
    direction = "Bullish" if momentum > 0 else "Bearish" if momentum < 0 else "Neutral"
    return (f"{label} - {direction}", momentum)

# --------------------------
# Main scan and message compose
# --------------------------
def analyze_symbol(name: str, ticker: str) -> Dict:
    result = {"name": name, "ticker": ticker, "ok": False, "note": "", "last": None}
    df = safe_download(ticker)
    if df is None:
        result["note"] = "No data / download failed"
        return result
    last_close = df["Close"].iloc[-1]
    result["last"] = float(last_close)
    wave_label, momentum = detect_impulse_vs_correction(df)
    fib = fib_44_check(df)
    fib_note = ""
    if fib:
        if fib["prox"] <= 0.006:  # within ~0.6% (tunable)
            fib_note = f"44% retracement ~{fib['level']:.5f} (prox {fib['prox']:.4f})"
        else:
            fib_note = f"44% not near (prox {fib['prox']:.4f})"
    result.update({
        "ok": True,
        "wave": wave_label,
        "momentum": float(momentum),
        "fib_note": fib_note,
    })
    return result

def build_message(session_label: str, analyses: Dict[str, Dict], failed: Dict[str, str]) -> str:
    header = f"Impulse Scanner — {session_label}\nTime: {now_sast().strftime('%Y-%m-%d %H:%M %Z')}\n"
    lines = [header, "Summary:"]
    for name, r in analyses.items():
        if not r.get("ok"):
            lines.append(f"{name}: ❌ {r.get('note')}")
            continue
        last = r.get("last")
        wave = r.get("wave")
        fib = r.get("fib_note", "")
        lines.append(f"{name}: {wave} | Last: {last:.5f} {('| ' + fib) if fib else ''}")
    if failed:
        lines.append("\nFailures:")
        for k, v in failed.items():
            lines.append(f"{k}: {v}")
    lines.append("\n--- End ---")
    return "\n".join(lines)

def run_scan():
    # session override via env if needed
    session_env = os.environ.get("RUN_SESSION")
    session_label = session_env.upper() if session_env else infer_session_from_time()

    analyses = {}
    failed = {}
    for name, ticker in SYMBOLS.items():
        res = analyze_symbol(name, ticker)
        analyses[name] = res
        if not res.get("ok"):
            failed[name] = res.get("note", "unknown")

    msg = build_message(session_label, analyses, failed)
    # send message
    try:
        bot.send_message(chat_id=CHAT_ID, text=msg)
        print("Sent Telegram message.")
    except Exception as e:
        # log error to stdout (GitHub Actions will capture)
        print("Failed sending Telegram message:", e)
        raise

# --------------------------
# Entry point
# --------------------------
if __name__ == "__main__":
    run_scan()
