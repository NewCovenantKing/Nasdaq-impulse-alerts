# impulse_scanner.py
# Full scanner: Yahoo price pulls, simple Elliott-style fractal impulse detector,
# Silver-Bullet session zone check, and Telegram alert sender.
# Meant for GitHub Actions environment. Reads BOT_TOKEN and CHAT_ID from env.

import os
import datetime
import pytz
import math
import statistics
import yfinance as yf
from telegram import Bot

# ---------- Config (edit if needed) ----------
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"
}
LOOKBACK_1M = 9       # number of 1m candles
LOOKBACK_5M = 12      # number of 5m candles (for trend confirmation)
IMPULSE_PCT = 0.0030  # 0.30% move threshold for strong impulse over lookback
WEAK_PCT = IMPULSE_PCT / 2.0
SESSION_HIGH_LOW_LOOKBACK_HOURS = 24  # session high/low check window
SB_PROXIMITY_PCT = 0.0018  # 0.18% = proximity to session high/low considered "at liquidity"
# ------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")
bot = Bot(token=BOT_TOKEN)

def utc_now():
    return datetime.datetime.utcnow()

def session_tag_from_utc(dt_utc):
    h = dt_utc.hour
    # approximate session bins (UTC)
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "London"
    return "NY"

def fetch_history(symbol, period_minutes, interval):
    t = yf.Ticker(symbol)
    # yfinance uses minutes when using like "60m" or "1d"; for small intervals use 'm' with small period
    # Use an approximate period string; we request a bit more and slice
    try:
        df = t.history(period=f"{max(5, period_minutes)}m", interval=interval, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df

def pct_change(first, last):
    if first == 0:
        return 0.0
    return (last - first) / first

def monotonic_check(arr):
    # returns (rising_count, falling_count)
    rising = all(arr[i] < arr[i+1] for i in range(len(arr)-1))
    falling = all(arr[i] > arr[i+1] for i in range(len(arr)-1))
    return rising, falling

def detect_fractal_impulse(sym, minutes1=LOOKBACK_1M, minutes5=LOOKBACK_5M):
    """
    Heuristic:
    - Strong impulse if:
      * 1m: last 3 closes monotonic (rising/falling)
      * 1m net move over lookback > IMPULSE_PCT in same dir
      * 5m net move over lookback > IMPULSE_PCT/1.5 confirming direction
    - Weak move if some conditions partially met (WEAK_PCT)
    - Else correction/neutral
    """
    # fetch 1m and 5m
    df1 = fetch_history(SYMBOLS[sym], minutes1, "1m")
    df5 = fetch_history(SYMBOLS[sym], minutes5, "5m")
    # fallback safety
    if df1 is None or df1.empty:
        return {"type":"unknown","dir":"neutral","move":0.0,"conf":0}
    try:
        closes1 = list(df1["Close"].values)[-minutes1:]
    except Exception:
        closes1 = list(df1["Close"].values)
    last1_first = float(closes1[0])
    last1_last  = float(closes1[-1])
    move1 = pct_change(last1_first, last1_last)
    rising1, falling1 = monotonic_check(closes1[-3:]) if len(closes1) >= 3 else (False,False)

    # 5m confirmation
    move5 = 0.0
    conf_dir = None
    if df5 is not None and not df5.empty:
        closes5 = list(df5["Close"].values)[-minutes5:]
        if len(closes5) >= 2:
            move5 = pct_change(float(closes5[0]), float(closes5[-1]))
            conf_dir = "BUY" if move5 > 0 else ("SELL" if move5 < 0 else None)

    # decide
    if rising1 and move1 > IMPULSE_PCT and move5 > (IMPULSE_PCT/1.5):
        return {"type":"impulse","dir":"BUY","move":move1,"conf":2}
    if falling1 and move1 < -IMPULSE_PCT and move5 < -(IMPULSE_PCT/1.5):
        return {"type":"impulse","dir":"SELL","move":move1,"conf":2}

    # weaker
    if (rising1 and move1 > WEAK_PCT) or (move1 > WEAK_PCT and move5 > 0):
        return {"type":"weak_move","dir":"BUY","move":move1,"conf":1}
    if (falling1 and move1 < -WEAK_PCT) or (move1 < -WEAK_PCT and move5 < 0):
        return {"type":"weak_move","dir":"SELL","move":move1,"conf":1}

    # if net move is small but volatility high -> correction
    return {"type":"correction","dir":"neutral","move":move1,"conf":0}

def session_high_low(symbol):
    """
    Compute session (daily 24h) high/low from recent data (use 1h or 5m history)
    """
    # fetch 1h candles for last 2 days to cover timezones
    t = yf.Ticker(SYMBOLS[symbol])
    try:
        df = t.history(period="2d", interval="30m", auto_adjust=False)
    except Exception:
        return None, None
    if df is None or df.empty:
        return None, None
    high = float(df["High"].max())
    low  = float(df["Low"].min())
    return high, low

def near_session_liquidity(price, session_high, session_low):
    if session_high is None or session_low is None or price is None:
        return None
    # closeness in percent
    if session_high != 0 and abs(price - session_high)/session_high <= SB_PROXIMITY_PCT:
        return "Near Session High"
    if session_low != 0 and abs(price - session_low)/session_low <= SB_PROXIMITY_PCT:
        return "Near Session Low"
    # also check if price above session high by small margin (sweep)
    if session_high != 0 and (price - session_high)/session_high > SB_PROXIMITY_PCT:
        return "Above Session High (sweep)"
    if session_low != 0 and (session_low - price)/session_low > SB_PROXIMITY_PCT:
        return "Below Session Low (sweep)"
    return None

def build_message(all_results, session_tag):
    now = utc_now().strftime("%Y-%m-%d %H:%M UTC")
    header = f"📡 Impulse Scanner + Elliott-fractal (v1)\n{now} — Session: {session_tag}\n"
    lines = [header]
    for sym, data in all_results.items():
        price = data.get("price")
        r = data.get("analysis")
        sb = data.get("sb_note") or ""
        move_pct = f"{r['move']*100:+.2f}%" if r else "0.00%"
        conf = r.get("conf",0) if r else 0
        if r["type"] == "impulse":
            wave = "Impulse"
            icon = "⚡"
        elif r["type"] == "weak_move":
            wave = "Weak Move"
            icon = "🔶"
        elif r["type"] == "correction":
            wave = "Correction"
            icon = "🔁"
        else:
            wave = "Unknown"
            icon = "❓"
        lines.append(f"{sym}: {icon} {wave} | {r['dir']} | {move_pct} | conf={conf} {(' | ' + sb) if sb else ''}")
    lines.append("\nNotes: impulse = 1m monotonic + 5m confirmation. SB proximity uses 24h high/low.")
    return "\n".join(lines)

def main():
    session = session_tag_from_utc(utc_now())
    results = {}
    for key in SYMBOLS.keys():
        # fetch latest price (single tick)
        ticker = yf.Ticker(SYMBOLS[key])
        # try fast quote
        try:
            info = ticker.history(period="2m", interval="1m", auto_adjust=False)
            price = float(info["Close"].iloc[-1]) if (info is not None and not info.empty) else None
        except Exception:
            price = None
        analysis = detect_fractal_impulse(key)
        # compute session high/low and SB note
        sh, sl = session_high_low(key)
        sb_note = near_session_liquidity(price, sh, sl)
        results[key] = {"price": price, "analysis": analysis, "sb_note": sb_note}
    message = build_message(results, session)
    bot.send_message(chat_id=CHAT_ID, text=message)

if __name__ == "__main__":
    main()
