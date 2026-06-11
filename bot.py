import requests
import time

NTFY_URL = "https://ntfy.sh/raokaif_trading"
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]
TIMEFRAME = "1h"
LIMIT = 50

def send_alert(title, message, priority="high"):
    try:
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": "chart_with_upwards_trend,moneybag"
        }
        requests.post(NTFY_URL, data=message.encode("utf-8"), headers=headers, timeout=10)
        print(f"✅ Alert sent: {title}")
    except Exception as e:
        print(f"⚠️ Alert error: {e}")

def get_candles(symbol):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={TIMEFRAME}&limit={LIMIT}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        candles = []
        for c in data:
            candles.append({
                "open":  float(c[1]),
                "high":  float(c[2]),
                "low":   float(c[3]),
                "close": float(c[4]),
                "vol":   float(c[5])
            })
        return candles
    except Exception as e:
        print(f"⚠️ Candle fetch error {symbol}: {e}")
        return None

def ema(closes, period):
    k = 2 / (period + 1)
    val = closes[0]
    for price in closes[1:]:
        val = price * k + val * (1 - k)
    return val

def rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def bollinger(closes, period=20):
    recent = closes[-period:]
    mean = sum(recent) / period
    std = (sum((x - mean) ** 2 for x in recent) / period) ** 0.5
    return mean - 2 * std, mean, mean + 2 * std

def analyze(symbol, candles):
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    vols   = [c["vol"]   for c in candles]

    curr        = candles[-1]
    c_close     = curr["close"]
    c_low       = curr["low"]
    c_high      = curr["high"]

    past        = candles[-16:-1]
    past_highs  = [c["high"] for c in past]
    past_lows   = [c["low"]  for c in past]
    past_vols   = [c["vol"]  for c in past]
    high_15     = max(past_highs)
    low_15      = min(past_lows)

    ema9        = ema(closes[-20:], 9)
    ema21       = ema(closes[-30:], 21)
    ema50       = ema(closes[-60:], 50)
    rsi_val     = rsi(closes[-15:])
    bb_lower, bb_mid, bb_upper = bollinger(closes)

    prev_ema9   = ema(closes[-21:-1], 9)
    prev_ema21  = ema(closes[-31:-1], 21)

    signal      = None
    target1     = None
    target2     = None
    invalid     = None

    # Strategy 1: SMC Liquidity Sweep + EMA50 filter
    if c_low < low_15 and c_close > low_15 and c_close > ema50:
        signal   = "SMC Liquidity Sweep ⚡"
        detail   = f"Swept {low_15:.4f} low, rejected up. EMA50 trend confirmed."
        target1  = round(c_close * 1.02, 4)
        target2  = round(c_close * 1.04, 4)
        invalid  = round(c_close * 0.97, 4)

    # Strategy 2: Golden Sniper Fib 50-61.8% + EMA50 filter
    elif (high_15 - low_15) > 0:
        fib50  = high_15 - 0.50  * (high_15 - low_15)
        fib618 = high_15 - 0.618 * (high_15 - low_15)
        if c_low <= fib50 and c_close >= fib618 and c_close > ema50:
            signal  = "Golden Sniper Fib 🎯"
            detail  = f"Fib 50-61.8% zone hit ({fib618:.4f}-{fib50:.4f}). EMA50 above."
            target1 = round(c_close * 1.025, 4)
            target2 = round(c_close * 1.05, 4)
            invalid = round(c_close * 0.97, 4)

    # Strategy 3: EMA 9/21 Crossover + RSI confirm
    if signal is None:
        if prev_ema9 < prev_ema21 and ema9 > ema21 and rsi_val > 50:
            signal  = "EMA 9/21 Crossover 📈"
            detail  = f"EMA9 crossed above EMA21. RSI={rsi_val:.1f}"
            target1 = round(c_close * 1.02, 4)
            target2 = round(c_close * 1.035, 4)
            invalid = round(c_close * 0.975, 4)

    # Strategy 4: Bollinger Band Mean Reversion (Ranging Market)
    if signal is None:
        if c_close <= bb_lower and candles[-1]["close"] > candles[-2]["close"]:
            signal  = "BB Mean Reversion 📊"
            detail  = f"Price hit lower BB ({bb_lower:.4f}), bouncing to mid ({bb_mid:.4f})"
            target1 = round(bb_mid, 4)
            target2 = round(bb_mid * 1.01, 4)
            invalid = round(c_close * 0.975, 4)

    if signal:
        title = f"🚨 BUY SIGNAL: {symbol}"
        msg = (
            f"Strategy: {signal}\n"
            f"Entry: {c_close:.4f}\n"
            f"Target 1: {target1} (+2%)\n"
            f"Target 2: {target2} (+4%)\n"
            f"Invalidation: {invalid} (-3%)\n"
            f"Detail: {detail}\n"
            f"Time: {time.strftime('%H:%M UTC')}"
        )
        print(f"🔥 SIGNAL: {symbol} — {signal}")
        send_alert(title, msg)
    else:
        print(f"   • {symbol}: No setup.")

def main():
    print(f"🚀 Scan started: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    for symbol in WATCHLIST:
        candles = get_candles(symbol)
        if candles and len(candles) >= 30:
            analyze(symbol, candles)
        else:
            print(f"⚠️ Not enough data: {symbol}")
    print("✅ Scan complete.")

main()
