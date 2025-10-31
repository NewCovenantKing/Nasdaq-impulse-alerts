# impulse_scanner.py
# Single-file scanner: yfinance -> simple direction/wave detection -> Telegram + Email
# Requirements: yfinance, python-telegram-bot
# This script is intentionally conservative/simple (easy to reason about)

import os
import sys
import time
import math
import smtplib
from email.message import EmailMessage
import traceback

import pandas as pd
import numpy as np
import yfinance as yf
from telegram import Bot

# ---------------- CONFIG ----------------
SYMBOLS = {
    # user-friendly key : yahoo ticker(s) to try in order
    "NAS100": ["^NDX", "NQ=F", "NDX"],         # try several options
    "EURUSD": ["EURUSD=X"],
    "GBPJPY": ["GBPJPY=X"],
    "USDJPY": ["JPY=X", "USDJPY=X"],
    "SPX": ["^GSPC", "SPY"],
    # add or remove tickers here
}

# how many minutes per candle expected by workflow (we use 15m)
INTERVAL = "15m"
PERIOD = "5d"  # how much history to download (5 days covers many sessions)

# detection thresholds (tune later if needed)
EMA_PERIOD = 8        # short EMA for slope
IMPULSE_BARS = 3      # number of consecutive trend bars to call "impulse"
TP_ATR_MULT = 2.0     # take-profit = price +/- TP_ATR_MULT * ATR
SL_ATR_MULT = 1.0     # stop-loss = price +/- SL_ATR_MULT * ATR

# ---------------- HELPERS ----------------
def safe_download(ticker):
    """Try to download a ticker and return DataFrame or None."""
    try:
        # auto_adjust True will normalize splits/dividends
        df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None

def compute_atr(df, n=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(n, min_periods=1).mean()
    return atr

def detect_direction_and_wave(df):
    """
    Simple logic:
      - compute EMA(period=EMA_PERIOD) and slope of EMA over last 3 points
      - direction = Buy if price > EMA and EMA slope > 0.0001, Sell if opposite
      - wave = Impulse if last IMPULSE_BARS candles all move in same direction (higher highs/lows or lower lows/highs)
    """
    close = df['Close']
    if close.empty:
        return "Neutral", "No data"

    ema = close.ewm(span=EMA_PERIOD, adjust=False).mean()
    if len(ema) < 3:
        return "Neutral", "No data"

    slope = (ema.iloc[-1] - ema.iloc[-3]) / ema.iloc[-3] if ema.iloc[-3] != 0 else 0.0

    last_price = close.iloc[-1]
    direction = "Neutral"
    if last_price > ema.iloc[-1] and slope > 0.0002:
        direction = "Buy"
    elif last_price < ema.iloc[-1] and slope < -0.0002:
        direction = "Sell"
    else:
        direction = "Neutral"

    # impulse detection
    impulse = False
    if len(df) >= IMPULSE_BARS:
        # examine last IMPULSE_BARS candles
        recent = df.tail(IMPULSE_BARS)
        increases = (recent['Close'].diff() > 0).sum()
        decreases = (recent['Close'].diff() < 0).sum()
        if increases == IMPULSE_BARS - 1 and direction == "Buy":
            impulse = True
        elif decreases == IMPULSE_BARS - 1 and direction == "Sell":
            impulse = True

    wave = "Impulse" if impulse else "Correction/No impulse"
    return direction, wave

def compute_tp_sl(df, direction):
    """Compute ATR-based TP/SL (returns tuple (TP, SL))"""
    if df is None or df.empty:
        return None, None
    atr = compute_atr(df)
    last_atr = atr.iloc[-1] if not atr.empty else None
    last_price = df['Close'].iloc[-1]
    if last_atr is None or math.isnan(last_atr) or last_atr == 0:
        return None, None
    if direction == "Buy":
        tp = last_price + TP_ATR_MULT * last_atr
        sl = last_price - SL_ATR_MULT * last_atr
    elif direction == "Sell":
        tp = last_price - TP_ATR_MULT * last_atr
        sl = last_price + SL_ATR_MULT * last_atr
    else:
        tp, sl = None, None
    return tp, sl

def format_price(p):
    return f"{p:.6f}" if isinstance(p, float) else str(p)

# ---------------- OUTPUT (Telegram + Email) ----------------
def send_telegram(bot_token, chat_id, text):
    try:
        bot = Bot(token=bot_token)
        # split if huge (Telegram has message size limits) — but try once
        bot.send_message(chat_id=chat_id, text=text)
        return True, None
    except Exception as e:
        return False, str(e)

def send_email(smtp_user, smtp_pass, to_addr, subject, body):
    try:
        msg = EmailMessage()
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)

        # Gmail SSL on 465
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

# ---------------- MAIN ----------------
def main():
    # env
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    CHAT_ID = os.environ.get("CHAT_ID")
    EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    EMAIL_TO = os.environ.get("EMAIL_TO")

    if not BOT_TOKEN:
        print("Warning: BOT_TOKEN not set. Telegram messages will not be sent.")
    if not CHAT_ID:
        print("Warning: CHAT_ID not set. Telegram messages will not be sent.")
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not EMAIL_TO:
        print("Warning: Email settings incomplete. Email will not be sent.")

    out_lines = []
    out_lines.append("Impulse Scanner Report\n")
    failed = []
    for key, try_list in SYMBOLS.items():
        df = None
        ticker_used = None
        for t in try_list:
            df = safe_download(t)
            if df is not None and not df.empty:
                ticker_used = t
                break
        if df is None or df.empty:
            failed.append(key)
            out_lines.append(f"{key}: FAILED to find price data for {try_list}\n")
            continue

        # compute
        direction, wave = detect_direction_and_wave(df)
        tp, sl = compute_tp_sl(df, direction)
        last_time = df.index[-1].isoformat()
        last_price = df['Close'].iloc[-1]

        out_lines.append(f"{key} ({ticker_used})")
        out_lines.append(f"Time: {last_time} UTC")
        out_lines.append(f"Price: {format_price(last_price)}")
        out_lines.append(f"Direction: {direction}")
        out_lines.append(f"Wave: {wave}")
        if tp is not None and sl is not None:
            out_lines.append(f"TP: {format_price(tp)}  SL: {format_price(sl)}")
        out_lines.append("")  # blank line

    summary = "\n".join(out_lines)

    # send telegram
    tel_ok = False
    tel_err = None
    if BOT_TOKEN and CHAT_ID:
        try:
            tel_ok, tel_err = send_telegram(BOT_TOKEN, CHAT_ID, summary)
        except Exception as e:
            tel_ok = False
            tel_err = str(e)

    # send email fallback/backup
    mail_ok = False
    mail_err = None
    if EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_TO:
        subject = "Impulse Scanner Report"
        try:
            mail_ok, mail_err = send_email(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO, subject, summary)
        except Exception as e:
            mail_ok = False
            mail_err = str(e)

    # final log
    final = [
        "=== Delivery ===",
        f"Telegram sent: {tel_ok} (err: {tel_err})",
        f"Email sent: {mail_ok} (err: {mail_err})",
        "",
        "=== Details ===",
        summary
    ]
    final_msg = "\n".join(final)
    print(final_msg)

    # if telegram failed but email succeeded, optionally print that to console (Actions will show)
    if not tel_ok and not mail_ok:
        # exit non-zero so Actions shows failure
        print("Both Telegram and Email failed to deliver. See logs above.")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
