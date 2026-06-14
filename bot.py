import time
import requests
import ccxt
import pandas as pd
import numpy as np

# --- CONFIGURATION & API SETTINGS ---
NOTIFICATION_URL = "https://ntfy.sh/rookaif_secret_trading_786"

# Pure Halal Spot Scan - Top Volatile Pairs
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'AVAX/USDT']
TIMEFRAME = '15m'

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}  # STRICTLY SPOT ONLY
})

# Tracker variables hourly report ke liye
last_report_time = time.time()
scan_count_in_hour = 0

def send_notification(title, message, tags="chart_with_upwards_trend,green_circle"):
    """Sends immediate notification to your ntfy channel"""
    try:
        headers = {
            "Title": title,
            "Priority": "high",
            "Tags": tags
        }
        response = requests.post(NOTIFICATION_URL, data=message.encode('utf-8'), headers=headers)
        if response.status_code == 200:
            print(f"Notification Sent: {title}")
    except Exception as e:
        print(f"Notification Error: {e}")

def send_hourly_report():
    """Sends a status report every hour to confirm the bot is active"""
    global scan_count_in_hour
    title = "🤖 TradeBuddy AI Status Report"
    message = f"Alhamdulillah, bot is running perfectly fine.\n\n🔄 Market scans completed this hour: {scan_count_in_hour}\n📡 Status: Active & hunting for 100% Halal setups.\n\nNo active signal triggered in this round, waiting for the next candles."
    send_notification(title, message, tags="robot,white_check_mark")
    scan_count_in_hour = 0  # Reset counter hourly report ke baad

# --- TECHNICAL INDICATORS ---
def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def fetch_data(symbol, timeframe, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

# --- PRACTICAL STRATEGIES ENGINES (FLEXIBLE & REASONABLE RULES) ---

def scan_daily_level_hunt(df, symbol):
    """1. Daily Level Hunt (DLH) - Safe Entry Mode"""
    try:
        d_ohlcv = exchange.fetch_ohlcv(symbol, '1d', limit=2)
        if len(d_ohlcv) < 2: return
        pd_low = d_ohlcv[0][3]
        
        last_candle = df.iloc[-2]
        current_candle = df.iloc[-1]
        
        # Low sweep setup: Previous 15m candle swept daily low, current candle closes inside/above
        if last_candle['low'] < pd_low and current_candle['close'] > pd_low:
            if current_candle['close'] > last_candle['close']:  # Simple structural response
                entry_range = f"{current_candle['close']} - {current_candle['close'] * 1.003:.4f}"
                tp = current_candle['close'] * 1.03
                sl = min(last_candle['low'], current_candle['low']) * 0.996
                
                msg = f"🟢 100% Halal Spot Buy\nCoin: {symbol}\n🎯 ENTRY AREA: {entry_range}\n💰 TARGET PRICE (TP): {tp:.4f}\n🛡️ STOP LOSS (SL): {sl:.4f}\nRules: DLH Low Sweep & Bounce."
                send_notification("Daily Level Hunt (DLH) Triggered!", msg)
    except Exception as e:
        print(f"DLH Error: {e}")

def scan_golden_sniper(df, symbol):
    """2. Golden Sniper - Practical Fib Pocket Entry"""
    try:
        recent_low = df['low'].iloc[-30:].min()
        recent_high = df['high'].iloc[-30:].max()
        
        fib_618 = recent_high - (0.618 * (recent_high - recent_low))
        fib_786 = recent_high - (0.786 * (recent_high - recent_low))
        
        current_price = df['close'].iloc[-1]
        
        # Price is simply inside the golden zone and showing a green close response
        if fib_786 <= current_price <= fib_618 and df['close'].iloc[-1] > df['open'].iloc[-1]:
            entry_range = f"{current_price} - {current_price * 1.003:.4f}"
            tp = recent_high
            sl = recent_low * 0.996
            
            msg = f"🟢 100% Halal Spot Buy\nCoin: {symbol}\n🎯 ENTRY AREA: {entry_range}\n💰 TARGET PRICE (TP): {tp:.4f}\n🛡️ STOP LOSS (SL): {sl:.4f}\nRules: Golden Fib Pocket Reversal."
            send_notification("Golden Sniper Triggered!", msg)
    except Exception as e:
        print(f"Golden Sniper Error: {e}")

def scan_smc_imbalance(df, symbol):
    """3. SMC Imbalance - Practical FVG Retest"""
    try:
        c1_high = df['high'].iloc[-3]
        c3_low = df['low'].iloc[-1]
        current_price = df['close'].iloc[-1]
        
        # If an active FVG gap is present and price taps the area
        if c3_low > c1_high:
            entry_range = f"{current_price} - {current_price * 1.002:.4f}"
            tp = current_price * 1.02
            sl = c1_high * 0.996
            
            msg = f"🟢 100% Halal Spot Buy\nCoin: {symbol}\n🎯 ENTRY AREA: {entry_range}\n💰 TARGET PRICE (TP): {tp:.4f}\n🛡️ STOP LOSS (SL): {sl:.4f}\nRules: FVG Gap Dynamic Entry."
            send_notification("SMC Open Imbalance Triggered!", msg)
    except Exception as e:
        print(f"SMC Imbalance Error: {e}")

def scan_mr_trader(df, symbol):
    """4. Mr Trader - EMA 50 > 200 + 9/20 Dynamic Crossover"""
    try:
        df['ema9'] = calculate_ema(df, 9)
        df['ema20'] = calculate_ema(df, 20)
        df['ema50'] = calculate_ema(df, 50)
        df['ema200'] = calculate_ema(df, 200)
        
        c_candle = df.iloc[-1]
        l_candle = df.iloc[-2]
        
        if c_candle['ema50'] > c_candle['ema200']:
            # Fluid crossover execution: 9 crosses above 20
            if l_candle['ema9'] <= l_candle['ema20'] and c_candle['ema9'] > c_candle['ema20']:
                entry_range = f"{c_candle['close']} - {c_candle['close'] * 1.002:.4f}"
                tp = c_candle['close'] * 1.025
                sl = c_candle['ema20'] * 0.995
                
                msg = f"🟢 100% Halal Spot Buy\nCoin: {symbol}\n🎯 ENTRY AREA: {entry_range}\n💰 TARGET PRICE (TP): {tp:.4f}\n🛡️ STOP LOSS (SL): {sl:.4f}\nRules: EMA 50>200 + 9/20 Crossover Ride."
                send_notification("Mr Trader Trend Triggered!", msg)
    except Exception as e:
        print(f"Mr Trader Error: {e}")

def scan_range_deviation(df, symbol):
    """5. Range Deviation - Sideways Sweep Play"""
    try:
        range_high = df['high'].iloc[-20:-1].max()
        range_low = df['low'].iloc[-20:-1].min()
        
        current_candle = df.iloc[-1]
        df['rsi'] = calculate_rsi(df)
        current_rsi = df['rsi'].iloc[-1]
        
        # Dynamic check for range deviation inside flat zones
        if current_candle['low'] < range_low and current_candle['close'] > range_low:
            if 35 <= current_rsi <= 65:  # Realistic wide sideways boundary check
                entry_range = f"{current_candle['close']} - {current_candle['close'] * 1.002:.4f}"
                tp = range_high
                sl = current_candle['low'] * 0.995
                
                msg = f"🟢 100% Halal Spot Buy\nCoin: {symbol}\n🎯 ENTRY AREA: {entry_range}\n💰 TARGET PRICE (TP): {tp:.4f}\n🛡️ STOP LOSS (SL): {sl:.4f}\nRules: Sideways Deviation Support Play."
                send_notification("Range Deviation Triggered!", msg)
    except Exception as e:
        print(f"Range Deviation Error: {e}")

# --- MAIN CONTROLLER WITH HOURLY HEARTBEAT REPORT ---
def run_trading_bot():
    global last_report_time, scan_count_in_hour
    print("Starting the auto-restarting trading loop with dynamic confirmation filters...")
    
    while True:
        current_time = time.time()
        
        # Check if 1 hour (3600 seconds) has passed to send status notification report
        if current_time - last_report_time >= 3600:
            send_hourly_report()
            last_report_time = current_time
            
        print("Scanning markets...")
        for symbol in SYMBOLS:
            df = fetch_data(symbol, TIMEFRAME)
            if df is not None and not df.empty:
                scan_daily_level_hunt(df, symbol)
                scan_golden_sniper(df, symbol)
                scan_smc_imbalance(df, symbol)
                scan_mr_trader(df, symbol)
                scan_range_deviation(df, symbol)
                time.sleep(1)
        
        scan_count_in_hour += 1
        print("Scan complete. Waiting 10 minutes...")
        time.sleep(600)  # Check next candle cycles loop every 10 mins

if __name__ == '__main__':
    run_trading_bot()

