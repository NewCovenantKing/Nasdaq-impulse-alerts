import os
import smtplib
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Bot

# ========== CONFIG ==========
TICKERS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
}
TIMEFRAME = "5m"
PERIOD = "1d"

# ========== TELEGRAM ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ========== EMAIL ==========
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# ========== FUNCTIONS ==========

def fetch_data(symbol):
    """Download recent price data safely."""
    try:
        df = yf.download(symbol, period=PERIOD, interval=TIMEFRAME, progress=False)
        if df.empty:
            print(f"[WARN] No data for {symbol}")
            return None
        df.dropna(inplace=True)
        return df
    except Exception as e:
        print(f"[ERROR] fetch_data({symbol}) failed: {e}")
        return None


def detect_direction_and_wave(df):
    """Detect trend direction and wave type."""
    try:
        ema_fast = df["Close"].ewm(span=8, adjust=False).mean()
        ema_slow = df["Close"].ewm(span=21, adjust=False).mean()

        if len(ema_fast) < 3 or len(ema_slow) < 3:
            return "Neutral", "No impulse (insufficient data)"

        # ✅ FIX #1: Force scalar values
        last_fast_val = float(ema_fast.iloc[-1])
        prev_fast_val = float(ema_fast.iloc[-3] if len(ema_fast) >= 3 else ema_fast.iloc[-2])

        slope = (last_fast_val - prev_fast_val) / prev_fast_val if prev_fast_val != 0 else 0.0

        if last_fast_val > ema_slow.iloc[-1] and slope > 0:
            return "Buy", "Impulse"
        elif last_fast_val < ema_slow.iloc[-1] and slope < 0:
            return "Sell", "Impulse"
        else:
            return "Neutral", "Correction/No impulse"

    except Exception as e:
        print(f"[ERROR] detect_direction_and_wave failed: {e}")
        return "Error", "No signal"


def send_telegram_message(message):
    """Send message via Telegram bot."""
    if not BOT_TOKEN or not CHAT_ID:
        print("[WARN] Telegram not configured")
        return
    try:
        bot = Bot(token=BOT_TOKEN)
        bot.send_message(chat_id=CHAT_ID, text=message)
        print("[OK] Telegram message sent")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")


def send_email(subject, body):
    """Send email alert using Gmail SMTP."""
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not EMAIL_TO:
        print("[WARN] Email not configured")
        return
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)

        print("[OK] Email sent")
    except Exception as e:
        print(f"[ERROR] Email send failed: {e}")


def main():
    scan_zone = os.getenv("SCAN_ZONE", "London")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    results = []
    for name, symbol in TICKERS.items():
        print(f"Scanning {name}...")
        df = fetch_data(symbol)
        if df is None or df.empty:
            continue

        direction, wave = detect_direction_and_wave(df)
        last_price = df["Close"].iloc[-1]

        result = (
            f"{name} ({symbol})\n"
            f"Time: {timestamp}\n"
            f"Price: {last_price:.5f}\n"
            f"Direction: {direction}\n"
            f"Wave: {wave}\n"
            f"Zone: {scan_zone}\n"
            "-------------------------"
        )
        results.append(result)

    if not results:
        print("[INFO] No data available or all fetches failed.")
        return

    final_message = "\n".join(results)

    # ✅ Send via Telegram & Email
    send_telegram_message(final_message)
    send_email(
        subject=f"Impulse Scanner Alerts - {scan_zone}",
        body=final_message
    )


if __name__ == "__main__":
    main()
