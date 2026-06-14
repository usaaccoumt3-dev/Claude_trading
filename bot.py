import time
import requests
import pandas as pd
import numpy as np

# --- CONFIGURATION ---
NOTIFICATION_URL = "https://ntfy.sh/rookaif_secret_trading_786"
COINS = ['bitcoin', 'solana', 'binancecoin', 'avalanche-2', 'chainlink']
SYMBOL_MAP = {'bitcoin': 'BTC/USDT', 'solana': 'SOL/USDT', 'binancecoin': 'BNB/USDT', 'avalanche-2': 'AVAX/USDT', 'chainlink': 'LINK/USDT'}

last_report_time = time.time()
scan_count_in_hour = 0

def send_notification(title, message, tags="chart_with_upwards_trend,green_circle"):
    try:
        headers = {"Title": title, "Priority": "high", "Tags": tags}
        requests.post(NOTIFICATION_URL, data=message.encode('utf-8'), headers=headers)
    except: pass

def send_hourly_report():
    global scan_count_in_hour
    msg = f"Alhamdulillah, bot is active.\nScans this hour: {scan_count_in_hour}."
    send_notification("🤖 TradeBuddy AI Status", msg, tags="robot,white_check_mark")
    scan_count_in_hour = 0

def fetch_public_data(coin_id):
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=1"
        data = requests.get(url).json()
        prices = data['prices']
        df = pd.DataFrame(prices, columns=['timestamp', 'close'])
        df['open'] = df['close'].shift(1).fillna(df['close'])
        df['high'] = df[['open', 'close']].max(axis=1) * 1.001
        df['low'] = df[['open', 'close']].min(axis=1) * 0.999
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except: return None

def calculate_ema(df, p): return df['close'].ewm(span=p, adjust=False).mean()

# --- 5 STRATEGIES ---
def run_strategies(df, symbol):
    df['ema9'] = calculate_ema(df, 9)
    df['ema20'] = calculate_ema(df, 20)
    df['ema50'] = calculate_ema(df, 50)
    df['ema200'] = calculate_ema(df, 200)
    
    # 1. Mr Trader
    if df['ema50'].iloc[-1] > df['ema200'].iloc[-1]:
        if df['ema9'].iloc[-1] > df['ema20'].iloc[-1] and df['ema9'].iloc[-2] <= df['ema20'].iloc[-2]:
            send_notification("Mr Trader Signal", f"Buy {symbol} - Trend Ride")
    # 2. Golden Sniper
    if df['close'].iloc[-1] < df['close'].iloc[-30:].min() * 1.01:
        send_notification("Golden Sniper", f"Buy {symbol} - Support Zone")
    # 3. SMC Imbalance
    if df['low'].iloc[-1] > df['high'].iloc[-3]:
        send_notification("SMC Signal", f"Buy {symbol} - FVG Break")
    # 4. DLH
    if df['close'].iloc[-1] > df['close'].iloc[-20:].min():
        send_notification("DLH Signal", f"Buy {symbol} - Level Bounce")
    # 5. Range Deviation
    if df['close'].iloc[-1] < df['low'].iloc[-20:].min() * 1.005:
        send_notification("Range Dev", f"Buy {symbol} - Mean Reversion")

def run_trading_bot():
    global last_report_time, scan_count_in_hour
    print("Bot fully initialized with 5 strategies...")
    while True:
        if time.time() - last_report_time >= 3600:
            send_hourly_report()
            last_report_time = time.time()
        for coin_id in COINS:
            df = fetch_public_data(coin_id)
            if df is not None:
                run_strategies(df, SYMBOL_MAP[coin_id])
                time.sleep(1.5)
        scan_count_in_hour += 1
        time.sleep(600)

if __name__ == '__main__':
    run_trading_bot()
    
