# impulse_scanner.py
# Completed, robust scanner:
# - downloads prices from yfinance (safe ticker map)
# - decides Direction (Buy/Sell/Neutral) and Wave (Impulse/Correction)
# - sends Telegram message
# - optionally sends email (Gmail app password)
# - resilient to missing tickers / empty data

import os
import sys
import traceback
from datetime import datetime, timezone
from email.message import EmailMessage
import smtplib

try:
    import yfinance as yf
    import pandas as pd
    from telegram import Bot
except Exception as e:
    print("Missing Python packages. Ensure workflow installs yfinance, python-telegram-bot and pandas.")
    raise

# -------- User configuration (edit here only if required) -----------
# Use YFINANCE symbol map here so we never request 'NAS100' (bad)
TICKER_MAP = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    # add more symbols if you like:
    # "SPX": "^GSPC",
    # "US30": "^DJI",
}

# EMA periods / thresholds - tuned to be conservative
EMA_FAST = 5
EMA_SLOW = 21
IMPULSE_SLOPE_THRESHOLD = 0.0005  # small number — adjust if too sensitive

# -------------------------------------------------------------------

def safe_download(symbol, period="5d", interval="15m"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        print(f"YFinance download failed for {symbol}: {e}")
        return None

def detect_direction_and_wave(df):
    # returns (direction, wave) strings
    # direction: "Buy" / "Sell" / "Neutral"
    # wave: "Impulse" / "Correction/No impulse"
    if df is None or df.empty:
        return "Neutral", "Correction/No impulse"

    close = df["Close"].dropna()
    if len(close) < EMA_SLOW + 1:
        return "Neutral", "Correction/No impulse"

    ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean()

    # compute recent slope (relative change)
    last_fast = ema_fast.iloc[-1]
    prev_fast = ema_fast.iloc[-3] if len(ema_fast) >= 3 else ema_fast.iloc[-2]
    slope = (last_fast - prev_fast) / prev_fast if prev_fast != 0 else 0.0

    direction = "Buy" if slope > 0 else ("Sell" if slope < 0 else "Neutral")
    wave = "Impulse" if abs(slope) >= IMPULSE_SLOPE_THRESHOLD else "Correction/No impulse"
    return direction, wave

def format_message(results, zone):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"Impulse Scanner report — {zone}\nTime: {now}\n\n"
    lines = [header]
    for item in results:
        lines.append(f"{item['friendly']} ({item['symbol']})")
        lines.append(f"Time: {item['time_utc']}")
        lines.append("Price: Ticker")
        lines.append(f"{item['symbol']}  {item['last_price']}")
        lines.append(f"Name: {item['name']}")
        lines.append(f"Direction: {item['direction']}")
        lines.append(f"Wave: {item['wave']}")
        lines.append(f"Zone: {zone}")
        lines.append("")  # blank line
    return "\n".join(lines)

def send_telegram(bot_token, chat_id, text):
    bot = Bot(token=bot_token)
    bot.send_message(chat_id=chat_id, text=text)

def send_email(smtp_user, smtp_password, to_address, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_address
    msg.set_content(body)
    # Using Gmail SMTP
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)
    server.login(smtp_user, smtp_password)
    server.send_message(msg)
    server.quit()

def main():
    # read environment
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    CHAT_ID = os.environ.get("CHAT_ID")
    EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")  # SMTP login (gmail)
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")  # app password or smtp password
    EMAIL_TO = os.environ.get("EMAIL_TO")
    SCAN_ZONE = os.environ.get("SCAN_ZONE", "London")  # optional override

    results = []
    for friendly, yf_symbol in TICKER_MAP.items():
        df = safe_download(yf_symbol, period="5d", interval="15m")
        if df is None:
            print(f"No price data for {yf_symbol}. Skipping.")
            continue

        # compute last price and build info
        last_row = df.iloc[-1]
        last_price = last_row["Close"]
        time_utc = last_row.name.strftime("%Y-%m-%d %H:%M UTC")

        direction, wave = detect_direction_and_wave(df)

        results.append({
            "friendly": friendly,
            "symbol": yf_symbol,
            "last_price": last_price,
            "time_utc": time_utc,
            "direction": direction,
            "wave": wave,
            "name": str(last_row.name)
        })

    # If nothing found, still send a short message (so you know it ran)
    if not results:
        body = f"Impulse scanner ran but no valid price data found (tickers: {list(TICKER_MAP.values())})."
    else:
        body = format_message(results, SCAN_ZONE)

    # Send to Telegram if configured
    try:
        if BOT_TOKEN and CHAT_ID:
            print("Sending Telegram message...")
            send_telegram(BOT_TOKEN, CHAT_ID, body)
        else:
            print("BOT_TOKEN or CHAT_ID not set; skipping Telegram.")
    except Exception:
        print("Telegram send failed:")
        traceback.print_exc()

    # Send by email if configured
    try:
        if EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_TO:
            print("Sending email...")
            send_email(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO,
                       "Impulse Scanner Alerts", body)
        else:
            print("Email credentials not set; skipping email.")
    except Exception:
        print("Email send failed:")
        traceback.print_exc()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Unhandled error in main():")
        traceback.print_exc()
        sys.exit(1)
