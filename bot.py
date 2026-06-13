import requests
import time

# Aapka naya test-shuda aur working ntfy topic URL
NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"

# Coins watchlist (CoinGecko IDs aur unke symbols)
WATCHLIST = {
    "bitcoin":  "BTC",
    "ethereum": "ETH",
    "solana":   "SOL",
    "cardano":  "ADA"
}

def send_alert(symbol, signal, entry, t1, t2, detail):
    title = f"SIGNAL: {symbol} - {signal}"
    msg = (
        f"Entry: {entry}\n"
        f"Target 1: {t1}\n"
        f"Target 2: {t2}\n"
        f"Detail: {detail}\n"
        f"Time: {time.strftime('%H:%M UTC')}"
    )
    try:
        headers = {
            "X-Title": title,
            "X-Priority": "high",
            "X-Cache": "no"
        }
        response = requests.post(NTFY_URL, data=msg.encode("utf-8"), headers=headers, timeout=15)
        if response.status_code == 200:
            print(f"Success: Alert sent for {symbol}")
        else:
            print(f"Alert Failed: Status code {response.status_code}")
    except Exception as e:
        print(f"Alert Connection Error: {e}")

def send_status_heartbeat():
    try:
        headers = {
            "X-Title": "Bot Active",
            "X-Priority": "low",
            "X-Cache": "no"
        }
        msg = f"Market scan complete. No setups found.\nTime: {time.strftime('%H:%M UTC')}"
        response = requests.post(NTFY_URL, data=msg.encode("utf-8"), headers=headers, timeout=15)
        if response.status_code == 200:
            print("Status heartbeat sent to ntfy.")
    except Exception as e:
        print(f"Status Heartbeat Error: {e}")

def get_candles(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc?vs_currency=usd&days=7"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            print(f"CoinGecko API Error for {coin_id}: Status {r.status_code}")
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) < 35:
            return None
        candles = []
        for c in data:
            try:
                candles.append({
                    "open":  float(c[1]),
                    "high":  float(c[2]),
                    "low":   float(c[3]),
                    "close": float(c[4])
                })
            except:
                continue
        return candles
    except Exception as e:
        print(f"Connection Error for {coin_id}: {e}")
        return None

def ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for price in closes[period:]:
        val = price * k + val * (1 - k)
    return val

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100
    return 100 - (100 / (1 + avg_g / avg_l))

def bollinger(closes, period=20):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]
    recent = closes[-period:]
    mean = sum(recent) / period
    std = (sum((x - mean)**2 for x in recent) / period) ** 0.5
    return mean - 2*std, mean, mean + 2*std

def analyze(coin_id, symbol, candles):
    closes = [c["close"] for c in candles]
    curr    = candles[-2]
    prev    = candles[-3]
    c_close = curr["close"]
    c_low   = curr["low"]

    past       = candles[-17:-2]
    high_15    = max(c["high"] for c in past)
    low_15     = min(c["low"]  for c in past)

    ema9       = ema(closes[-25:], 9)
    ema21      = ema(closes[-35:], 21)
    ema50      = ema(closes, 50)
    rsi_val    = rsi(closes[-20:])
    bb_low, bb_mid, _ = bollinger(closes)

    prev_ema9  = ema(closes[-26:-1], 9)
    prev_ema21 = ema(closes[-36:-1], 21)

    signal = None
    detail = ""
    t1 = t2 = None
    entry = round(c_close, 4)

    # 1. SMC Liquidity Sweep
    if c_low < low_15 and c_close > low_15 and c_close > ema50:
        signal = "SMC Liquidity Sweep"
        detail = f"Swept low {round(low_15,4)}, rejected up."
        t1 = round(c_close * 1.02, 4)
        t2 = round(c_close * 1.04, 4)

    # 2. Golden Sniper Fibonacci
    elif (high_15 - low_15) > 0:
        fib50  = high_15 - 0.500 * (high_15 - low_15)
        fib618 = high_15 - 0.618 * (high_15 - low_15)
        if c_low <= fib50 and c_close >= fib618 and c_close > ema50:
            signal = "Golden Sniper Fib"
            detail = f"Fib zone {round(fib618,4)}-{round(fib50,4)} hit."
            t1 = round(c_close * 1.025, 4)
            t2 = round(c_close * 1.050, 4)

    # 3. EMA 9/21 Crossover
    if signal is None:
        if prev_ema9 < prev_ema21 and ema9 > ema21 and rsi_val > 50:
            signal = "EMA 9/21 Crossover"
            detail = f"EMA9 crossed EMA21 up. RSI={round(rsi_val,1)}"
            t1 = round(c_close * 1.020, 4)
            t2 = round(c_close * 1.035, 4)

    # 4. Bollinger Band Bounce
    if signal is None:
        if c_close <= bb_low and c_close > prev["close"]:
            signal = "BB Mean Reversion"
            detail = f"Lower BB {round(bb_low,4)} hit, bouncing."
            t1 = round(bb_mid, 4)
            t2 = round(bb_mid * 1.01, 4)

    if signal:
        send_alert(symbol, signal, entry, t1, t2, detail)
        return True
    return False

def main():
    print(f"Starting Scan: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    any_signal_found = False
    
    for coin_id, symbol in WATCHLIST.items():
        print(f"Scanning {symbol}...")
        candles = get_candles(coin_id)
        if candles:
            has_signal = analyze(coin_id, symbol, candles)
            if has_signal:
                any_signal_found = True
        time.sleep(2)  # API rate limit safety delay
    
    if not any_signal_found:
        send_status_heartbeat()
        
    print("Scan finished successfully.")

if __name__ == "__main__":
    main()
    
