#!/usr/bin/env python3
# impulse_scanner.py
# Sends simple "impulse" alerts to Telegram + email (Gmail) as backup.
# Requires: yfinance, python-telegram-bot, pandas
# Env vars (set in GitHub Actions secrets): BOT_TOKEN, CHAT_ID, EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO

import os
import datetime as dt
import yfinance as yf
import pandas as pd
from telegram import Bot
import smtplib
from email.message import EmailMessage
import traceback
import sys

# --- Config / symbols ---
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"   # example
}

# thresholds
IMPULSE_PCT_THRESHOLD = 0.004  # 0.4% change considered impulse (adjustable)

# --- Helpers ---
def now_utc():
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

def detect_zone(now_utc_dt):
    # Determine whether this run is London pre-open or NY pre-open, based on UTC hour
    h = now_utc_dt.hour
    # We scheduled runs at 06:30 UTC (London pre-open) and 13:00 UTC (NY pre-open)
    if h in (6, 7, 8):
        return "London"
    if h in (12, 13, 14):
        return "New York"
    return "Unknown"

def fetch_price(symbol, period="5d", interval="15m"):
    # Return last row (timestamp and close) or raise
    data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if data is None or data.empty:
        raise ValueError(f"No data found for {symbol}")
    # use the last available close
    last = data.iloc[-1]
    prev = data.iloc[-2] if len(data) >= 2 else None
    return {
        "symbol": symbol,
        "time": last.name,
        "close": float(last["Close"]),
        "prev_close": float(prev["Close"]) if prev is not None else None,
        "raw": last
    }

def simple_direction_and_wave(price, prev_close):
    if prev_close is None:
        return "Neutral", "No impulse (no history)"
    change = (price - prev_close) / prev_close
    if change > IMPULSE_PCT_THRESHOLD:
        return "Buy", "Impulse"
    if change < -IMPULSE_PCT_THRESHOLD:
        return "Sell", "Impulse"
    # otherwise small move -> correction/neutral
    return "Neutral", "Correction/No impulse"

def basic_tp_sl(price):
    # small example: TP = 0.5% away, SL = 0.5% away (example)
    tp = price * (1 + 0.005)
    sl = price * (1 - 0.005)
    return round(tp, 6), round(sl, 6)

def send_telegram(bot_token, chat_id, text):
    bot = Bot(token=bot_token)
    bot.send_message(chat_id=chat_id, text=text)

def send_email(smtp_user, smtp_password, to_addr, subject, body):
    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    # Gmail SMTP TLS
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

# --- Main ---
def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    CHAT_ID = os.environ.get("CHAT_ID")
    EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    EMAIL_TO = os.environ.get("EMAIL_TO")

    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID environment variables", file=sys.stderr)
        raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

    # CHAT_ID should be numeric string (no <> and no spaces)
    chat_id = CHAT_ID.strip()

    # Determine zone
    now = now_utc()
    zone = detect_zone(now)

    results = []
    errors = []

    for name, sym in SYMBOLS.items():
        try:
            info = fetch_price(sym)
            price = info["close"]
            prev = info["prev_close"]
            direction, wave = simple_direction_and_wave(price, prev)
            tp, sl = basic_tp_sl(price)
            results.append({
                "name": name,
                "yf_ticker": sym,
                "time": info["time"],
                "price": price,
                "direction": direction,
                "wave": wave,
                "tp": tp,
                "sl": sl
            })
        except Exception as e:
            errors.append(f"{name} ({sym}) error: {e}")
            # continue with next symbol
            continue

    # Build message text
    lines = []
    header = f"Impulse Scanner Report\nRun time (UTC): {now.isoformat()}  Zone: {zone}\n"
    lines.append(header)

    if results:
        for r in results:
            lines.append(f"{r['name']} ({r['yf_ticker']})")
            lines.append(f"Time: {r['time']}")
            lines.append(f"Price: {r['price']}")
            lines.append(f"Direction: {r['direction']}")
            lines.append(f"Wave: {r['wave']}")
            lines.append(f"Zone: {zone}")
            lines.append(f"TP: {r['tp']}  SL: {r['sl']}")
            lines.append("")  # blank
    else:
        lines.append("No results (all failed).")

    if errors:
        lines.append("Errors:")
        lines.extend(errors)

    message_text = "\n".join(lines)

    # Send Telegram
    try:
        send_telegram(BOT_TOKEN, chat_id, message_text)
        print("Telegram message sent")
    except Exception as e:
        print("Telegram send failed:", e, file=sys.stderr)
        errors.append(f"Telegram send failed: {e}")

    # Send email backup if email secrets provided
    if EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_TO:
        try:
            send_email(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO, "Impulse Scanner Report", message_text)
            print("Email sent")
        except Exception as e:
            print("Email send failed:", e, file=sys.stderr)
            errors.append(f"Email send failed: {e}")

    # If there were errors, raise non-zero for GitHub Actions visibility
    if errors:
        raise SystemExit("Completed with errors: " + "; ".join(errors))
    else:
        print("Completed OK")

if __name__ == "__main__":
    try:
        main()
    except SystemExit as se:
        print("Exit:", se, file=sys.stderr)
        raise
    except Exception:
        traceback.print_exc()
        raise
