# impulse_scanner.py — simple scheduled scanner that posts to your Telegram bot
import os
import time
from datetime import datetime
import yfinance as yf
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID  = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

bot = Bot(BOT_TOKEN)

# small, fast Nasdaq/EURUSD/GBPJPY/Gold impulse checker (minute-level)
SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD": "GC=F"
}

def is_impulse(ticker):
    # quick heuristic: large single-minute move vs 5-min avg range
    data = ticker.history(period="15m", interval="1m", actions=False)
    if data.shape[0] < 6:
        return False, None
    last = data.iloc[-1]
    prev5 = data['High'][-6:-1] - data['Low'][-6:-1]
    avg_range = prev5.mean()
    last_range = last['High'] - last['Low']
    # impulse if last range >= 2.5× average range (adjust as you like)
    if avg_range > 0 and last_range / avg_range >= 2.5:
        direction = "BUY" if last['Close'] > data['Open'][-6] else "SELL"
        return True, direction
    return False, None

def check_all_and_notify():
    messages = []
    for name, sym in SYMBOLS.items():
        t = yf.Ticker(sym)
        imp, direction = is_impulse(t)
        if imp:
            messages.append(f"{name} impulse detected → {direction}")
    if messages:
        text = f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}] Impulses:\n" + "\n".join(messages)
        # ---------- Compose enhanced alert ----------
# these names are tolerant: if your script already sets direction, wave, sb_zone, price, it will use them.
direction = globals().get("direction") or locals().get("direction") or "N/A"
wave      = globals().get("wave")      or locals().get("wave")      or ("Impulse" if globals().get("impulse_detected") else "Correction" if globals().get("impulse_detected") is not None else "N/A")
sb_zone   = globals().get("sb_zone")   or locals().get("sb_zone")   or "N/A"
price     = globals().get("price")     or locals().get("price")     or globals().get("last_price") or "N/A"

from datetime import datetime
now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

message = (
    f"📣 *Scanner Summary*\n"
    f"• Symbol: {globals().get('symbol') or 'N/A'}\n"
    f"• Direction: *{direction}*\n"
    f"• Wave: {wave}\n"
    f"• SilverBullet zone: {sb_zone}\n"
    f"• Price: {price}\n"
    f"• Time: {now}"
)

# send as plain text (or set parse_mode="Markdown" if you like)
# ---- AUTO-DETECTION: Direction / Wave / SilverBullet / Auto-rules ----
def detect_direction_wave_zone(df, pct_threshold=0.002):
    """
    df: pandas DataFrame with columns ['Open','High','Low','Close','Volume'] indexed oldest->newest
    pct_threshold: minimum absolute move (fraction) to treat as strong impulse (e.g. 0.002 = 0.2%)
    returns: (direction, wave, zone, move_pct)
    """
    if df.shape[0] < 5:
        return ("N/A","N/A","N/A",0.0)

    last = df.iloc[-1]
    prev = df.iloc[-2]
    window_high = df['High'].iloc[-5:].max()
    window_low  = df['Low'].iloc[-5:].min()

    # percent move from prev close
    move_pct = (last['Close'] - prev['Close']) / prev['Close']

    # Direction heuristic (simple, robust)
    if last['Close'] > prev['High']:
        direction = "Buy"
    elif last['Close'] < prev['Low']:
        direction = "Sell"
    else:
        # small drift -> keep direction by sign of move_pct
        direction = "Buy" if move_pct > 0 else "Sell" if move_pct < 0 else "Neutral"

    # Wave heuristic: impulse if current close breaks recent swing extremes and magnitude > threshold
    broke_high = last['Close'] > window_high
    broke_low  = last['Close'] < window_low
    if (broke_high or broke_low) and abs(move_pct) >= pct_threshold:
        wave = "Impulse"
    else:
        wave = "Correction"

    # SilverBullet zone by UTC hour (approx):
    # Asia: 0-7 UTC, London: 7-15 UTC, NewYork: 13-22 UTC (overlap handled by hour ranges)
    import datetime, pytz
    now = datetime.datetime.utcnow()
    h = now.hour
    if 0 <= h < 7:
        zone = "Asia"
    elif 7 <= h < 13:
        zone = "London"
    elif 13 <= h < 22:
        zone = "New York"
    else:
        zone = "Asia"

    return (direction, wave, zone, float(move_pct))

# Use the helper just before sending the message.
# (Assumes `df` for current symbol is already available; adapt `df_symbol` variable name if different.)
direction, wave, zone, move_pct = detect_direction_wave_zone(df_symbol, pct_threshold=0.002)

# Auto-rule: only send if impulse OR user wants everything.
SEND_ONLY_ON_IMPULSE = True   # set False to receive all summaries (for debugging)
if SEND_ONLY_ON_IMPULSE and wave != "Impulse":
    # Optional: send a lightweight summary or skip
    message_text = f"📣 Scanner Summary — {symbol}\n• Wave: {wave} (no strong impulse)\n• Direction: {direction}\n• Zone: {zone}\n• Move: {move_pct:.3%}\n(Alerts suppressed unless Impulse)"
else:
    message_text = (
        f"📣 Scanner Summary — {symbol}\n"
        f"• Direction: {direction}\n"
        f"• Wave: {wave}\n"
        f"• SilverBullet zone: {zone}\n"
        f"• Price: {last['Close']:.5f}\n"
        f"• Move: {move_pct:.3%}\n"
        f"• Time (UTC): {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
    )
# ---- AUTO-DETECTION: Direction / Wave / SilverBullet / Auto-rules ----
def detect_direction_wave_zone(df, pct_threshold=0.002):
    """
    df: pandas DataFrame with columns ['Open','High','Low','Close','Volume'] indexed oldest->newest
    pct_threshold: minimum absolute move (fraction) to treat as strong impulse (e.g. 0.002 = 0.2%)
    returns: (direction, wave, zone, move_pct)
    """
    if df.shape[0] < 5:
        return ("N/A","N/A","N/A",0.0)

    last = df.iloc[-1]
    prev = df.iloc[-2]
    window_high = df['High'].iloc[-5:].max()
    window_low  = df['Low'].iloc[-5:].min()

    # percent move from prev close
    move_pct = (last['Close'] - prev['Close']) / prev['Close']

    # Direction heuristic (simple, robust)
    if last['Close'] > prev['High']:
        direction = "Buy"
    elif last['Close'] < prev['Low']:
        direction = "Sell"
    else:
        # small drift -> keep direction by sign of move_pct
        direction = "Buy" if move_pct > 0 else "Sell" if move_pct < 0 else "Neutral"

    # Wave heuristic: impulse if current close breaks recent swing extremes and magnitude > threshold
    broke_high = last['Close'] > window_high
    broke_low  = last['Close'] < window_low
    if (broke_high or broke_low) and abs(move_pct) >= pct_threshold:
        wave = "Impulse"
    else:
        wave = "Correction"

    # SilverBullet zone by UTC hour (approx):
    # Asia: 0-7 UTC, London: 7-15 UTC, NewYork: 13-22 UTC (overlap handled by hour ranges)
    import datetime, pytz
    now = datetime.datetime.utcnow()
    h = now.hour
    if 0 <= h < 7:
        zone = "Asia"
    elif 7 <= h < 13:
        zone = "London"
    elif 13 <= h < 22:
        zone = "New York"
    else:
        zone = "Asia"

    return (direction, wave, zone, float(move_pct))

# Use the helper just before sending the message.
# (Assumes `df` for current symbol is already available; adapt `df_symbol` variable name if different.)
direction, wave, zone, move_pct = detect_direction_wave_zone(df_symbol, pct_threshold=0.002)

# Auto-rule: only send if impulse OR user wants everything.
SEND_ONLY_ON_IMPULSE = True   # set False to receive all summaries (for debugging)
if SEND_ONLY_ON_IMPULSE and wave != "Impulse":
    # Optional: send a lightweight summary or skip
    message_text = f"📣 Scanner Summary — {symbol}\n• Wave: {wave} (no strong impulse)\n• Direction: {direction}\n• Zone: {zone}\n• Move: {move_pct:.3%}\n(Alerts suppressed unless Impulse)"
else:
    message_text = (
        f"📣 Scanner Summary — {symbol}\n"
        f"• Direction: {direction}\n"
        f"• Wave: {wave}\n"
        f"• SilverBullet zone: {zone}\n"
        f"• Price: {last['Close']:.5f}\n"
        f"• Move: {move_pct:.3%}\n"
        f"• Time (UTC): {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
    )

# send to your bot (existing bot variable)
bot.send_message(CHAT_ID, message_text)
# ---- end AUTO-DETECTION ----


if __name__ == "__main__":
    # run once (GitHub Actions will schedule). For local runs, you can loop.
    try:
        check_all_and_notify()
    except Exception as e:
        # report error to your telegram
        bot.send_message(chat_id=CHAT_ID, text=f"Scanner error: {e}")
        raise
