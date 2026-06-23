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

NEWS_TIMES_UTC = [(8,30),(14,0),(14,30),(18,0)]
NEWS_BLOCK_MIN = 30

try:
    exchange = ccxt.mexc({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    print("[OK] Exchange (MEXC) connected")
except Exception as e:
    print(f"[FATAL] Exchange error: {e}")
    exit(1)

active_trades = {}

def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        headers = {"Title": title, "Priority": "high", "Tags": tags}
        r = requests.post(NTFY_URL, data=msg.encode('utf-8'), headers=headers, timeout=10)
        print(f"[NOTIF] {title} — status:{r.status_code}")
    except Exception as e:
        print(f"[NOTIF ERROR] {e}")

def is_news_time():
    now = datetime.now(timezone.utc)
    for (h, m) in NEWS_TIMES_UTC:
        diff = abs((now.hour * 60 + now.minute) - (h * 60 + m))
        if diff <= NEWS_BLOCK_MIN:
            return True
    return False

def is_good_session():
    h = datetime.now(timezone.utc).hour
    return (8 <= h < 11) or (13 <= h < 16)

def wait_for_candle_close():
    now     = datetime.now(timezone.utc)
    seconds = now.minute * 60 + now.second
    rem     = 900 - (seconds % 900)
    time.sleep(rem + 2)

def get_df(symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        return df
    except Exception as e:
        return None

def ema(df, p):
    return df['close'].ewm(span=p, adjust=False).mean()

def atr(df, p=14):
    hl  = df['high'] - df['low']
    hpc = abs(df['high'] - df['close'].shift(1))
    lpc = abs(df['low']  - df['close'].shift(1))
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

def adx_val(df, p=14):
    try:
        df       = df.copy()
        df['hd'] = df['high'].diff()
        df['ld'] = df['low'].diff()
        df['pdm']= np.where((df['hd'] > df['ld']) & (df['hd'] > 0), df['hd'], 0)
        df['mdm']= np.where((df['ld'] > df['hd']) & (df['ld'] > 0), df['ld'], 0)
        df['atr']= atr(df, p)
        pdi      = 100 * (df['pdm'].rolling(p).mean() / (df['atr'] + 1e-10))
        mdi      = 100 * (df['mdm'].rolling(p).mean() / (df['atr'] + 1e-10))
        dx       = 100 * abs(pdi - mdi) / (pdi + mdi + 1e-10)
        return dx.rolling(p).mean().iloc[-1]
    except: return 20

def is_uptrend(symbol):
    df = get_df(symbol, TF_TREND, 210)
    if df is None: return False
    return df['close'].iloc[-1] > ema(df, 200).iloc[-1]

def market_type(df):
    adx = adx_val(df)
    return "TRENDING" if adx > 25 else "RANGING"

def calc_targets(entry, atr_v, rr1=2.5, rr2=4.5, sl_m=1.5):
    sl   = entry - atr_v * sl_m
    risk = entry - sl
    tp1  = entry + risk * rr1
    tp2  = entry + risk * rr2
    return sl, tp1, tp2

def send_signal(symbol, strategy, market, entry, sl, tp1, tp2):
    rr  = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    msg = f"Entry: {entry:.4f}\nTP1: {tp1:.4f}\nSL: {sl:.4f}\nRR: 1:{rr}\nStrategy: {strategy}"
    notify(f"BUY | {strategy}", msg)
    active_trades[symbol] = {'entry': entry, 'tp1': tp1, 'tp2': tp2, 'sl': sl}

def strat_daily_breakout(df, symbol):
    try:
        daily_df = exchange.fetch_ohlcv(symbol, '1d', limit=2)
        prev_high = daily_df[0][2]
        c = df.iloc[-1]
        if c['close'] > prev_high:
            sl, tp1, tp2 = calc_targets(c['close'], atr(df).iloc[-1], 3.5, 5.5)
            send_signal(symbol, "DAILY BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
    except: pass

def strat_sweep(df, symbol):
    try:
        c, p = df.iloc[-1], df.iloc[-2]
        swing = df['low'].iloc[-20:-1].min()
        if p['low'] < swing and c['close'] > swing and c['close'] > c['open']:
            sl, tp1, tp2 = calc_targets(c['close'], atr(df).iloc[-1], 2.5, 4.5)
            send_signal(symbol, "SWEEP", "RANGING", c['close'], sl, tp1, tp2)
    except: pass

def strat_ema_pullback(df, symbol):
    try:
        c = df.iloc[-1]
        if c['close'] > ema(df, 50).iloc[-1] and c['close'] > ema(df, 200).iloc[-1]:
            sl, tp1, tp2 = calc_targets(c['close'], atr(df).iloc[-1], 3.0, 5.0)
            send_signal(symbol, "EMA PULLBACK", "TRENDING", c['close'], sl, tp1, tp2)
    except: pass

def strat_breakout(df, symbol):
    try:
        resist = df['high'].iloc[-20:-2].max()
        c, p = df.iloc[-1], df.iloc[-2]
        if p['close'] > resist and c['close'] > resist:
            sl, tp1, tp2 = calc_targets(c['close'], atr(df).iloc[-1], 3.5, 5.5)
            send_signal(symbol, "BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
    except: pass

def monitor(df, symbol):
    if symbol not in active_trades: return
    t, c = active_trades[symbol], df.iloc[-1]
    if c['high'] >= t['tp2']: active_trades.pop(symbol, None)
    elif c['low'] <= t['sl']: active_trades.pop(symbol, None)

def run():
    while True:
        try:
            for symbol in SYMBOLS:
                df15 = get_df(symbol, TF_ENTRY)
                if df15 is None: continue
                if symbol in active_trades: monitor(df15, symbol); continue
                
                uptrend = is_uptrend(symbol)
                if uptrend:
                    strat_daily_breakout(df15, symbol)
                    if symbol not in active_trades: strat_breakout(df15, symbol)
                    if symbol not in active_trades: strat_ema_pullback(df15, symbol)
                    if symbol not in active_trades: strat_sweep(df15, symbol)
                else:
                    strat_sweep(df15, symbol)
            wait_for_candle_close()
        except: time.sleep(60)

if __name__ == '__main__':
    run()
    
