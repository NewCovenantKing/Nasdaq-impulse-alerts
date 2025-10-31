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
bot.send_message(CHAT_ID, message)
# ---------- end enhanced alert ----------

if __name__ == "__main__":
    # run once (GitHub Actions will schedule). For local runs, you can loop.
    try:
        check_all_and_notify()
    except Exception as e:
        # report error to your telegram
        bot.send_message(chat_id=CHAT_ID, text=f"Scanner error: {e}")
        raise
