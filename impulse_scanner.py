# impulse_scanner.py
# Simple NASDAQ/EURUSD/GBPJPY/GOLD impulse + 44% retrace scanner
# Reads BOT_TOKEN and CHAT_ID from environment (set as GitHub Secrets)

import os
import datetime
import pytz
import yfinance as yf
from telegram import Bot

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID in environment variables.")

SYMBOLS = {
    "NAS100":"^NDX",
    "EURUSD":"EURUSD=X",
    "GBPJPY":"GBPJPY=X",
    "GOLD":"GC=F"
}

IMPULSE_FACTOR = 1.6
LOOKBACK = "3d"
INTERVAL = "5m"
bot = Bot(BOT_TOKEN)

def fetch(sym):
    df = yf.download(tickers=sym, period=LOOKBACK, interval=INTERVAL, progress=False)
    if df is None or df.empty:
        return None
    return df.dropna()

def detect_impulse(df):
    bodies = (df['Close'] - df['Open']).abs()
    avg = bodies[-100:].mean() if len(bodies) > 50 else bodies.mean()
    last = bodies.iloc[-1]
    if last > IMPULSE_FACTOR * avg:
        high = float(df['High'].iloc[-1])
        low  = float(df['Low'].iloc[-1])
        direction = "bull" if df['Close'].iloc[-1] > df['Open'].iloc[-1] else "bear"
        return True, high, low, direction
    return False, None, None, None

def calc_44pct(high, low, direction):
    return (high - 0.44*(high-low)) if direction == "bear" else (low + 0.44*(high-low))

def build_msg(name, high, low, direction, pct44):
    t = datetime.datetime.now(pytz.timezone("Africa/Johannesburg")).strftime("%Y-%m-%d %H:%M SAST")
    target = low if direction == "bear" else high
    invalid = high if direction == "bear" else low
    return (f"{name} | {t}\n"
            f"Dir: {direction}\n"
            f"Range: {high:.2f} - {low:.2f}\n"
            f"44%: {pct44:.2f}\n"
            f"Target: {target:.2f}\n"
            f"Invalid: {invalid:.2f}")

def run_once():
    sent = 0
    for name, sym in SYMBOLS.items():
        df = fetch(sym)
        if df is None:
            continue
        ok, high, low, direction = detect_impulse(df)
        if not ok:
            continue
        pct44 = calc_44pct(high, low, direction)
        msg = build_msg(name, high, low, direction, pct44)
        bot.send_message(chat_id=CHAT_ID, text=msg)
        sent += 1
    return sent

if __name__ == "__main__":
    c = run_once()
    print(f"Messages sent: {c}")
