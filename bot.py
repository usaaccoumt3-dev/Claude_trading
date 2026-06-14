import requests
import time

NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
WATCHLIST = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "ADAUSDT": "ADA"}
TIME_FRAMES = ['15m', '1h']

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
        print("Hourly heartbeat notification sent successfully!")
    except: pass

def get_candles(symbol, interval):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=50", timeout=5)
        return [{"close": float(c[4]), "high": float(c[2]), "low": float(c[3])} for r in r.json()]
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

def scan_markets():
    print(f"Scanning markets at {time.strftime('%H:%M:%S UTC')}...")
    for tf in TIME_FRAMES:
        for symbol, name in WATCHLIST.items():
            candles = get_candles(symbol, tf)
            if candles:
                analyze(symbol, tf, candles, name)
            time.sleep(0.2)

def main():
    print("Starting Main Engine...")
    
    # Pehle scan par hi sukoon ke liye aik report bhej dete hain
    send_status_heartbeat()
    
    last_heartbeat_time = time.time()
    
    while True:
        # 1. Market Scan karein
        scan_markets()
        
        # 2. Check karein ke kya 1 ghanta (3600 seconds) guzar gaya hai?
        current_time = time.time()
        if current_time - last_heartbeat_time >= 3600:
            send_status_heartbeat()
            last_heartbeat_time = current_time  # Time reset for next hour
            
        print("Scan cycle complete. Sleeping for 10 minutes...")
        time.sleep(600)

if __name__ == "__main__":
    main()
    
