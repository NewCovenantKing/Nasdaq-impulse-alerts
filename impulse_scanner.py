#!/usr/bin/env python3
"""
Complete robust impulse_scanner.py
- Defensive pandas handling to avoid Series-boolean ambiguity
- Telegram + email (Gmail app-password) notifications (both optional)
- EMA-based simple direction + impulse detection
- Silver-bullet zone inference by UTC hour
- Safe handling of missing/delisted tickers
"""

import os
import sys
import traceback
from datetime import datetime, timezone
import smtplib
from email.message import EmailMessage

import pandas as pd
import yfinance as yf
from telegram import Bot

# -------------------------
# USER CONFIG
SYMBOLS = [
    "^NDX",        # Nasdaq100 (Yahoo)
    "EURUSD=X",
    "GBPJPY=X",
    # add more symbols here if you want
]

FAST_EMA = 5
SLOW_EMA = 20

DEFAULT_TP_PCT = 0.5
DEFAULT_SL_PCT = 0.5

# -------------------------
# ENV / SECRETS (from GitHub actions env block)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

# -------------------------
# Messaging helpers
def send_telegram(text: str) -> bool:
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

def send_email(subject: str, body: str) -> bool:
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not EMAIL_TO:
        print("Email not configured (EMAIL_ADDRESS/EMAIL_PASSWORD/EMAIL_TO missing).")
        return False
    try:
        msg = EmailMessage()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
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
# Data fetch + utilities
def fetch_recent(symbol: str, period="5d", interval="15m"):
    """Download recent OHLC data. Returns DataFrame or None."""
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if data is None or data.empty:
            print(f"No data for {symbol}")
            return None
        # Ensure timezone aware index (UTC)
        if data.index.tz is None:
            try:
                data.index = data.index.tz_localize(timezone.utc)
            except Exception:
                # ignore localization error
                pass
        return data
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        traceback.print_exc()
        return None

def compute_emas(df: pd.DataFrame, fast=FAST_EMA, slow=SLOW_EMA) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["Close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=slow, adjust=False).mean()
    return df

# Safe scalar extractor
def scalar_from_series_or_value(v):
    """Return a scalar value if v is scalar, if it's a Series return last element, else None."""
    try:
        if isinstance(v, pd.Series):
            if v.empty:
                return None
            return v.iloc[-1]
        # If it's numpy array-like
        if hasattr(v, "__len__") and not isinstance(v, (str, bytes)) and not pd.api.types.is_scalar(v):
            # convert to list and return last, if possible
            try:
                return list(v)[-1]
            except Exception:
                return None
        return v
    except Exception:
        return None

# -------------------------
# Detection logic
def detect_direction_and_wave(df: pd.DataFrame):
    """
    Returns (direction, wave) safely. Handles edge cases gracefully.
    direction: 'Buy' / 'Sell' / 'Neutral' / 'Unknown'
    wave: 'Impulse' / 'Correction' / 'No impulse' / 'Insufficient data' / 'No data'
    """
    try:
        if df is None or df.empty:
            return "Unknown", "No data"

        # Need at least a few rows to compute EMAs
        if len(df) < 3:
            return "Unknown", "Insufficient data"

        df2 = compute_emas(df)
        # Get last row
        last = df2.iloc[-1]
        # Pull scalars with safety
        ema_fast = scalar_from_series_or_value(last.get("ema_fast"))
        ema_slow = scalar_from_series_or_value(last.get("ema_slow"))
        price = scalar_from_series_or_value(last.get("Close"))

        if ema_fast is None or ema_slow is None:
            return "Unknown", "Insufficient EMA"

        # Get previous values for slope
        try:
            prev_fast = scalar_from_series_or_value(df2["ema_fast"].iloc[-3])
            prev_slow = scalar_from_series_or_value(df2["ema_slow"].iloc[-3])
        except Exception:
            prev_fast = ema_fast
            prev_slow = ema_slow

        # Ensure numeric
        try:
            ema_fast = float(ema_fast)
            ema_slow = float(ema_slow)
        except Exception:
            return "Unknown", "Bad numeric"

        prev_fast = float(prev_fast) if prev_fast is not None else ema_fast
        prev_slow = float(prev_slow) if prev_slow is not None else ema_slow

        # calculate slopes defensively
        slope_fast = (ema_fast - prev_fast) / prev_fast if prev_fast != 0 else 0.0
        slope_slow = (ema_slow - prev_slow) / prev_slow if prev_slow != 0 else 0.0

        direction = "Neutral"
        wave = "No impulse"

        if slope_fast > 0 and slope_slow > 0 and ema_fast > ema_slow:
            direction = "Buy"
            wave = "Impulse" if slope_fast > 0.001 else "Correction/weak"
        elif slope_fast < 0 and slope_slow < 0 and ema_fast < ema_slow:
            direction = "Sell"
            wave = "Impulse" if slope_fast < -0.001 else "Correction/weak"
        else:
            direction = "Neutral"
            wave = "Correction/No impulse"

        return direction, wave
    except Exception as e:
        print("Error in detect_direction_and_wave:", e)
        traceback.print_exc()
        return "Unknown", "Error"

def zone_from_time(ts):
    """Given a pandas Timestamp or datetime (preferably timezone-aware), return 'London'/'New York'/'Other'"""
    try:
        if ts is None:
            return "Unknown"
        # attempt to coerce to pandas Timestamp and tz to UTC
        t = pd.Timestamp(ts)
        if t.tz is None:
            t = t.tz_localize(timezone.utc)
        t = t.tz_convert(timezone.utc)
        hour = t.hour
        if 5 <= hour <= 9:
            return "London"
        elif 11 <= hour <= 14:
            return "New York"
        else:
            return "Other"
    except Exception:
        return "Unknown"

def format_price_safe(val):
    try:
        v = scalar_from_series_or_value(val)
        if v is None or pd.isna(v):
            return "N/A"
        return f"{float(v):.6f}"
    except Exception:
        return "N/A"

def build_message(symbol, df, direction, wave):
    """Create readable message text for one symbol"""
    try:
        if df is None or df.empty:
            time_str = "No data"
            price_str = "N/A"
            zone = "Unknown"
        else:
            last = df.iloc[-1]
            # get timestamp
            idx = df.index[-1]
            try:
                ts = pd.Timestamp(idx)
                if ts.tz is None:
                    ts = ts.tz_localize(timezone.utc)
                ts_utc = ts.tz_convert(timezone.utc)
                time_str = ts_utc.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                time_str = str(idx)

            price_str = format_price_safe(last.get("Close", None))
            zone = zone_from_time(idx)

        # TP/SL if numeric
        tp = sl = ""
        try:
            if df is not None and not df.empty:
                last_close = scalar_from_series_or_value(df["Close"].iloc[-1])
                if last_close is not None and not pd.isna(last_close):
                    px = float(last_close)
                    if direction == "Buy":
                        tp = f"{px * (1 + DEFAULT_TP_PCT / 100):.6f}"
                        sl = f"{px * (1 - DEFAULT_SL_PCT / 100):.6f}"
                    elif direction == "Sell":
                        tp = f"{px * (1 - DEFAULT_TP_PCT / 100):.6f}"
                        sl = f"{px * (1 + DEFAULT_SL_PCT / 100):.6f}"
                    else:
                        tp = sl = ""
        except Exception:
            tp = sl = ""

        txt = (
            f"{symbol}\n"
            f"Time: {time_str}\n"
            f"Price: {price_str}\n"
            f"Direction: {direction}\n"
            f"Wave: {wave}\n"
            f"Zone: {zone}\n"
        )
        if tp and sl:
            txt += f"TP: {tp}  SL: {sl}\n"
        return txt
    except Exception as e:
        print("Error in build_message:", e)
        traceback.print_exc()
        return f"{symbol} - Error building message"

# -------------------------
# Scanning orchestration
def scan_all():
    parts = []
    for s in SYMBOLS:
        try:
            print("Processing", s)
            df = fetch_recent(s, period="5d", interval="15m")
            if df is None:
                parts.append(f"{s} - No data")
                continue
            direction, wave = detect_direction_and_wave(df)
            parts.append(build_message(s, df, direction, wave))
        except Exception as e:
            print(f"Error scanning {s}: {e}")
            traceback.print_exc()
            parts.append(f"{s} - Error")
    return "\n\n".join(parts)

def main():
    print("Starting impulse scanner run at", datetime.utcnow().isoformat(), "UTC")
    try:
        report = scan_all()
        if not report:
            report = "No results."

        # Try Telegram (best effort)
        t_ok = send_telegram(report)

        # Try email as backup (best effort)
        subj = f"Impulse Scanner Report {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
        e_ok = send_email(subj, report)

        print("Finished run. telegram_sent=", t_ok, "email_sent=", e_ok)
    except Exception as e:
        print("Unhandled error in main():", e)
        traceback.print_exc()
        try:
            send_email("Impulse scanner: run error", f"Error: {e}\n\nTrace:\n{traceback.format_exc()}")
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
