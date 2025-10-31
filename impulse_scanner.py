#!/usr/bin/env python3
import os
import sys
import time
import traceback
from datetime import datetime, timezone
import smtplib
from email.message import EmailMessage

import pandas as pd
import yfinance as yf
from telegram import Bot

# -------------------------
# Config: symbols to scan
# Replace or extend these with tickers yfinance understands.
SYMBOLS = [
    "^NDX",        # NAS100 / Nasdaq 100 - sometimes '^NDX' works
    "EURUSD=X",   # EURUSD
    "GBPJPY=X",   # GBPJPY
    # add more symbols here as needed
]

# Tuning for EMA windows
FAST_EMA = 5
SLOW_EMA = 20

# Simple TP / SL placeholders (percent)
DEFAULT_TP_PCT = 0.5   # 0.5% example
DEFAULT_SL_PCT = 0.5   # 0.5% example

# -------------------------
# Helpers: environment + messaging
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram not configured (BOT_TOKEN or CHAT_ID missing).")
        return False
    try:
        bot = Bot(token=BOT_TOKEN)
        bot.send_message(chat_id=CHAT_ID, text=text)
        print("Telegram message sent.")
        return True
    except Exception as e:
        print("Telegram send failed:", e)
        traceback.print_exc()
        return False

def send_email(subject: str, body: str):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not EMAIL_TO:
        print("Email not configured (EMAIL_ADDRESS/EMAIL_PASSWORD/EMAIL_TO missing).")
        return False
    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)

        # Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
        print("Email sent.")
        return True
    except Exception as e:
        print("Email send failed:", e)
        traceback.print_exc()
        return False

# -------------------------
# Data & detection
def fetch_recent(symbol: str, period="5d", interval="15m"):
    """Download recent OHLCV data with safety checks."""
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if data is None or data.empty:
            print(f"No data for {symbol}")
            return None
        # Ensure index is timezone-aware UTC if not already
        if data.index.tz is None:
            data.index = data.index.tz_localize(timezone.utc)
        return data
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        traceback.print_exc()
        return None

def compute_emas(df: pd.DataFrame, fast=FAST_EMA, slow=SLOW_EMA):
    df = df.copy()
    df["ema_fast"] = df["Close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=slow, adjust=False).mean()
    return df

def detect_direction_and_wave(df: pd.DataFrame):
    """
    Return (direction, wave) where direction is 'Buy'/'Sell'/'Neutral'
    and wave is 'Impulse'/'Correction'/'No impulse'
    This function is defensive about empty/short data.
    """
    if df is None or df.empty:
        return "Unknown", "No data"

    # need at least a few rows
    if len(df) < max(FAST_EMA, SLOW_EMA) // 2 + 2:
        return "Unknown", "Insufficient data"

    df = compute_emas(df)
    # take the last valid ema values
    last = df.iloc[-1]
    # Ensure numeric scalars
    try:
        ema_fast = float(last["ema_fast"])
        ema_slow = float(last["ema_slow"])
        price = float(last["Close"])
    except Exception as e:
        print("Error extracting numeric EMA/price:", e)
        return "Unknown", "Bad numeric"

    # Slope calculation: percent change of EMAs using two points if possible
    try:
        if len(df) >= 3:
            prev_fast = float(df["ema_fast"].iloc[-3])
            prev_slow = float(df["ema_slow"].iloc[-3])
        else:
            prev_fast = ema_fast
            prev_slow = ema_slow
    except Exception:
        prev_fast, prev_slow = ema_fast, ema_slow

    slope_fast = (ema_fast - prev_fast) / prev_fast if prev_fast != 0 else 0.0
    slope_slow = (ema_slow - prev_slow) / prev_slow if prev_slow != 0 else 0.0

    # Simple rules:
    # - If both EMAs rising and fast > slow -> Buy + possibly Impulse
    # - If both EMAs falling and fast < slow -> Sell + possibly Impulse
    # - Else Neutral / Correction
    direction = "Neutral"
    wave = "No impulse"

    if slope_fast > 0 and slope_slow > 0 and ema_fast > ema_slow:
        direction = "Buy"
        # magnitude threshold to call impulse
        if slope_fast > 0.001:  # tune this threshold
            wave = "Impulse"
        else:
            wave = "Correction/weak"
    elif slope_fast < 0 and slope_slow < 0 and ema_fast < ema_slow:
        direction = "Sell"
        if slope_fast < -0.001:
            wave = "Impulse"
        else:
            wave = "Correction/weak"
    else:
        direction = "Neutral"
        wave = "Correction/No impulse"

    return direction, wave

def zone_from_time(ts_utc):
    # ts_utc: a timezone-aware UTC timestamp
    # Define London pre-open at 08:30 SAST (SAST = UTC+2), but user asked for runs at 08:30 SAST which is 06:30 UTC
    # We'll label timezones simplistically:
    # If between 06:00-09:30 UTC -> London pre-open region
    # If between 12:00-15:00 UTC -> NY pre-open region
    hour = ts_utc.hour
    if 5 <= hour <= 9:
        return "London"
    elif 11 <= hour <= 14:
        return "New York"
    else:
        return "Other"

def build_message(symbol, df, direction, wave):
    if df is None or df.empty:
        time_str = "No data"
        price_str = "N/A"
    else:
        last = df.iloc[-1]
        ts = last.name
        # convert to UTC string
        try:
            ts_utc = pd.Timestamp(ts).tz_convert(timezone.utc)
        except Exception:
            ts_utc = pd.Timestamp(ts).tz_localize(timezone.utc)
        time_str = ts_utc.strftime("%Y-%m-%d %H:%M UTC")
        price_str = f"{last['Close']:.6f}" if pd.notnull(last["Close"]) else "N/A"

    # zone by last timestamp
    zone = "Unknown"
    try:
        if df is not None and not df.empty:
            last_ts = df.index[-1]
            if getattr(last_ts, "tz", None) is None:
                last_ts = pd.Timestamp(last_ts).tz_localize(timezone.utc)
            zone = zone_from_time(last_ts.tz_convert(timezone.utc))
    except Exception:
        zone = "Unknown"

    # Basic TP/SL placeholders (absolute numbers using percent)
    tp = sl = ""
    try:
        if df is not None and not df.empty and pd.notnull(df.iloc[-1]["Close"]):
            px = float(df.iloc[-1]["Close"])
            tp = px * (1 + DEFAULT_TP_PCT / 100.0) if direction == "Buy" else px * (1 - DEFAULT_TP_PCT / 100.0)
            sl = px * (1 - DEFAULT_SL_PCT / 100.0) if direction == "Buy" else px * (1 + DEFAULT_SL_PCT / 100.0)
            tp = f"{tp:.6f}"
            sl = f"{sl:.6f}"
    except Exception:
        tp = sl = "N/A"

    text = (
        f"{symbol}\n"
        f"Time: {time_str}\n"
        f"Price: {price_str}\n"
        f"Direction: {direction}\n"
        f"Wave: {wave}\n"
        f"Zone: {zone}\n"
    )
    if tp and sl:
        text += f"TP: {tp}  SL: {sl}\n"

    return text

# -------------------------
# Main scanning routine
def scan_all():
    messages = []
    for s in SYMBOLS:
        print(f"Processing {s} ...")
        df = fetch_recent(s, period="5d", interval="15m")
        if df is None:
            msg = f"{s} - No price data available."
            print(msg)
            messages.append(msg)
            continue

        direction, wave = detect_direction_and_wave(df)
        text = build_message(s, df, direction, wave)
        messages.append(text)
    return "\n\n".join(messages)

def main():
    print("Starting impulse scanner run at", datetime.utcnow().isoformat(), "UTC")
    try:
        report = scan_all()
        if not report:
            report = "No results."

        # send to Telegram
        t_ok = send_telegram(report)

        # send email as backup
        subj = f"Impulse Scanner Report: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
        e_ok = send_email(subj, report)

        print("Summary: telegram_sent=", t_ok, " email_sent=", e_ok)
    except Exception as e:
        print("Unhandled error in main():", e)
        traceback.print_exc()
        # try to notify via email (best effort)
        try:
            send_email("Impulse scanner: run error", f"Error: {e}\n\nTrace:\n{traceback.format_exc()}")
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
