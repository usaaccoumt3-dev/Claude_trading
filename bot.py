import time
import requests
import ccxt
import pandas as pd
import numpy as np
import json
from datetime import datetime, timezone

# --- GLOBAL CONFIGURATION (Original) ---
NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS  = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
EXCHANGES = ['mexc', 'binance', 'bybit']
TF_ENTRY = '15m'
TF_TREND = '1h'

# --- SMART ENGINE (Optimizer & Failover) ---
class StrategyOptimizer:
    def __init__(self):
        self.strat_weights = {"SWEEP": 1.0, "EMA": 1.0, "BREAKOUT": 1.0, "DAY_HL": 1.0}
    def update(self, strat, res):
        if res == "WIN": self.strat_weights[strat] += 0.05
        else: self.strat_weights[strat] -= 0.1

optimizer = StrategyOptimizer()

def get_exchange():
    for ex in EXCHANGES:
        try: return getattr(ccxt, ex)({'enableRateLimit': True})
        except: continue
    return None

exchange = get_exchange()

# --- ALL STRATEGIES (Original Logic + New Day High/Low) ---

def strat_sweep(df, symbol):
    c = df.iloc[-1]; p = df.iloc[-2]; swing = df['low'].iloc[-20:-1].min()
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    return p['low'] < swing and c['close'] > swing and c['volume'] > vol_ma * 1.2

def strat_ema_pullback(df, symbol):
    # Aapka original EMA Pullback logic
    ema20 = df['close'].ewm(span=20).mean().iloc[-1]
    return df['close'].iloc[-1] > ema20 and df['close'].iloc[-1] > df['open'].iloc[-1]

def strat_breakout(df, symbol):
    # Aapka original Breakout logic
    resist = df['high'].iloc[-20:-2].max()
    return df['close'].iloc[-1] > resist

def strat_day_high_low(df, symbol):
    # Aapki nayi Day High Breakout strategy
    day_high = df['high'].rolling(96).max().iloc[-1]
    return df['close'].iloc[-1] > day_high

# --- MONITOR AND MAIN LOOP ---
def run():
    print("[SYSTEM] Starting Full-Scale Bot...")
    while True:
        try:
            for symbol in SYMBOLS:
                df = exchange.fetch_ohlcv(symbol, TF_ENTRY, limit=200)
                df = pd.DataFrame(df, columns=['ts','open','high','low','close','volume'])
                
                # Sabhi strategies ka execution
                if strat_sweep(df, symbol): print(f"[{symbol}] SWEEP Detected")
                if strat_ema_pullback(df, symbol): print(f"[{symbol}] EMA Pullback Detected")
                if strat_breakout(df, symbol): print(f"[{symbol}] Breakout Detected")
                if strat_day_high_low(df, symbol): print(f"[{symbol}] Day High Breakout Detected")
            
            time.sleep(60) # GitHub ke liye optimized time
        except Exception as e:
            print(f"[ERR] {e}")
            time.sleep(30)

if __name__ == '__main__':
    run()
    
