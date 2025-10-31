import os
import datetime as dt
import pytz
import yfinance as yf
from telegram import Bot

# --- Configuration ---
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"
}
IMPULSE_THRESHOLD = 0.25  # 0.25% move considered an impulse
TZ = pytz.UTC

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

bot = Bot(token=BOT_TOKEN)

def detect_impulse(prev_close, last_close):
    if prev_close == 0:
        return ("NEUTRAL", "UNKNOWN", 0)
    pct = (last_close - prev_close) / prev_close * 100
    if pct > IMPULSE_THRESHOLD:
        return ("BUY", "IMPULSE", round(pct, 3))
    elif pct < -IMPULSE_THRESHOLD:
        return ("SELL", "IMPULSE", round(pct, 3))
    else:
        return ("NEUTRAL", "CORRECTION", round(pct, 3))

def silver_bullet_zone(now):
    hour = now.hour
    if 0 <= hour < 6:
        return "Asia"
    elif 6 <= hour < 12:
        return "London"
    else:
        return "New York"

def build_message(symbol, bias, wave, zone, price, pct, ts):
    return (
        f"{symbol}\n"
        f"Bias: {bias} | Wave: {wave} | Zone: {zone}\n"
        f"Price: {price:.5f} | Move: {pct}%\n"
        f"Time: {ts.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )

def main():
    now = dt.datetime.now(TZ)
    zone = silver_bullet_zone(now)
    for name, ticker in SYMBOLS.items():
        df = yf.download(ticker, period="15m", interval="1m", progress=False)
        if len(df) < 2:
            continue
        prev_close = df["Close"].iloc[-2]
        last_close = df["Close"].iloc[-1]
        bias, wave, pct = detect_impulse(prev_close, last_close)
        msg = build_message(name, bias, wave, zone, last_close, pct, now)
        bot.send_message(CHAT_ID, msg)
    bot.send_message(CHAT_ID, "✅ Scan completed successfully.")

if __name__ == "__main__":
    main()
