import requests
import time

NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
WATCHLIST = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "ADAUSDT": "ADA"}
TIME_FRAMES = ['15m', '1h']

def send_alert(symbol, tf, signal, entry, t1, t2, detail):
    title = f"🚀 {signal} ({tf}) - {symbol}"
    msg = (
        f"🎯 ENTRY AREA: {entry}\n\n"
        f"💰 EXIT AREAS (Take Profit):\n"
        f"👉 Target 1 (Safe Exit): {t1}\n"
        f"👉 Target 2 (Max Hold): {t2}\n\n"
        f"💡 Info: {detail}\n"
        f"⏰ Time: {time.strftime('%H:%M UTC')}"
    )
    try:
        headers = {
            "X-Title": title,
            "X-Priority": "high",
            "X-Cache": "no"
        }
        requests.post(NTFY_URL, data=msg.encode("utf-8"), headers=headers, timeout=5)
        print(f"Success: Alert sent for {symbol} ({tf})")
    except Exception as e:
        print(f"Error sending alert: {e}")

def get_candles(symbol, interval):
    try:
        r = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=60", timeout=5)
        if r.status_code != 200: return None
        return [{"close": float(c[4]), "high": float(c[2]), "low": float(c[3])} for c in r.json()]
    except: 
        return None

def analyze(symbol, tf, candles, display_name):
    closes = [c["close"] for c in candles]
    c_close = candles[-1]["close"]
    c_low = candles[-1]["low"]
    
    # Technical Indicators
    ema9 = sum(closes[-9:]) / 9
    ema21 = sum(closes[-21:]) / 21
    
    # 15 candles high/low to find major resistance/exit zones
    recent_high = max(c["high"] for c in candles[-15:])
    
    # Strategy: EMA Cross up + Price Dip lower than 5-period average
    if ema9 > ema21 and c_low < (sum(closes[-5:]) / 5) * 0.995:
        entry = round(c_close, 4)
        
        # Smart Exit Calculations based on Timeframe and Recent Highs
        if tf == '15m':
            # Fast scalp exit targets
            t1 = round(max(entry * 1.018, recent_high), 4)
            t2 = round(entry * 1.035, 4)
            detail = f"15m Dip found in Uptrend. Exit at Target 1 for safe 1.8%+ gain."
        else:
            # 1h Major trend swing exit targets
            t1 = round(max(entry * 1.04, recent_high * 1.01), 4)
            t2 = round(entry * 1.075, 4)
            detail = f"1H Strong Bullish Structure. Target 1 is a major resistance zone."

        send_alert(display_name, tf, "BULLISH ENTRY", entry, t1, t2, detail)
        return True
    return False

def main():
    print(f"Starting Scan: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    for tf in TIME_FRAMES:
        for symbol, name in WATCHLIST.items():
            candles = get_candles(symbol, tf)
            if candles: 
                analyze(symbol, tf, candles, name)
            time.sleep(0.3) # Fast execution delay

if __name__ == "__main__":
    main()
            
