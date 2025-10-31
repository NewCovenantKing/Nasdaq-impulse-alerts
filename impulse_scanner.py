# impulse_scanner.py
# Complete, standalone scanner:
# - uses yfinance to fetch prices
# - simple impulse heuristic + "Elliott-like" label
# - marks session (Asia / London / NY)
# - sends result to Telegram (BOT_TOKEN, CHAT_ID from env)
# - robust to missing data and network errors

import os
import time
import traceback
from datetime import datetime, timezone, timedelta
import pytz
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Bot

# ---------- CONFIG ----------
SYMBOLS = {
    # keys are friendly names; values are list of tickers tried in order
    "NASDAQ": ["^NDX", "NQ=F", "NDX"],
    "EURUSD": ["EURUSD=X"],
    "GBPJPY": ["GBPJPY=X"],
    "GOLD": ["GC=F", "XAUUSD=X"]
}
INTERVAL = "15m"          # interval used for impulse detection
PERIOD = "5d"             # period to download
MIN_BARS = 6              # minimum bars needed to evaluate
MAX_RETRIES = 2           # retry attempts for yfinance
# ----------------------------

def now_utc():
    return datetime.now(timezone.utc)

def session_label(dt_utc):
    # crude session mapping by UTC hour (adjust as you want)
    h = dt_utc.hour
    # Asia: 00:00-06:59 UTC, London: 07:00-11:59 UTC, NY: 12:00-20:59 UTC
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 12:
        return "London"
    return "NY"

def try_download(tickers, period, interval):
    # try tickers in order, return (symbol_used, dataframe) or (None, None)
    last_err = None
    for t in tickers:
        for attempt in range(MAX_RETRIES):
            try:
                data = yf.download(t, period=period, interval=interval, progress=False, threads=False, auto_adjust=True)
                if data is None or data.empty:
                    last_err = f"No data for {t}"
                    time.sleep(1)
                    continue
                # ensure standard column names
                if isinstance(data.index, pd.DatetimeIndex):
                    return t, data
            except Exception as e:
                last_err = f"{t} err: {e}"
                time.sleep(1)
                continue
    return None, None

def detect_impulse(df):
    # returns (is_impulse:bool, score:int, reason:str)
    # simple heuristic:
    # - need at least MIN_BARS bars
    # - look at last 5 bars: require >=3 consecutive bars in same direction (close>open or close<open)
    # - last close must break most recent local high/low depending on direction
    # - volume rising over last 3 bars enhances score
    if df.shape[0] < MIN_BARS:
        return False, 0, "not enough bars"

    # use last 6 bars
    recent = df.tail(6).copy()
    recent["dir"] = np.sign(recent["Close"] - recent["Open"])  # 1 up, -1 down, 0 neutral
    dirs = recent["dir"].astype(int).values
    # count max consecutive same-direction in last 5 bars (exclude the oldest if we used 6)
    last5 = dirs[-5:]
    # find longest run at the end
    last_dir = last5[-1]
    if last_dir == 0:
        return False, 0, "last bar neutral"

    consec = 1
    for i in range(2, 6):
        if last5[-i] == last_dir:
            consec += 1
        else:
            break

    score = 0
    reason_parts = []
    if consec >= 3:
        score += 2
        reason_parts.append(f"{consec} consecutive bars {('UP' if last_dir>0 else 'DOWN')}")

    # breakout check
    if last_dir > 0:
        prior_high = recent["High"].iloc[:-1].max()
        if recent["Close"].iloc[-1] > prior_high:
            score += 2
            reason_parts.append("breaks prior high")
        else:
            reason_parts.append("no breakout")
    else:
        prior_low = recent["Low"].iloc[:-1].min()
        if recent["Close"].iloc[-1] < prior_low:
            score += 2
            reason_parts.append("breaks prior low")
        else:
            reason_parts.append("no breakout")

    # volume trend
    if "Volume" in recent.columns:
        vol = recent["Volume"].astype(float).values
        if len(vol) >= 3 and vol[-1] > np.mean(vol[-3:]):
            score += 1
            reason_parts.append("rising volume")

    is_impulse = score >= 4
    reason = "; ".join(reason_parts)
    return is_impulse, score, reason

def make_summary(symbol_label, ticker_used, is_impulse, score, reason, last_close, session):
    wave = "Impulse" if is_impulse else "Correction"
    direction = "Buy" if is_impulse and "UP" in reason or (is_impulse and last_close) else ("Sell" if is_impulse else "Neutral")
    # simple direction: if last close above prior close => buy else sell
    direction = "Buy" if is_impulse and "UP" in reason else ("Sell" if is_impulse and "DOWN" in reason else ("Neutral" if not is_impulse else "Buy"))
    txt = (
        f"📡 {symbol_label}  ({ticker_used})\n"
        f"🕒 UTC {now_utc().strftime('%Y-%m-%d %H:%M')}\n"
        f"🔎 Session: {session}\n"
        f"📈 Last: {last_close:.5f}\n"
        f"🧭 Bias: {direction}\n"
        f"🌊 Wave: {wave} (score {score})\n"
        f"💡 Reason: {reason}\n"
    )
    return txt

def send_telegram(bot_token, chat_id, text):
    bot = Bot(token=bot_token)
    # send as plain text; safe call
    bot.send_message(chat_id=chat_id, text=text)

def run_one_cycle():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    CHAT_ID = os.environ.get("CHAT_ID")
    if not BOT_TOKEN or not CHAT_ID:
        msg = "⚠️ Missing BOT_TOKEN or CHAT_ID env vars. Set secrets in Actions (BOT_TOKEN, CHAT_ID)."
        print(msg)
        # can't send to Telegram if secrets missing
        return False, msg

    summary_messages = []
    session = session_label(now_utc())

    for label, tickers in SYMBOLS.items():
        ticker_used, df = try_download(tickers, period=PERIOD, interval=INTERVAL)
        if df is None:
            msg = f"❌ {label}: No price data for any ticker in {tickers}"
            print(msg)
            summary_messages.append(msg)
            continue

        # ensure columns present
        if "Close" not in df.columns:
            msg = f"❌ {label}: no Close column in data for {ticker_used}"
            print(msg)
            summary_messages.append(msg)
            continue

        # drop rows with NaN close
        df = df.dropna(subset=["Close"])
        if df.shape[0] < MIN_BARS:
            msg = f"❌ {label}: insufficient bars ({df.shape[0]}) for {ticker_used}"
            print(msg)
            summary_messages.append(msg)
            continue

        try:
            is_impulse, score, reason = detect_impulse(df)
            last_close = float(df["Close"].iloc[-1])
            text = make_summary(label, ticker_used, is_impulse, score, reason, last_close, session)
            # send message
            send_telegram(BOT_TOKEN, CHAT_ID, text)
            summary_messages.append(f"✅ {label}: message sent (score {score})")
            print(f"Sent for {label} ({ticker_used})")
        except Exception as e:
            tb = traceback.format_exc()
            err_msg = f"❌ {label}: error processing: {e}\n{tb}"
            print(err_msg)
            summary_messages.append(err_msg)

    # final status message (sent to same chat)
    final = "Impulse scanner run complete:\n" + "\n".join(summary_messages)
    try:
        send_telegram(BOT_TOKEN, CHAT_ID, final)
    except Exception as e:
        print("Failed to send final summary:", e)
        return False, str(e)
    return True, final

if __name__ == "__main__":
    ok, info = run_one_cycle()
    if not ok:
        print("Scanner failed:", info)
        raise SystemExit(1)
    print("Scanner finished successfully.")
