# impulse_scanner.py
# Replace the entire file in your repo with this.
# Requirements (already used in workflow): yfinance, python-telegram-bot
# Env vars (set in GitHub Actions repo secrets): BOT_TOKEN, CHAT_ID

import os
import datetime as dt
import pytz
import traceback
import math
import statistics
import yfinance as yf
from telegram import Bot

# ---------- CONFIG ----------
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"
}

# how many 1-minute candles to fetch (keeps quick)
PERIOD = "120m"
INTERVAL = "1m"

# minimum percent move to consider (helps ignore noise)
MIN_MOVE_PCT = 0.0004   # 0.04% default filter

# ATR lookback for volatility baseline
ATR_LENGTH = 14

# ---------- HELPERS ----------
def utcnow():
    return dt.datetime.now(tz=pytz.UTC)

def session_zone_for_utc(ts_utc):
    # rough session windows (UTC)
    h = ts_utc.hour
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "London"
    return "NY"

def pct(a, b):
    if b == 0:
        return 0.0
    return (a - b) / abs(b)

def compute_atr(highs, lows, closes, length=ATR_LENGTH):
    # simple ATR using TR on the series (len must be >=2)
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if not trs:
        return 0.0
    # use last `length` TRs
    trs = trs[-length:]
    return statistics.mean(trs)

def is_impulse(closes, opens, highs, lows):
    # Simple impulse detector:
    # - last 3 closes trending same direction
    # - magnitude relative to ATR and percent threshold
    if len(closes) < 5:
        return None
    last = closes[-1]
    prev1 = closes[-2]
    prev2 = closes[-3]
    # check direction
    up = last > prev1 > prev2
    down = last < prev1 < prev2
    if not (up or down):
        return None

    atr = compute_atr(highs, lows, closes)
    move = abs(last - prev1)
    # require a minimum absolute move (relative to ATR) and pct
    if atr > 0 and move < 0.5 * atr:
        return None
    if abs(pct(last, prev1)) < MIN_MOVE_PCT:
        return None

    return "Impulse Up" if up else "Impulse Down"

def wave_label(closes):
    # very simple EW-ish label:
    # if impulse detected -> "Impulse", else "Correction/Range"
    # (keeps logic conservative)
    if len(closes) < 5:
        return "Unknown"
    # measure 5-bar trend strength
    last5 = closes[-5:]
    diffs = [last5[i+1] - last5[i] for i in range(4)]
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    if pos >= 3:
        return "Impulse (up)"
    if neg >= 3:
        return "Impulse (down)"
    return "Correction/Range"

# ---------- MAIN SCAN ----------
def scan():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    CHAT_ID   = os.environ.get("CHAT_ID")

    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

    bot = Bot(token=BOT_TOKEN)

    results = []
    now = utcnow()
    zone = session_zone_for_utc(now)

    for name, ticker in SYMBOLS.items():
        try:
            # fetch recent 1m candles
            df = yf.download(tickers=ticker, period=PERIOD, interval=INTERVAL, progress=False, threads=False)
            if df is None or df.empty:
                results.append((name, "NO_DATA", None))
                continue

            # ensure columns exist
            if not {"Open", "High", "Low", "Close"}.issubset(df.columns):
                results.append((name, "NO_COLUMNS", None))
                continue

            # convert to lists
            opens = df["Open"].tolist()
            highs = df["High"].tolist()
            lows = df["Low"].tolist()
            closes = df["Close"].tolist()

            # basic impulse check
            impulse = is_impulse(closes, opens, highs, lows)
            wave = wave_label(closes)

            last_close = closes[-1]
            prev_close = closes[-2] if len(closes) >= 2 else last_close
            move_pct = pct(last_close, prev_close)

            # prepare tag for silver-bullet session zone (which session the scan ran in)
            sb_zone = zone

            # compose human-friendly line
            if impulse:
                direction = "BUY" if "Up" in impulse else "SELL"
                summary = f"{direction} — {impulse.split()[0]} | Wave:{wave} | {sb_zone} | {last_close:.5f} ({move_pct*100:.2f}%)"
            else:
                summary = f"No clear impulse | Wave:{wave} | {sb_zone} | {last_close:.5f} ({move_pct*100:.2f}%)"

            results.append((name, "OK", summary))
        except Exception as e:
            results.append((name, "ERROR", str(e)))
            continue

    # Build consolidated message
    ts_local = now.astimezone(pytz.timezone("Africa/Johannesburg"))  # SAST example
    header = f"Impulse scan — {ts_local.strftime('%Y-%m-%d %H:%M %Z')} ({zone} zone)\n"
    lines = [header]
    for name, status, msg in results:
        if status == "OK":
            lines.append(f"{name}: {msg}")
        elif status == "NO_DATA":
            lines.append(f"{name}: no recent data")
        elif status == "ERROR":
            lines.append(f"{name}: ERROR — {msg}")
        else:
            lines.append(f"{name}: {status}")

    body = "\n".join(lines)

    # send the consolidated message
    try:
        bot.send_message(chat_id=CHAT_ID, text=body)
        return True, body
    except Exception as e:
        # attach traceback for logs (not sent)
        traceback.print_exc()
        return False, str(e)

if __name__ == "__main__":
    ok, info = scan()
    if not ok:
        raise SystemExit(f"Send failed: {info}")
    else:
        print("Scan sent successfully.")
