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
    print(f"[WAIT] Next candle in {rem//60}m {rem%60}s")
    time.sleep(rem + 2)

def get_df(symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        print(f"[DATA] {symbol} {timeframe} — {len(df)} candles ok")
        return df
    except Exception as e:
        print(f"[FETCH ERROR] {symbol} {timeframe}: {e}")
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
    except:
        return 20

def is_uptrend(symbol):
    df = get_df(symbol, TF_TREND, 210)
    if df is None:
        return False
    result = df['close'].iloc[-1] > ema(df, 200).iloc[-1]
    print(f"[TREND] {symbol} uptrend: {result}")
    return result

def market_type(df):
    adx = adx_val(df)
    mkt = "TRENDING" if adx > 25 else "RANGING"
    print(f"[ADX] {adx:.1f} — {mkt}")
    return mkt

def calc_targets(entry, atr_v, rr1=2.5, rr2=4.5, sl_m=1.5):
    sl   = entry - atr_v * sl_m
    risk = entry - sl
    tp1  = entry + risk * rr1
    tp2  = entry + risk * rr2
    return sl, tp1, tp2

def send_signal(symbol, strategy, market, entry, sl, tp1, tp2):
    rr  = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    msg = (
        f"Coin: {symbol}\n"
        f"Market: {market}\n"
        f"Entry:  {entry:.4f}\n"
        f"TP1:    {tp1:.4f}  (+{((tp1-entry)/entry*100):.1f}%)\n"
        f"TP2:    {tp2:.4f}  (+{((tp2-entry)/entry*100):.1f}%)\n"
        f"SL:     {sl:.4f}   (-{((entry-sl)/entry*100):.1f}%)\n"
        f"RR:     1:{rr}\n"
        f"Strategy: {strategy}"
    )
    notify(f"BUY | {strategy}", msg)
    active_trades[symbol] = {'entry': entry, 'tp1': tp1, 'tp2': tp2, 'sl': sl}

# --- ADDED: DAILY BREAKOUT STRATEGY ---
def strat_daily_breakout(df, symbol):
    try:
        daily_df = exchange.fetch_ohlcv(symbol, '1d', limit=2)
        prev_high = daily_df[0][2]
        c = df.iloc[-1]
        atr_v = atr(df).iloc[-1]
        if c['close'] > prev_high:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.5, 5.5)
            send_signal(symbol, "DAILY BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[DAILY BREAKOUT ERR] {e}")

def strat_sweep(df, symbol):
    try:
        c      = df.iloc[-1]
        p      = df.iloc[-2]
        swing  = df['low'].iloc[-20:-1].min()
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        atr_v  = atr(df).iloc[-1]
        swept  = p['low'] < swing and c['close'] > swing
        bull   = c['close'] > c['open']
        vol_ok = c['volume'] > vol_ma * 1.2
        print(f"[SWEEP] swept:{swept} bull:{bull} vol:{vol_ok}")
        if swept and bull and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.5, 4.5)
            send_signal(symbol, "SWEEP", "RANGING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[SWEEP ERR] {e}")

def strat_fvg(df, symbol):
    try:
        atr_v  = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        found  = False
        for i in range(3, 10):
            c1h = df['high'].iloc[-i]
            c3l = df['low'].iloc[-i+2]
            c   = df.iloc[-1]
            if c3l > c1h:
                in_gap = c1h <= c['close'] <= c3l
                bull   = c['close'] > c['open']
                vol_ok = c['volume'] > vol_ma
                if in_gap and bull and vol_ok:
                    sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.0, 3.5)
                    send_signal(symbol, "FVG", "RANGING", c['close'], sl, tp1, tp2)
                    found = True
                    break
        print(f"[FVG] found:{found}")
    except Exception as e:
        print(f"[FVG ERR] {e}")

def strat_ema_pullback(df, symbol):
    try:
        df         = df.copy()
        df['e20']  = ema(df, 20)
        df['e50']  = ema(df, 50)
        df['e200'] = ema(df, 200)
        atr_v      = atr(df).iloc[-1]
        vol_ma     = df['volume'].rolling(20).mean().iloc[-1]
        c = df.iloc[-1]
        p = df.iloc[-2]
        trend   = c['e50'] > c['e200']
        touched = p['low'] <= p['e20'] * 1.002
        bounced = c['close'] > c['e20'] and c['close'] > c['open']
        vol_ok  = c['volume'] > vol_ma
        print(f"[EMA] trend:{trend} touch:{touched} bounce:{bounced} vol:{vol_ok}")
        if trend and touched and bounced and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(symbol, "EMA PULLBACK", "TRENDING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[EMA ERR] {e}")

def strat_breakout(df, symbol):
    try:
        atr_v  = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        resist = df['high'].iloc[-20:-2].max()
        c = df.iloc[-1]
        p = df.iloc[-2]
        broke    = p['close'] > resist and p['volume'] > vol_ma * 1.5
        retested = c['low'] <= resist * 1.002 and c['close'] > resist
        bull     = c['close'] > c['open']
        print(f"[BREAK] broke:{broke} retest:{retested} bull:{bull}")
        if broke and retested and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.5, 5.5)
            send_signal(symbol, "BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[BREAKOUT ERR] {e}")

def monitor(df, symbol):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    if c['high'] >= t['tp2']:
        notify("TP2 HIT!", f"{symbol}\nFull target!\nTP2: {t['tp2']:.4f}", tags="trophy,fire")
        active_trades.pop(symbol, None)
    elif c['high'] >= t['tp1']:
        notify("TP1 HIT!", f"{symbol}\nTP1: {t['tp1']:.4f} hit!\nMove SL to entry!", tags="money_bag")
    elif c['low'] <= t['sl']:
        notify("SL HIT", f"{symbol}\nSL: {t['sl']:.4f}", tags="red_circle")
        active_trades.pop(symbol, None)

last_report = time.time()
scan_count  = 0

def hourly_report():
    global last_report, scan_count
    if time.time() - last_report >= 3600:
        active = list(active_trades.keys()) or ["None"]
        notify("Alhamdulillah — Bot Active",
            f"Scans: {scan_count}\n"
            f"Active: {', '.join(active)}\n"
            f"Session: {'ON' if is_good_session() else 'OFF'}\n"
            f"News: {'BLOCKED' if is_news_time() else 'CLEAR'}",
            tags="robot,white_check_mark")
        last_report = time.time()
        scan_count  = 0

def run():
    global scan_count
    start_time = time.time()
    print("[START] Bot starting...")
    notify("Bot Started", "Crypto Spot Bot Live!\nBTC ETH SOL AVAX\n100% Halal Spot Only")
    print("[START] Startup notification sent!")

    while True:
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr done — restarting now", tags="arrows_counterclockwise")
            print("[EXIT] Time limit reached")
            break

        try:
            hourly_report()
            now = datetime.now(timezone.utc)
            print(f"\n[TIME] {now.strftime('%d-%b %H:%M')} UTC")

            if is_news_time():
                print("[SKIP] News time — 10min wait")
                time.sleep(600)
                continue

            if not is_good_session():
                print(f"[SKIP] Outside London/NY session")
                wait_for_candle_close()
                continue

            print("[SESSION] Active — waiting for candle close...")
            wait_for_candle_close()

            now = datetime.now(timezone.utc)
            print(f"\n{'='*45}")
            print(f"[SCAN #{scan_count+1}] {now.strftime('%d-%b %H:%M')} UTC")
            print(f"{'='*45}")

            for symbol in SYMBOLS:
                try:
                    print(f"\n--- {symbol} ---")
                    df15 = get_df(symbol, TF_ENTRY)
                    if df15 is None or df15.empty:
                        print(f"[{symbol}] No data — skip")
                        continue

                    if symbol in active_trades:
                        monitor(df15, symbol)
                        print(f"[{symbol}] Monitoring active trade")
                        continue

                    # --- UPDATED: EMA 200 FILTER + STRATEGY SELECTOR ---
                    uptrend = is_uptrend(symbol)
                    
                    if uptrend:
                        # Trend hai: Sab chalayenge (Daily Breakout priority par)
                        strat_daily_breakout(df15, symbol)
                        time.sleep(1)
                        if symbol not in active_trades: strat_breakout(df15, symbol)
                        time.sleep(1)
                        if symbol not in active_trades: strat_ema_pullback(df15, symbol)
                        time.sleep(1)
                        if symbol not in active_trades: strat_sweep(df15, symbol)
                    else:
                        # Trend nahi hai: Sirf Sweep allowed
                        strat_sweep(df15, symbol)
                    
                    time.sleep(1)

                except Exception as e:
                    print(f"[ERR] {symbol}: {e}")
                    continue

            scan_count += 1
            print(f"\n[✓] Scan #{scan_count} done!")
            print(f"[→] Next: waiting for 15min candle close")

        except Exception as e:
            print(f"[MAIN ERR] {e}")
            time.sleep(60)
            continue

if __name__ == '__main__':
    run()
    
