# impulse_scanner.py
# Simple scanner: Yahoo (yfinance) -> detect short impulse -> Telegram message
import os, datetime, pytz, requests
import yfinance as yf

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables")

SYMBOLS = {
    "NAS100": "^NDX",
    "EURUSD": "EURUSD=X",
    "GBPJPY": "GBPJPY=X",
    "GOLD":   "GC=F"
}

# session mapping by UTC hour (approx)
def current_session_utc():
    h = datetime.datetime.utcnow().hour
    # Asia roughly 23:00-06:59 UTC ; London ~07:00-14:59 UTC ; NY ~13:00-20:59 UTC
    if 23 <= h or h < 7:
        return "Asia"
    if 7 <= h < 15:
        return "London"
    return "NY"

def fetch_5m(symbol):
    try:
        df = yf.download(symbol, period="2d", interval="5m", progress=False)
        return df
    except Exception as e:
        return None

def detect_impulse(df):
    # Minimal impulse: last 3 closes strictly increasing (buy) or decreasing (sell)
    if df is None or len(df) < 3:
        return "Unknown", "Neutral"
    closes = df['Close'].dropna().values
    a, b, c = closes[-3], closes[-2], closes[-1]
    if c > b > a:
        return "Impulse", "Buy"
    if c < b < a:
        return "Impulse", "Sell"
    # else treat as correction/oscillation
    # bias by 1H SMA(20)
    try:
        df1h = yf.download(df.tz_localize(None).name if False else df.columns[0] if False else None)
    except Exception:
        pass
    return "Correction", "Neutral"

def higher_timeframe_bias(symbol):
    # 1h SMA20: if last close > sma => Buy bias, < => Sell bias
    try:
        h = yf.download(symbol, period="10d", interval="60m", progress=False)
        if h is None or len(h) < 20:
            return "Neutral"
        sma20 = h['Close'].rolling(20).mean().iloc[-1]
        last = h['Close'].iloc[-1]
        if last > sma20: return "Buy"
        if last < sma20: return "Sell"
    except Exception:
        pass
    return "Neutral"

def build_report():
    session = current_session_utc()
    out = []
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    out.append(f"Impulse Scanner report — {now} — Session: {session}\n")
    for name, sym in SYMBOLS.items():
        df = fetch_5m(sym)
        wave, dir_short = detect_impulse(df)
        ht_bias = higher_timeframe_bias(sym)
        # final direction = combine short + higher timeframe (simple)
        if dir_short == "Neutral":
            direction = ht_bias
        elif ht_bias == "Neutral":
            direction = dir_short
        elif dir_short == ht_bias:
            direction = dir_short
        else:
            # conflict -> prefer higher timeframe
            direction = ht_bias
        out.append(f"{name} ({sym}):")
        out.append(f"  Wave: {wave} | Short: {dir_short} | HTF bias: {ht_bias} -> Final: {direction}")
        # add last price if available
        if df is not None and len(df) > 0:
            last = df['Close'].dropna().iloc[-1]
            out.append(f"  Last: {last:.5f}")
        out.append("") 
    out.append("Notes: Wave detection = simple 3-candle impulse test. Add complexity later.")
    return "\n".join(out)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, data=payload, timeout=15)
    return r.status_code, r.text

if __name__ == "__main__":
    report = build_report()
    code, resp = send_telegram(report)
    if code != 200:
        print("Telegram send failed:", code, resp)
        raise SystemExit(1)
    print("Sent OK")
