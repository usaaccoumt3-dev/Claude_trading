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
SYMBOLS  = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
EXCHANGES = ['mexc', 'binance', 'bybit']
TF_ENTRY = '15m'
LOG_FILE = "bot_performance_full_record.json"

active_trades = {}

# --- PERFORMANCE LOGGER (GENIUS MEMORY) ---
def save_performance_record(record):
    data = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            try: data = json.load(f)
            except: data = []
    data.append(record)
    with open(LOG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- SMART DATA FETCHING (FAILOVER) ---
def get_exchange_data(symbol, timeframe):
    for ex_name in EXCHANGES:
        try:
            exchange = getattr(ccxt, ex_name)({'enableRateLimit': True})
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=200)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception:
            continue
    return None

# --- INDICATOR CALCULATIONS ---
def get_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def get_atr(df, period=14):
    df['h-l'] = df['high'] - df['low']
    df['h-pc'] = abs(df['high'] - df['close'].shift(1))
    df['l-pc'] = abs(df['low'] - df['close'].shift(1))
    tr = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)
    return tr.rolling(period).mean()

# --- SMART SCORING SYSTEM (QUALITY HUNTER) ---
def get_trade_quality_score(df):
    score = 0
    # Volume Check
    avg_vol = df['volume'].rolling(20).mean().iloc[-1]
    if df['volume'].iloc[-1] > avg_vol * 1.5: score += 3
    # Trend alignment check
    if df['close'].iloc[-1] > get_ema(df, 200).iloc[-1]: score += 3
    # Volatility Check
    if get_atr(df).iloc[-1] > df['close'].iloc[-1] * 0.0005: score += 2
    # Momentum Check
    if df['close'].iloc[-1] > df['open'].iloc[-1]: score += 2
    return score

# --- STRATEGIES ---
def check_daily_breakout(df, symbol):
    daily_df = get_exchange_data(symbol, '1d')
    if daily_df is None: return
    prev_high = daily_df.iloc[-2]['high']
    if df['close'].iloc[-1] > prev_high and get_trade_quality_score(df) >= 7:
        execute_trade(symbol, "DAILY_BREAKOUT", df['close'].iloc[-1])

def check_sweep(df, symbol):
    swing_low = df['low'].iloc[-20:-1].min()
    if df['low'].iloc[-2] < swing_low and df['close'].iloc[-1] > swing_low and get_trade_quality_score(df) >= 7:
        execute_trade(symbol, "SWEEP", df['close'].iloc[-1])

def check_ema_pullback(df, symbol):
    df['e20'] = get_ema(df, 20)
    df['e50'] = get_ema(df, 50)
    if df['e50'].iloc[-1] > get_ema(df, 200).iloc[-1] and df['close'].iloc[-1] > df['e20'].iloc[-1] and get_trade_quality_score(df) >= 7:
        execute_trade(symbol, "EMA_PULLBACK", df['close'].iloc[-1])

def check_breakout(df, symbol):
    res = df['high'].iloc[-20:-2].max()
    if df['close'].iloc[-1] > res and get_trade_quality_score(df) >= 7:
        execute_trade(symbol, "BREAKOUT", df['close'].iloc[-1])

# --- TRADE EXECUTION & MANAGEMENT ---
def execute_trade(symbol, strategy, entry):
    if symbol in active_trades: return
    atr_val = get_atr(df_global).iloc[-1]
    sl = entry - (atr_val * 1.5)
    tp1 = entry + (atr_val * 4.5)
    
    active_trades[symbol] = {'entry': entry, 'tp': tp1, 'sl': sl, 'strat': strategy}
    msg = f"Entry: {entry}\nTP: {tp1}\nSL: {sl}\nStrat: {strategy}"
    requests.post(NTFY_URL, data=msg.encode('utf-8'), headers={"Title": f"BUY | {symbol}"})

def monitor_trades(df, symbol):
    t = active_trades[symbol]
    curr = df['close'].iloc[-1]
    if curr >= t['tp']:
        save_performance_record({"symbol": symbol, "result": "WIN", "profit": "High RR", "strat": t['strat']})
        active_trades.pop(symbol)
    elif curr <= t['sl']:
        save_performance_record({"symbol": symbol, "result": "LOSS", "profit": "SL Hit", "strat": t['strat']})
        active_trades.pop(symbol)

# --- MAIN ENGINE ---
def run_bot():
    print("Genius Trading Bot Started...")
    while True:
        try:
            for symbol in SYMBOLS:
                global df_global
                df_global = get_exchange_data(symbol, TF_ENTRY)
                if df_global is None: continue
                
                if symbol in active_trades:
                    monitor_trades(df_global, symbol)
                else:
                    check_daily_breakout(df_global, symbol)
                    check_sweep(df_global, symbol)
                    check_ema_pullback(df_global, symbol)
                    check_breakout(df_global, symbol)
            time.sleep(900)
        except: time.sleep(60)

if __name__ == '__main__':
    run_bot()
                
