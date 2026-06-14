import requests
import time
import os

NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
WATCHLIST = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "ADAUSDT": "ADA"}
TIME_FRAMES = ['15m', '1h']

# Counter file ka path (GitHub Actions ke temporary server par save hoga)
COUNTER_FILE = "scan_counter.txt"

def send_alert(symbol, tf, signal, entry, t1, t2, detail):
    title = f"🚀 {signal} ({tf}) - {symbol}"
    msg = f"🎯 ENTRY AREA: {entry}\n\n💰 EXIT AREAS:\n👉 Target 1: {t1}\n👉 Target 2: {t2}\n\n💡 Info: {detail}"
    try:
        requests.post(NTFY_URL, data=msg.encode("utf-8"), headers={"X-Title": title, "X-Priority": "high", "X-Cache": "no"}, timeout=5)
    except: pass

def send_status_heartbeat():
    title = "🟢 Bot Active (1 Hour Report)"
    msg = f"Bot is running perfectly 24/7.\nScanned 15m & 1h charts continuously for the last 1 hour.\n⏰ Time: {time.strftime('%H:%M UTC')}"
    try:
        requests.post(NTFY_URL, data=msg.encode("utf-8"), headers={"X-Title": title, "X-Priority": "low", "X-Cache": "no"}, timeout=5)
    except: pass

def get_counter():
    if os.path.exists(COUNTER_FILE):
        try:
            with open(COUNTER_FILE, "r") as f:
                return int(f.read().strip())
        except: return 0
    return 0

def update_counter(val):
    try:
        with open(COUNTER_FILE, "w") as f:
            f.write(str(val))
    except: pass

def get_candles(symbol, interval):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=50", timeout=5)
        return [{"close": float(c[4]), "high": float(c[2]), "low": float(c[3])} for c in r.json()]
    except: return None

def analyze(symbol, tf, candles, display_name):
    closes = [c["close"] for c in candles]
    c_close, c_low = candles[-1]["close"], candles[-1]["low"]
    
    ema9 = sum(closes[-9:]) / 9
    ema21 = sum(closes[-21:]) / 21
    recent_high = max(c["high"] for c in candles[-15:])
    
    if ema9 > ema21 and c_low < (sum(closes[-5:]) / 5) * 0.995:
        entry = round(c_close, 4)
        if tf == '15m':
            t1 = round(max(entry * 1.018, recent_high), 4)
            t2 = round(entry * 1.035, 4)
            detail = "15m Dip found in Uptrend. Safe exit at Target 1."
        else:
            t1 = round(max(entry * 1.04, recent_high * 1.01), 4)
            t2 = round(entry * 1.075, 4)
            detail = "1H Strong Bullish Structure. Target 1 is major resistance."
            
        send_alert(display_name, tf, "BULLISH ENTRY", entry, t1, t2, detail)
        return True
    return False

def main():
    print("Starting Scan...")
    any_signal = False
    
    for tf in TIME_FRAMES:
        for symbol, name in WATCHLIST.items():
            candles = get_candles(symbol, tf)
            if candles:
                if analyze(symbol, tf, candles, name):
                    any_signal = True
            time.sleep(0.2)
            
    # Heartbeat logic jo har 1 ghante (6 scans) baad bhejegi
    if not any_signal:
        current_count = get_counter() + 1
        if current_count >= 6:  # 6 scans * 10 minutes = 60 minutes (1 Hour)
            send_status_heartbeat()
            update_counter(0)  # Counter reset
        else:
            print(f"Scan complete. Counter is at {current_count}/6. No notification sent.")
            update_counter(current_count)
    else:
        # Agar koi signal aa gaya, toh counter ko phir bhi chalne dein
        current_count = get_counter() + 1
        if current_count >= 6:
            update_counter(0)
        else:
            update_counter(current_count)

if __name__ == "__main__":
    main()
    
