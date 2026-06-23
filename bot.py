import time
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS  = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
TF_ENTRY = '15m'
TF_TREND = '1h'

try:
    exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    print("[OK] Exchange (MEXC) connected")
except Exception as e:
    print(f"[FATAL] Exchange error: {e}")
    exit(1)

active_trades = {}

def notify(title, msg, tags="rocket"):
    try:
        msg_clean = msg.encode('ascii', 'ignore').decode('ascii')
        headers = {"Title": title, "Priority": "high", "Tags": tags}
        requests.post(NTFY_URL, data=msg_clean.encode('utf-8'), headers=headers, timeout=10)
    except Exception as e:
        print(f"[NOTIF ERROR] {e}")

# --- STRATEGIES ---
def get_df(symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
    except: return None

def ema(df, p): return df['close'].ewm(span=p, adjust=False).mean()

def strat_sweep(df, symbol):
    c, p = df.iloc[-1], df.iloc[-2]
    swing = df['low'].iloc[-20:-1].min()
    if p['low'] < swing and c['close'] > swing:
        notify("SIGNAL", f"{symbol} SWEEP DETECTED")

def strat_fvg(df, symbol):
    for i in range(3, 10):
        if df['low'].iloc[-i+2] > df['high'].iloc[-i]:
            notify("SIGNAL", f"{symbol} FVG DETECTED")
            break

def run():
    print("[START] Testing Mode Active - No Session Limits")
    while True:
        try:
            # Session check remove kar diya hai taake har waqt chalta rahe
            for symbol in SYMBOLS:
                df = get_df(symbol, TF_ENTRY)
                if df is not None:
                    strat_sweep(df, symbol)
                    strat_fvg(df, symbol)
            
            print("[SCAN] Cycle complete, waiting...")
            time.sleep(600) # Har 10 minute mein scan
        except Exception as e:
            time.sleep(60)

if __name__ == '__main__':
    run()
    
