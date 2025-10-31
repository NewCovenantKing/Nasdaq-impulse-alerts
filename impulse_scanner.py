import os
import yfinance as yf
from telegram import Bot
from datetime import datetime

# === Telegram setup ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

bot = Bot(token=BOT_TOKEN)

# === Example scanner logic ===
# You can later expand this to include Elliott Wave or Silver Bullet logic.
symbol = "NAS100"  # Example instrument
data = yf.download(tickers="^NDX", period="1d", interval="5m")

# Basic check (for demonstration)
latest_close = data['Close'][-1]
previous_close = data['Close'][-2]
direction = "BUY 🚀" if latest_close > previous_close else "SELL 🔻"

# === Compose message ===
message = (
    f"📊 *Impulse Scanner Alert*\n\n"
    f"Symbol: {symbol}\n"
    f"Direction: {direction}\n"
    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    f"Source: GitHub Auto-Scanner"
)

# === Send message to your personal Telegram ===
bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")

print("✅ Alert sent to personal Telegram successfully.")
