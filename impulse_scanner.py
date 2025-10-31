# impulse_scanner.py
import os
import sys
import datetime as dt
import pytz
import yfinance as yf
import pandas as pd
from telegram import Bot

# Optional email sending
import smtplib
from email.message import EmailMessage

# -------------------------
# Config: symbols and params
# -------------------------
SYMBOLS = {
    "NAS100": "^NDX",       # yfinance ticker for Nasdaq 100
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F",
}

INTERVAL = "15m"   # 15 minute bars
PERIOD = "5d"      # last 5 days of 15m bars
IMPULSE_LOOKBACK = 3  # lookback bars for simple impulse check
IMPULSE_THRESHOLD = 0.0005  # relative move threshold for small assets (adjust per-symbol if needed)

# -------------------------
# Helpers
# -------------------------
def now_utc():
    return dt.datetime.now(pytz.utc)

def session_zone_for_time(ts_utc):
    # Determine approximate session zone (Asia / London / NY) by UTC hour.
    # You may tweak these hour ranges to match your preferred "killzones".
    hour = ts_utc.hour
    if 0 <= hour < 7:
        return "Asia"
    if 7 <= hour < 14:
        return "London"
    return "NY"

def fetch_symbol(symbol, period=PERIOD, interval=INTERVAL):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise ValueError("No data returned")
        return df
    except Exception as e:
        raise

def simple_impulse_check(df):
    """
    Very simple heuristic:
    - Compare last IMPULSE_LOOKBACK closes.
    - If strictly increasing and % move > threshold => 'IMPULSE UP'
    - If strictly decreasing and % move < -threshold => 'IMPULSE DOWN'
    - Otherwise => 'CORRECTION/NO_IMPULSE'
    """
    if df.shape[0] < IMPULSE_LOOKBACK + 1:
        return "NO_DATA"

    closes = df['Close'].dropna().values
    last = closes[-(IMPULSE_LOOKBACK+1):]  # include a prior bar to compare slope
    # compute simple slope between earliest and latest in window
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

def build_message(symbol_key, symbol, df, result, zone):
    # Compose a compact but informative text message
    last_close = df['Close'].dropna().iloc[-1]
    last_time = df.index[-1].strftime("%Y-%m-%d %H:%M UTC")
    direction = "Buy" if result == "IMPULSE_UP" else ("Sell" if result == "IMPULSE_DOWN" else "Neutral")
    wave = "Impulse" if result.startswith("IMPULSE") else "Correction/No impulse"
    text = (
        f"{symbol_key} ({symbol})\n"
        f"Time: {last_time}\n"
        f"Price: {last_close}\n"
        f"Direction: {direction}\n"
        f"Wave: {wave}\n"
        f"Zone: {zone}\n"
    )
    return text

# -------------------------
# Sending functions
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

        # Gmail SMTP (SSL)
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
        print("Missing BOT_TOKEN or CHAT_ID environment variables - aborting")
        print("TELEGRAM: False MISSING_SECRETS")
        # continue to attempt email if configured
    # keep a list of results
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
            zone = session_zone_for_time(now_utc())
            message = build_message(key, sym, df, result, zone)
            results.append((key, sym, df, result, message))
            print(f"{key} -> {result}")
        except Exception as e:
            print(f"Processing failed for {key}: {e}")
            results.append((key, sym, None, "PROCESS_ERROR", str(e)))

    # Compose summary message
    summary_lines = []
    for item in results:
        key, sym, df, result, info = item
        if result in ("DATA_ERROR", "PROCESS_ERROR"):
            summary_lines.append(f"{key} ({sym}): {result} - {info}")
        else:
            # info is the message
            summary_lines.append(info)

    final_text = "\n\n".join(summary_lines) if summary_lines else "No results."

    # Send to Telegram if token & chat exist
    telegram_ok = False
    telegram_msg = "SKIPPED"
    if BOT_TOKEN and CHAT_ID:
        ok, info = send_telegram(BOT_TOKEN, CHAT_ID, final_text)
        telegram_ok = ok
        telegram_msg = info
    else:
        print("TELEGRAM: SKIPPED (missing env)")

    # Send email if configured
    email_ok = False
    email_msg = "SKIPPED"
    if EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_TO:
        subj = f"Impulse Scanner results {start.strftime('%Y-%m-%d %H:%M UTC')}"
        ok, info = send_email(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_TO, subj, final_text)
        email_ok = ok
        email_msg = info
    else:
        print("EMAIL: SKIPPED (missing env)")

    # Final printed status lines for Actions logs
    print(f"TELEGRAM: {telegram_ok} {telegram_msg}")
    print(f"EMAIL: {email_ok} {email_msg}")

    # exit non-zero if both sending methods failed and any data processed (so Action shows failure)
    if not telegram_ok and not email_ok:
        # If everything was skipped because no data attempted, allow success
        # but if we had results, return failure so you can inspect logs
        had_results = any(item[3] not in ("DATA_ERROR","PROCESS_ERROR") for item in results)
        if had_results:
            print("No delivery method succeeded -> failing for visibility.")
            sys.exit(1)

if __name__ == "__main__":
    main()
