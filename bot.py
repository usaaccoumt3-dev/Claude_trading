import time
import requests
import ccxt
import pandas as pd
from datetime import datetime, timezone

NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS  = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']

exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})

def notify(title, msg):
    try:
        headers = {"Title": title, "Priority": "high", "Tags": "rocket"}
        requests.post(NTFY_URL, data=msg.encode('utf-8'), headers=headers, timeout=10)
    except: pass

def get_df(symbol, timeframe='15m', limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
    except: return None

def get_ema(df, p): return df['close'].ewm(span=p, adjust=False).mean()

def run():
    print("[SYSTEM] Pro Strategies Active: Sweep, FVG, EMA, Trend")
    while True:
        for symbol in SYMBOLS:
            df = get_df(symbol)
            if df is None: continue
            
            # Trend Filter
            ema_200 = get_ema(df, 200).iloc[-1]
            price = df['close'].iloc[-1]
            
            if price > ema_200: # Uptrend
                # Strategy 1: Sweep
                swing_low = df['low'].iloc[-20:-1].min()
                if df['low'].iloc[-2] < swing_low and df['close'].iloc[-1] > swing_low:
                    notify("SIGNAL: SWEEP", f"{symbol} Bullish Sweep at {price:.2f}")
                
                # Strategy 2: FVG
                if df['low'].iloc[-1] > df['high'].iloc[-3]:
                    notify("SIGNAL: FVG", f"{symbol} Bullish FVG at {price:.2f}")
        
        time.sleep(900)

if __name__ == '__main__':
    run()
        
