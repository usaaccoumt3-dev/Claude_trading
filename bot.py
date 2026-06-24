import time
import requests
import ccxt
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timezone

# --- GLOBAL CONFIGURATION ---
NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
EXCHANGES = ['mexc', 'binance', 'bybit']
TF_ENTRY = '15m'
LOG_FILE = "bot_performance_full_record.json"

# --- STRATEGY CONTROLLER (THE GENIUS CORE) ---
class StrategyController:
    def __init__(self):
        # Bot khud in values ko change karega agar performance achi nahi rahi
        self.params = {
            "DAILY_BREAKOUT": {"sl_multiplier": 1.5, "atr_lookback": 14},
            "SWEEP": {"sl_multiplier": 1.2, "atr_lookback": 14},
            "EMA_PULLBACK": {"sl_multiplier": 1.5, "ema_period": 20},
            "BREAKOUT": {"sl_multiplier": 1.4, "atr_lookback": 14}
        }
        self.stats = {strat: {"wins": 0, "losses": 0} for strat in self.params}

    def adjust_strategy(self, strategy_name):
        """Agar loss zyada ho, to bot strategy ki sensitivity badha dega"""
        s = self.stats[strategy_name]
        if s["losses"] > s["wins"]:
            # Bot parameters ko adjust kar raha hai (Self-Improvement)
            self.params[strategy_name]["sl_multiplier"] += 0.1
            print(f"[IMPROVING] Strategy {strategy_name} params adjusted to avoid SL!")

    def record_result(self, strategy_name, result):
        if result == "WIN": self.stats[strategy_name]["wins"] += 1
        else: 
            self.stats[strategy_name]["losses"] += 1
            self.adjust_strategy(strategy_name)

# --- PERFORMANCE LOGGER ---
def save_performance_record(record):
    data = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            try: data = json.load(f)
            except: data = []
    data.append(record)
    with open(LOG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- DATA & INDICATORS ---
def get_exchange_data(symbol, timeframe):
    for ex in EXCHANGES:
        try:
            ex_obj = getattr(ccxt, ex)({'enableRateLimit': True})
            ohlcv = ex_obj.fetch_ohlcv(symbol, timeframe, limit=200)
            return pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        except: continue
    return None

def get_atr(df, period=14):
    df['hl'] = df['h'] - df['l']
    return df['hl'].rolling(period).mean()

# --- STRATEGIES ---
def execute_smart_trade(symbol, strategy_name, controller, df):
    p = controller.params[strategy_name]
    atr_val = get_atr(df).iloc[-1]
    entry = df['c'].iloc[-1]
    
    # Dynamic SL based on controller params
    sl = entry - (atr_val * p["sl_multiplier"])
    tp = entry + (atr_val * (p["sl_multiplier"] * 3))
    
    active_trades[symbol] = {'entry': entry, 'tp': tp, 'sl': sl, 'strat': strategy_name}
    requests.post(NTFY_URL, data=f"Strategy: {strategy_name}".encode(), headers={"Title": f"BUY | {symbol}"})

# --- MASTER ENGINE ---
active_trades = {}
def run_bot():
    controller = StrategyController()
    while True:
        try:
            for symbol in SYMBOLS:
                df = get_exchange_data(symbol, TF_ENTRY)
                if df is None: continue
                
                if symbol in active_trades:
                    t = active_trades[symbol]
                    if df['c'].iloc[-1] >= t['tp']:
                        controller.record_result(t['strat'], "WIN")
                        active_trades.pop(symbol)
                    elif df['c'].iloc[-1] <= t['sl']:
                        controller.record_result(t['strat'], "LOSS")
                        active_trades.pop(symbol)
                else:
                    # Bot har baar controller.params use karega jo dynamically update hote hain
                    # Logic here...
                    pass 
            time.sleep(900)
        except: time.sleep(60)

if __name__ == '__main__':
    run_bot()
    
