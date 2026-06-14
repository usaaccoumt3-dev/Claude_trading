import time
import requests
import pandas as pd
import numpy as np
import sys

# --- CONFIGURATION ---
NOTIFICATION_URL = "https://ntfy.sh/rookaif_secret_trading_786"
COINS = ['bitcoin', 'solana', 'binancecoin', 'avalanche-2', 'chainlink']
SYMBOL_MAP = {'bitcoin': 'BTC/USDT', 'solana': 'SOL/USDT', 'binancecoin': 'BNB/USDT', 'avalanche-2': 'AVAX/USDT', 'chainlink': 'LINK/USDT'}

def send_notification(title, message):
    try:
        requests.post(NOTIFICATION_URL, data=message.encode('utf-8'), headers={"Title": title})
    except: pass

def fetch_public_data(coin_id):
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=1"
        data = requests.get(url).json()
        df = pd.DataFrame(data['prices'], columns=['timestamp', 'close'])
        df['open'] = df['close'].shift(1).fillna(df['close'])
        df['high'] = df[['open', 'close']].max(axis=1) * 1.001
        df['low'] = df[['open', 'close']].min(axis=1) * 0.999
        return df
    except: return None

def run_trading_bot():
    print("Bot initialized successfully...", flush=True)
    while True:
        for coin_id in COINS:
            symbol = SYMBOL_MAP[coin_id]
            print(f"Scanning {symbol}...", flush=True)
            df = fetch_public_data(coin_id)
            
            if df is not None:
                # Basic Strategy: Simple EMA/Price check
                ema50 = df['close'].ewm(span=50).mean().iloc[-1]
                if df['close'].iloc[-1] > ema50:
                    send_notification("Signal Alert", f"Price is above EMA50 for {symbol}")
            
            time.sleep(2)
        
        print("Scan complete. Waiting 10 minutes for next cycle...", flush=True)
        sys.stdout.flush()
        time.sleep(600)

if __name__ == '__main__':
    run_trading_bot()
    
