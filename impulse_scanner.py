# impulse_scanner.py
# Updated: adds SB zone, ATR-based TP/SL, and clearer message format.

import os
import sys
import datetime as dt
import pytz
import yfinance as yf
import pandas as pd
from telegram import Bot

# optional email
import smtplib
from email.message import EmailMessage

# -------------------------
# Config
# -------------------------
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F",
}

INTERVAL = "15m"
PERIOD = "5d"
IMPULSE_LOOKBACK = 3
IMPULSE_THRESHOLD = 0.0005
ATR_PERIOD = 14   # ATR periods (on 15m)
TP_MULTIPLIER = 1.5
SL_MULTIPLIER = 1.0

# -------------------------
# Helpers
# -------------------------
def now_utc():
    return dt.datetime.now(pytz.utc)

def session_zone_for_time(ts_utc):
    hour = ts_utc.hour
    # coarse zones (adjust later if wanted)
    if 0 <= hour < 7:
        return "Asia"
    if 7 <= hour < 14:
        return "London"
    return "NY"

def fetch_symbol(symbol, period=PERIOD, interval=INTERVAL):
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError("No data returned")
    return df

def simple_impulse_check(df):
    if df.shape[0] < IMPULSE_LOOKBACK + 1:
        return "NO_DATA"
    closes = df['Close'].dropna().values
    last = closes[-(IMPULSE_LOOKBACK+1):]
    start, end = last[0], last[-1]
    if start == 0:
        return "NO_DATA"
    rel_move = (end - start) / abs(start)
    increasing = all(last[i] < last[i+1] for i in range(len(last)-1))
    decreasing = all(last[i] > last[i+1] for i in range(len(last)-1))
    if increasing and rel_move > IMPULSE_THRESHOLD:
        return "IMPULSE_UP"
    if decreasing and rel_move < -IMPULSE_THRESHOLD:
        return "IMPULSE_DOWN"
    return "CORRECTION"

def compute_atr(df, period=ATR_PERIOD):
    h = df['High']
    l = df['Low']
    c = df['Close']
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    if atr.dropna().empty:
        return None
    return atr.iloc[-1]

def build_message(key, symbol, df, result, zone):
    last_close = df['Close'].dropna().iloc[-1]
    last_time = df.index[-1].strftime("%Y-%m-%d %H:%M UTC")
    direction = "Buy" if result == "IMPULSE_UP" else ("Sell" if result == "IMPULSE_DOWN" else "Neutral")
    wave = "Impulse" if result.startswith("IMPULSE") else "Correction/No impulse"
    atr = compute_atr(df)
    if atr is not None and atr > 0:
        if direction == "Buy":
            sl = last_close - SL_MULTIPLIER * atr
            tp = last_close + TP_MULTIPLIER * atr
        elif direction == "Sell":
            sl = last_close + SL_MULTIPLIER * atr
            tp = last_close - TP_MULTIPLIER * atr
        else:
            sl = last_close - SL_MULTIPLIER * atr
            tp = last_close + TP_MULTIPLIER * atr
        sl_str = f"{sl:.5f}" if isinstance(sl, float) else str(sl)
        tp_str = f"{tp:.5f}" if isinstance(tp, float) else str(tp)
        atr_str = f"{atr:.6f}"
    else:
        sl_str = "n/a"
        tp_str = "n/a"
        atr_str = "n/a"

    sb_zone = zone

    text = (
        f"{key} ({symbol})\n"
        f"Time: {last_time}\n"
        f"Price: {symbol}  {last_close}\n"
        f"Direction: {direction}\n"
        f"Wave: {wave}\n"
        f"SB Zone: {sb_zone}\n"
        f"ATR({ATR_PERIOD}): {atr_str}\n"
        f"Suggested SL: {sl_str}\n"
        f"Suggested TP: {tp_str}\n"
    )
    return text

# -------------------------
# Sending helpers
# -------------------------
def send_telegram(bot_token, chat_id, text):
    try:
        bot = Bot(token=bot_token)
        bot.send_message(chat_id=chat_id, text=text)
        print("TELEGRAM: SENT")
        return True, "OK"
    except Exception as e:
        print("TELEGRAM: FAILED", str(e))
        return False, str(e)

def send_email(from_addr, app_password, to_addr, subject, body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(from_addr, app_password)
            smtp.send_message(msg)
        print("EMAIL: SENT")
        return True, "OK"
    except Exception as e:
        print("EMAIL: FAILED", str(e))
        return False, str(e)

# -------------------------
# Main
# -------------------------
def main():
    start = now_utc()
    print("Scanner start:", start.isoformat())

    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    CHAT_ID   = os.environ.get("CHAT_ID")
    EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
    EMAIL_TO = os.environ.get("EMAIL_TO")

    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID environment variables - telegram will be skipped")
    results = []

    for key, sym in SYMBOLS.items():
        try:
            df = fetch_symbol(sym)
        except Exception as e:
            print(f"Failed to download {key}/{sym}: {e}")
            results.append((key, sym, None, "DATA_ERROR", str(e)))
            continue

        try:
            result = simple_impulse_check(df)
            zone = session_zone_for_time(start)
            message = build_message(key, sym, df, result, zone)
            results.append((key, sym, df, result, message))
            print(f"{key} -> {result}")
        except Exception as e:
            print(f"Processing failed for {key}: {e}")
            results.append((key, sym, None, "PROCESS_ERROR", str(e)))

    final_text = ""
    for item in results:
        key, sym, df, result, info = item
        if result in ("DATA_ERROR", "PROCESS_ERROR"):
            final_text += f"{key} ({sym}): {result} - {info}\n\n"
        else:
            final_text += info + "\n\n"

    if not final_text:
        final_text = "No results."

    telegram_ok = False
    telegram_msg = "SKIPPED"
    if BOT_TOKEN and CHAT_ID:
        ok, info = send_telegram(BOT_TOKEN, CHAT_ID, final_text)
        telegram_ok = ok
        telegram_msg = info
    else:
        print("TELEGRAM: SKIPPED (missing env)")

    email_ok = False
    email_msg = "SKIPPED"
    if EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_TO:
        subj = f"Impulse Scanner results {start.strftime('%Y-%m-%d %H:%M UTC')}"
        ok, info = send_email(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO, subj, final_text)
        email_ok = ok
        email_msg = info
    else:
        print("EMAIL: SKIPPED (missing env)")

    print(f"TELEGRAM: {telegram_ok} {telegram_msg}")
    print(f"EMAIL: {email_ok} {email_msg}")

    if not telegram_ok and not email_ok:
        had_results = any(item[3] not in ("DATA_ERROR","PROCESS_ERROR") for item in results)
        if had_results:
            print("No delivery method succeeded -> failing for visibility.")
            sys.exit(1)

if __name__ == "__main__":
    main()
