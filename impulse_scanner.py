# impulse_scanner.py
# Final consolidated scanner:
# - yfinance data (Yahoo)
# - simple impulse detection (15m)
# - simple Elliott label (Impulse / Correction)
# - Silver Bullet session zone (SAST-based)
# - Telegram alert + Email fallback
# Requires env secrets: BOT_TOKEN, CHAT_ID, EMAIL_ADDRESS, EMAIL_PASSWORD, SEND_EMAIL_TO

import os
import sys
import smtplib
import traceback
from email.mime.text import MIMEText
from datetime import datetime, time, timedelta
import pytz
import yfinance as yf

# ---------- CONFIG ----------
# Symbols mapping (yfinance)
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"  # futures gold - commonly available
}

# Interval we scan (15m)
INTERVAL = "15m"
PERIOD = "5d"  # last 5 days sufficient to analyze recent structure

# Minimal thresholds for impulse detection (tunable)
IMPULSE_WINDOW = 3   # number of consecutive directional candles considered an impulse
MIN_BODY_PCT = 0.002  # minimal body percent of price to avoid tiny candles (0.2%)

# Timezone for your schedule and session zones
SAST = pytz.timezone("Africa/Johannesburg")

# ---------- ENV / SECRETS ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SEND_EMAIL_TO = os.environ.get("SEND_EMAIL_TO") or EMAIL_ADDRESS

# Validate
if not (BOT_TOKEN and CHAT_ID and EMAIL_ADDRESS and EMAIL_PASSWORD and SEND_EMAIL_TO):
    missing = [k for k,v in [
        ("BOT_TOKEN", BOT_TOKEN),
        ("CHAT_ID", CHAT_ID),
        ("EMAIL_ADDRESS", EMAIL_ADDRESS),
        ("EMAIL_PASSWORD", EMAIL_PASSWORD),
        ("SEND_EMAIL_TO", SEND_EMAIL_TO)
    ] if not v]
    raise SystemExit(f"Missing environment secrets: {', '.join(missing)}")

# ---------- UTILITIES ----------
def now_sast():
    return datetime.now(SAST)

def session_zone(dt=None):
    # dt is SAST datetime
    if dt is None:
        dt = now_sast()
    h = dt.hour
    # Simple session mapping (SAST): adjust if you prefer different boundaries
    if 6 <= h < 11:
        return "London"
    if 15 <= h < 21:
        return "NewYork"
    return "Asia"

def send_telegram(text):
    import requests
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True, r.text
    except Exception as e:
        return False, str(e)

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = SEND_EMAIL_TO
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, [SEND_EMAIL_TO], msg.as_string())
        server.quit()
        return True, "ok"
    except Exception as e:
        return False, str(e)

def fetch_symbol_data(ticker, period=PERIOD, interval=INTERVAL):
    # returns dataframe or raises
    data = yf.download(ticker, period=period, interval=interval, progress=False, threads=False)
    return data

# ---------- SIMPLE PATTERN DETECTION ----------
def detect_impulse(df):
    """
    Very simple heuristic:
      - look at last IMPULSE_WINDOW candles
      - if they are all bullish (close>open) and bodies are reasonably large -> up impulse
      - or all bearish -> down impulse
      - else -> correction / neutral
    Returns: (label, direction, strength_info)
    """
    if df is None or len(df) < IMPULSE_WINDOW + 1:
        return "NoData", None, "not enough candles"

    recent = df.tail(IMPULSE_WINDOW)
    closes = recent["Close"].values
    opens = recent["Open"].values
    bodies = abs(closes - opens)
    avg_price = recent["Close"].mean() if recent["Close"].mean() else 1.0
    # Require each body > MIN_BODY_PCT * price
    body_thresh = MIN_BODY_PCT * avg_price

    bullish = all(closes[i] > opens[i] and bodies[i] > body_thresh for i in range(len(recent)))
    bearish = all(closes[i] < opens[i] and bodies[i] > body_thresh for i in range(len(recent)))

    # A small momentum measure: size of last body relative to avg
    body_pct = bodies[-1] / avg_price

    if bullish:
        return "Impulse", "UP", f"body_pct={body_pct:.4f}"
    if bearish:
        return "Impulse", "DOWN", f"body_pct={body_pct:.4f}"
    # fallback: look at last 5 closes slope
    slope = closes[-1] - closes[0]
    if abs(slope) / avg_price > 0.003:  # small trend
        direction = "UP" if slope > 0 else "DOWN"
        return "Correction", direction, f"slope={slope:.4f}"
    return "Correction", None, "no clear directional impulse"

# ---------- BUILD MESSAGE ----------
def build_message(symbol_key, ticker, df):
    now = now_sast()
    session = session_zone(now)
    time_str = now.strftime("%Y-%m-%d %H:%M SAST")
    if df is None or df.empty:
        return f"{symbol_key} ({ticker}) — No price data at {time_str} — session {session}"

    last = df.tail(1).iloc[0]
    price = last["Close"]
    label, direction, info = detect_impulse(df)
    # Simple Elliott-like note: if label == Impulse then wave = "Impulse (1)" else "Correction"
    wave = "Impulse" if label == "Impulse" else "Correction"
    bias = direction if direction else "Neutral"
    # Daily high/low approximate from last 24h (using dataframe)
    high = df["High"].max()
    low = df["Low"].min()

    msg = (
        f"{symbol_key} ({ticker})\n"
        f"Time: {time_str}\n"
        f"Price: {price:.5f}\n"
        f"Bias: {bias}\n"
        f"Wave: {wave}\n"
        f"SilverBulletZone: {session}\n"
        f"Range(H/L): {high:.5f} / {low:.5f}\n"
        f"Note: {info}"
    )
    return msg

# ---------- MAIN RUN ----------
def run_scan():
    results = []
    errors = []
    for skey, ticker in SYMBOLS.items():
        try:
            df = None
            # download
            df = fetch_symbol_data(ticker)
            if df is None or df.empty:
                raise ValueError(f"No data returned for {ticker}")
            message = build_message(skey, ticker, df)
            results.append((skey, ticker, message))
        except Exception as e:
            tb = traceback.format_exc()
            errors.append((skey, ticker, str(e), tb))

    # send consolidated telegram message and email
    if results:
        # Compose full text
        header = f"Impulse Scanner Report — {now_sast().strftime('%Y-%m-%d %H:%M SAST')}\n"
        body = header + "\n\n".join(r[2] for r in results)
        ok_tg, resp_tg = send_telegram(body)
        ok_em, resp_em = send_email("Impulse Scanner Report", body)
        return {"telegram_ok": ok_tg, "telegram_resp": resp_tg, "email_ok": ok_em, "email_resp": resp_em, "errors": errors}
    else:
        # nothing to send; report errors
        body = f"No symbols scanned successfully. Errors:\n\n" + "\n\n".join(f"{e[0]} {e[1]} -> {e[2]}" for e in errors)
        send_email("Impulse Scanner Errors", body)
        return {"telegram_ok": False, "telegram_resp": "no results", "email_ok": True, "email_resp": "error email sent", "errors": errors}

if __name__ == "__main__":
    try:
        out = run_scan()
        # Print outcome for GitHub Actions log
        print("Scan result:", out)
        # Exit 0 even if some per-symbol errors occurred
        sys.exit(0)
    except Exception as e:
        tb = traceback.format_exc()
        print("Fatal error:", str(e))
        print(tb)
        # attempt to email fatal
        try:
            send_email("Impulse Scanner Fatal Error", f"{e}\n\n{tb}")
        except Exception:
            pass
        sys.exit(1)
