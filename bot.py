import time
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

NTFY_URL  = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS   = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
TF_ENTRY  = '15m'
TF_TREND  = '1h'

NEWS_TIMES_UTC = [(8,30),(14,0),(14,30),(18,0)]
NEWS_BLOCK_MIN = 30

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

active_trades = {}

def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        requests.post(NTFY_URL, data=msg.encode('utf-8'),
            headers={"Title": title, "Priority": "high", "Tags": tags})
        print(f"[NOTIF] {title}")
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
    print(f"[WAIT] Next 15min candle closes in {rem//60}m {rem%60}s")
    time.sleep(rem + 2)

def get_df(symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        return df
    except Exception as e:
        print(f"[FETCH ERROR] {symbol}: {e}")
        return None

def ema(df, p):
    return df['close'].ewm(span=p, adjust=False).mean()

def atr(df, p=14):
    hl  = df['high'] - df['low']
    hpc = abs(df['high'] - df['close'].shift(1))
    lpc = abs(df['low']  - df['close'].shift(1))
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

def adx_val(df, p=14):
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

def is_uptrend(symbol):
    df = get_df(symbol, TF_TREND, 210)
    if df is None:
        return False
    return df['close'].iloc[-1] > ema(df, 200).iloc[-1]

def market_type(df):
    return "TRENDING" if adx_val(df) > 25 else "RANGING"

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
    notify(f"🟢 BUY | {strategy}", msg)
    active_trades[symbol] = {'entry': entry, 'tp1': tp1, 'tp2': tp2, 'sl': sl}

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
        if swept and bull and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.5, 4.5)
            send_signal(symbol, "SWEEP", "RANGING", c['close'], sl, tp1, tp2)
            print(f"[{symbol}] SWEEP signal!")
        else:
            print(f"[{symbol}] SWEEP: no setup")
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
                    print(f"[{symbol}] FVG signal!")
                    found = True
                    break
        if not found:
            print(f"[{symbol}] FVG: no setup")
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
        if trend and touched and bounced and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(symbol, "EMA PULLBACK", "TRENDING", c['close'], sl, tp1, tp2)
            print(f"[{symbol}] EMA PULLBACK signal!")
        else:
            print(f"[{symbol}] EMA: no setup")
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
        if broke and retested and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.5, 5.5)
            send_signal(symbol, "BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
            print(f"[{symbol}] BREAKOUT signal!")
        else:
            print(f"[{symbol}] BREAKOUT: no setup")
    except Exception as e:
        print(f"[BREAKOUT ERR] {e}")

def monitor(df, symbol):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    if c['high'] >= t['tp2']:
        notify("🎯 TP2 HIT!", f"{symbol}\nFull target!\nEntry: {t['entry']:.4f}\nTP2: {t['tp2']:.4f}", tags="trophy,fire")
        active_trades.pop(symbol, None)
    elif c['high'] >= t['tp1']:
        notify("💰 TP1 HIT!", f"{symbol}\nTP1: {t['tp1']:.4f} hit!\nSL entry pe le aao!", tags="money_bag")
    elif c['low'] <= t['sl']:
        notify("🔴 SL HIT", f"{symbol}\nSL: {t['sl']:.4f} hit", tags="red_circle")
        active_trades.pop(symbol, None)

last_report = time.time()
scan_count  = 0

def hourly_report():
    global last_report, scan_count
    if time.time() - last_report >= 3600:
        active = list(active_trades.keys()) or ["None"]
        notify("🤖 Bot Active — Alhamdulillah",
            f"Scans this hour: {scan_count}\n"
            f"Active trades: {', '.join(active)}\n"
            f"Session: {'ACTIVE' if is_good_session() else 'WAITING'}\n"
            f"News block: {'YES' if is_news_time() else 'NO'}",
            tags="robot,white_check_mark")
        last_report = time.time()
        scan_count  = 0

def run():
    global scan_count
    start_time = time.time()
    notify("🚀 Bot Started", "Crypto Spot Bot Live!\nBTC ETH SOL AVAX\n100% Halal", tags="rocket")
    print("[START] Bot started!")

    while True:
        if time.time() - start_time > 19800:
            notify("🔄 Restarting", "5.5hr complete — restarting", tags="arrows_counterclockwise")
            print("[EXIT] Restarting...")
            break

        try:
            hourly_report()

            if is_news_time():
                now = datetime.now(timezone.utc)
                print(f"[SKIP] News time {now.strftime('%H:%M')} UTC")
                time.sleep(600)
                continue

            if not is_good_session():
                now = datetime.now(timezone.utc)
                print(f"[SKIP] Outside session {now.strftime('%H:%M')} UTC")
                wait_for_candle_close()
                continue

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
                        print(f"[{symbol}] No data")
                        continue

                    if symbol in active_trades:
                        monitor(df15, symbol)
                        print(f"[{symbol}] Trade active — monitoring")
                        continue

                    if not is_uptrend(symbol):
                        print(f"[{symbol}] Below EMA200 — skip")
                        continue

                    mkt = market_type(df15)
                    print(f"[{symbol}] Market: {mkt}")

                    if mkt == "RANGING":
                        strat_sweep(df15, symbol)
                        time.sleep(1)
                        if symbol not in active_trades:
                            strat_fvg(df15, symbol)
                    else:
                        strat_ema_pullback(df15, symbol)
                        time.sleep(1)
                        if symbol not in active_trades:
                            strat_breakout(df15, symbol)

                    time.sleep(1)

                except Exception as e:
                    print(f"[ERR] {symbol}: {e}")

            scan_count += 1
            print(f"\n[✓] Scan #{scan_count} complete!")
            print(f"[→] Waiting for next 15min candle...")

        except Exception as e:
            print(f"[MAIN ERR] {e}")
            time.sleep(60)

if __name__ == '__main__':
    run()
