#!/usr/bin/env python3
"""
impulse_scanner.py
- Pulls live prices (yfinance)
- Detects simple impulses (last-5 candle trend)
- Sends summary to Telegram and as fallback via Gmail
- Designed for scheduled runs (GitHub Actions)
"""

import os
import sys
import datetime as dt
import pytz
import traceback
from email.message import EmailMessage
import smtplib

import yfinance as yf
from telegram import Bot

# ---------- USER / ENV ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")           # string or int
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")  # app password for Gmail
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))

if not (EMAIL_ADDRESS and EMAIL_PASSWORD):
    # Email is optional but recommended; if missing we still continue with Telegram if present
    EMAIL_ADDRESS = None
    EMAIL_PASSWORD = None

if not BOT_TOKEN or not CHAT_ID:
    # We'll still attempt email-only if Telegram creds missing; otherwise fail early
    if not (EMAIL_ADDRESS and EMAIL_PASSWORD):
        raise SystemExit("Missing BOT_TOKEN/CHAT_ID and no EMAIL credentials available. Add secrets.")

# ---------- SYMBOLS ----------
SYMBOLS = {
    "NAS100": "^NDX",        # Nasdaq 100 index
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"           # gold futures ticker; alternative: "XAUUSD=X"
}

# ---------- SETTINGS ----------
INTERVAL = "15m"     # 15-minute candles
PERIOD = "5d"        # last 5 days for context
CANDLES_TO_CHECK = 5

# ---------- UTILS ----------
def now_ts(tz="Africa/Johannesburg"):
    tzobj = pytz.timezone(tz)
    return dt.datetime.now(tzobj).strftime("%Y-%m-%d %H:%M:%S %Z")

def session_zone_from_utc(utc_dt=None):
    # simple session mapping (UTC)
    if utc_dt is None:
        utc_dt = dt.datetime.utcnow()
    hr = utc_dt.hour
    # Asia session ~ 00:00-07:00 UTC, London ~ 07:00-15:00 UTC, NY ~ 13:00-21:00 UTC
    if 0 <= hr < 7:
        return "Asia"
    if 7 <= hr < 13:
        return "London"
    return "NY"

def send_telegram(text):
    if not (BOT_TOKEN and CHAT_ID):
        return False, "Telegram creds not set"
    try:
        bot = Bot(token=BOT_TOKEN)
        bot.send_message(chat_id=int(CHAT_ID), text=text)
        return True, "sent"
    except Exception as e:
        return False, str(e)

def send_email(subject, body, to_address):
    if not (EMAIL_ADDRESS and EMAIL_PASSWORD):
        return False, "Email creds missing"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to_address
        msg.set_content(body)
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
        return True, "sent"
    except Exception as e:
        return False, str(e)

# ---------- PRICE + SIGNAL LOGIC ----------
def fetch_data(symbol, interval=INTERVAL, period=PERIOD):
    # Return DataFrame or None
    data = None
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if data is None or data.empty:
            return None, "no_data"
        return data, None
    except Exception as e:
        return None, str(e)

def is_impulse(df, candles=CANDLES_TO_CHECK):
    # Heuristic impulse: last N closes trending up or down AND highs/lows aligning
    if df is None or len(df) < candles:
        return None  # insufficient data
    tail = df.tail(candles)
    closes = tail["Close"].values
    highs = tail["High"].values
    lows = tail["Low"].values
    # monotonic increase (bull impulse)
    bull = all(closes[i] > closes[i-1] for i in range(1, len(closes)))
    bear = all(closes[i] < closes[i-1] for i in range(1, len(closes)))
    if bull:
        return {"wave": "Impulse", "direction": "Buy"}
    if bear:
        return {"wave": "Impulse", "direction": "Sell"}
    # small correction detection: last candle reversed vs prior trend
    # fallback: check if last 3 are mixed -> Correction
    return {"wave": "Correction", "direction": "Neutral"}

# ---------- MESSAGE FORMAT ----------
def format_alert(symbol_key, symbol, df, sig, session_zone):
    price = df["Close"].iloc[-1]
    time = df.index[-1].to_pydatetime().astimezone(pytz.timezone("Africa/Johannesburg")).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"{symbol_key} ({symbol})",
        f"Time: {time} SAST",
        f"Price: {price:.4f}" if price >= 1 else f"Price: {price:.6f}",
        f"Session: {session_zone}",
        f"Wave: {sig.get('wave')}",
        f"Direction: {sig.get('direction')}",
        "",
        "Playbook:",
        "- If Impulse & Buy -> look for long setups into pullbacks",
        "- If Impulse & Sell -> look for shorts into rallies",
        "- If Correction -> wait for impulse confirmation",
    ]
    return "\n".join(lines)

# ---------- MAIN SCAN ----------
def run_scan():
    utc_now = dt.datetime.utcnow()
    session_zone = session_zone_from_utc(utc_now)
    results = []
    errors = []
    for key, sym in SYMBOLS.items():
        try:
            df, err = fetch_data(sym)
            if err:
                errors.append(f"{key}: fetch error -> {err}")
                continue
            sig = is_impulse(df)
            if sig is None:
                errors.append(f"{key}: insufficient data")
                continue
            msg = format_alert(key, sym, df, sig, session_zone)
            results.append({"symbol_key": key, "message": msg, "sig": sig})
        except Exception as e:
            tb = traceback.format_exc()
            errors.append(f"{key}: exception -> {e}\n{tb}")
    return results, errors

# ---------- RUN & SEND ----------
def main():
    run_time = now_ts()
    results, errors = run_scan()

    header = f"Impulse Scanner report — {run_time}\n"
    if not results and not errors:
        body = header + "\nNo symbols scanned."
    else:
        body_parts = [header]
        for r in results:
            body_parts.append(r["message"])
            body_parts.append("-" * 30)
        if errors:
            body_parts.append("Errors / notes:")
            body_parts.extend(errors)
        body = "\n".join(body_parts)

    # Try Telegram first
    tele_ok, tele_msg = send_telegram(body)
    email_ok, email_msg = (False, "not attempted")
    # If telegram failed or email creds present, send email as backup
    if EMAIL_ADDRESS and EMAIL_PASSWORD:
        # send to the same EMAIL_ADDRESS (you) as backup
        email_ok, email_msg = send_email("Impulse Scanner Report", body, EMAIL_ADDRESS)

    # print short result for logs (GitHub Actions)
    print("TELEGRAM:", tele_ok, tele_msg)
    print("EMAIL:", email_ok, email_msg)
    if errors:
        print("SCAN ERRORS:")
        for e in errors:
            print("-", e)

    # Exit codes: 0 OK (sent at least one), 1 otherwise (so Action flags failure)
    if tele_ok or email_ok:
        print("Alert delivered.")
        sys.exit(0)
    else:
        print("No delivery method succeeded.")
        sys.exit(1)

if __name__ == "__main__":
    main()
