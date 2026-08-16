import time
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ═══════════════════════════════════
# CONFIG
# ═══════════════════════════════════
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

# ═══════════════════════════════════
# NOTIFY
# ═══════════════════════════════════
def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        requests.post(
            NTFY_URL,
            data=msg.encode('utf-8'),
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": tags,
                "Content-Type": "text/plain; charset=utf-8"
            },
            timeout=10)
        print(f"[NOTIF] {title}")
    except Exception as e:
        print(f"[NOTIF ERR] {e}")
        
# ═══════════════════════════════════
# NEWS FILTER — high impact only
# ═══════════════════════════════════
def is_news_time():
    now = datetime.now(timezone.utc)
    cur = now.hour * 60 + now.minute
    for (h, m) in NEWS_TIMES_UTC:
        if abs(cur - (h * 60 + m)) <= NEWS_BLOCK_MIN:
            return True
    return False

# ═══════════════════════════════════
# CANDLE CLOSE WAIT
# ═══════════════════════════════════
def wait_for_candle_close():
    now     = datetime.now(timezone.utc)
    seconds = now.minute * 60 + now.second
    rem     = 900 - (seconds % 900)
    print(f"[WAIT] Next candle in {rem//60}m {rem%60}s")
    time.sleep(rem + 2)

# ═══════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════
def get_df(symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        return df
    except Exception as e:
        print(f"[FETCH ERR] {symbol}: {e}")
        return None

# ═══════════════════════════════════
# INDICATORS
# ═══════════════════════════════════
def ema(df, p):
    return df['close'].ewm(span=p, adjust=False).mean()

def atr(df, p=14):
    hl  = df['high'] - df['low']
    hpc = abs(df['high'] - df['close'].shift(1))
    lpc = abs(df['low']  - df['close'].shift(1))
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

def is_uptrend(symbol):
    df = get_df(symbol, TF_TREND, 210)
    if df is None:
        return False
    result = df['close'].iloc[-1] > ema(df, 200).iloc[-1]
    print(f"[TREND] {symbol}: {'UP' if result else 'DOWN'}")
    return result

# ═══════════════════════════════════
# SL / TP CALCULATOR
# ═══════════════════════════════════
def calc_targets(entry, atr_v, rr1=2.0, rr2=3.0, sl_mult=1.5):
    sl   = entry - atr_v * sl_mult
    risk = entry - sl
    tp1  = entry + risk * rr1
    tp2  = entry + risk * rr2
    return sl, tp1, tp2

# ═══════════════════════════════════
# SIGNAL SENDER
# ═══════════════════════════════════
def send_signal(symbol, strategy, entry, sl, tp1, tp2):
    if symbol in active_trades:
        print(f"[SKIP] {symbol} already in trade")
        return
    rr  = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    now = datetime.now(timezone.utc)
    msg = (
        f"Coin: {symbol}\n"
        f"Strategy: {strategy}\n"
        f"Time: {now.strftime('%H:%M')} UTC\n"
        f"Entry:  {entry:.4f}\n"
        f"TP1:    {tp1:.4f}  (+{((tp1-entry)/entry*100):.2f}%)\n"
        f"TP2:    {tp2:.4f}  (+{((tp2-entry)/entry*100):.2f}%)\n"
        f"SL:     {sl:.4f}   (-{((entry-sl)/entry*100):.2f}%)\n"
        f"RR:     1:{rr}"
    )
    notify(f"🟢 BUY | {strategy}", msg)
    active_trades[symbol] = {
        'entry': entry, 'tp1': tp1,
        'tp2': tp2, 'sl': sl,
        'strategy': strategy
    }
    print(f"[SIGNAL] {symbol} {strategy} entry:{entry:.4f}")

# ═══════════════════════════════════
# STRATEGY 1 — SWEEP
# Rules: prev low swept + bullish close + volume
# ═══════════════════════════════════
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

        print(f"[SWEEP] {symbol} swept:{swept} bull:{bull} vol:{vol_ok}")

        if swept and bull and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.0, 3.5)
            send_signal(symbol, "SWEEP", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[SWEEP ERR] {e}")

# ═══════════════════════════════════
# STRATEGY 2 — FVG
# Rules: 3-candle gap + price filling + bullish
# ═══════════════════════════════════
def strat_fvg(df, symbol):
    try:
        atr_v  = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        found  = False

        for i in range(3, 15):
            if -i+2 >= 0:
                continue
            c1h = df['high'].iloc[-i]
            c3l = df['low'].iloc[-i+2]
            c   = df.iloc[-1]

            if c3l > c1h:
                in_gap = c1h <= c['close'] <= c3l
                bull   = c['close'] > c['open']
                vol_ok = c['volume'] > vol_ma

                if in_gap and bull and vol_ok:
                    sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.0, 3.5)
                    send_signal(symbol, "FVG", c['close'], sl, tp1, tp2)
                    found = True
                    break

        print(f"[FVG] {symbol} found:{found}")
    except Exception as e:
        print(f"[FVG ERR] {e}")

# ═══════════════════════════════════
# STRATEGY 3 — EMA PULLBACK
# Rules: EMA50>EMA200 + price touches EMA20 + bounce
# ═══════════════════════════════════
def strat_ema_pullback(df, symbol):
    try:
        df2        = df.copy()
        df2['e20'] = ema(df2, 20)
        df2['e50'] = ema(df2, 50)
        df2['e200']= ema(df2, 200)
        atr_v      = atr(df2).iloc[-1]
        vol_ma     = df2['volume'].rolling(20).mean().iloc[-1]
        c = df2.iloc[-1]
        p = df2.iloc[-2]

        trend   = c['e50'] > c['e200']
        touched = p['low'] <= p['e20'] * 1.002
        bounced = c['close'] > c['e20'] and c['close'] > c['open']
        vol_ok  = c['volume'] > vol_ma

        print(f"[EMA] {symbol} trend:{trend} touch:{touched} bounce:{bounced} vol:{vol_ok}")

        if trend and touched and bounced and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.5, 4.0)
            send_signal(symbol, "EMA PULLBACK", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[EMA ERR] {e}")

# ═══════════════════════════════════
# STRATEGY 4 — BREAKOUT
# Rules: resistance break + retest + high volume
# ═══════════════════════════════════
def strat_breakout(df, symbol):
    try:
        atr_v  = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        resist = df['high'].iloc[-20:-2].max()
        c      = df.iloc[-1]
        p      = df.iloc[-2]

        broke    = p['close'] > resist and p['volume'] > vol_ma * 1.5
        retested = c['low'] <= resist * 1.002 and c['close'] > resist
        bull     = c['close'] > c['open']

        print(f"[BREAK] {symbol} broke:{broke} retest:{retested} bull:{bull}")

        if broke and retested and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(symbol, "BREAKOUT", c['close'], sl, tp1, tp2)
    except Exception as e:
        print(f"[BREAK ERR] {e}")

# ═══════════════════════════════════
# TRADE MONITOR — TP / SL check
# ═══════════════════════════════════
def monitor(df, symbol):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]

    print(f"[MON] {symbol} H:{c['high']:.4f} L:{c['low']:.4f} TP1:{t['tp1']:.4f} SL:{t['sl']:.4f}")

    if c['high'] >= t['tp2']:
        notify("🎯 TP2 HIT!",
            f"{symbol}\nStrategy: {t['strategy']}\nFull target!\nEntry: {t['entry']:.4f}\nTP2: {t['tp2']:.4f}",
            tags="trophy,fire")
        active_trades.pop(symbol, None)

    elif c['high'] >= t['tp1']:
        notify("💰 TP1 HIT!",
            f"{symbol}\nStrategy: {t['strategy']}\nTP1: {t['tp1']:.4f}\nMove SL to entry!",
            tags="money_bag")

    elif c['low'] <= t['sl']:
        notify("🔴 SL HIT",
            f"{symbol}\nStrategy: {t['strategy']}\nSL: {t['sl']:.4f}",
            tags="red_circle")
        active_trades.pop(symbol, None)

# ═══════════════════════════════════
# HOURLY REPORT
# ═══════════════════════════════════
last_report = time.time()
scan_count  = 0

def hourly_report():
    global last_report, scan_count
    if time.time() - last_report < 3600:
        return
    active = list(active_trades.keys()) or ["None"]
    notify("🤖 Bot Active — Alhamdulillah",
        f"Scans this hour: {scan_count}\n"
        f"Active trades: {', '.join(active)}\n"
        f"News block: {'YES' if is_news_time() else 'NO'}",
        tags="robot,white_check_mark")
    last_report = time.time()
    scan_count  = 0

# ═══════════════════════════════════
# MAIN LOOP — 24/7, no session filter
# ═══════════════════════════════════
def run():
    global scan_count
    start_time = time.time()

    notify("🚀 Bot Started",
        "Crypto Spot Bot Live!\nBTC ETH SOL AVAX\n24/7 Mode\n4 Strategies Active\n100% Halal",
        tags="rocket")
    print("[START] Bot started — 24/7 mode!")

    while True:
        # 5.5 hour restart for GitHub Actions
        if time.time() - start_time > 19800:
            notify("🔄 Restarting", "5.5hr complete — restarting", tags="arrows_counterclockwise")
            print("[EXIT] Restarting...")
            break

        try:
            hourly_report()

            # News block — skip high impact news
            if is_news_time():
                now = datetime.now(timezone.utc)
                print(f"[SKIP] News time {now.strftime('%H:%M')} UTC — 10min wait")
                time.sleep(600)
                continue

            # Wait for 15min candle close
            wait_for_candle_close()

            now = datetime.now(timezone.utc)
            print(f"\n{'='*45}")
            print(f"[SCAN #{scan_count+1}] {now.strftime('%d-%b %H:%M')} UTC")
            print(f"{'='*45}")

            for symbol in SYMBOLS:
                print(f"\n--- {symbol} ---")

                df15 = get_df(symbol, TF_ENTRY)
                if df15 is None or df15.empty:
                    print(f"[{symbol}] No data — skip")
                    continue

                # Monitor active trade first
                if symbol in active_trades:
                    monitor(df15, symbol)
                    print(f"[{symbol}] Trade active — monitoring only")
                    continue

                # Uptrend filter — EMA 200
                if not is_uptrend(symbol):
                    print(f"[{symbol}] Below EMA200 — skip")
                    continue

                # Run ALL 4 strategies — first signal wins
                strat_sweep(df15, symbol)

                if symbol not in active_trades:
                    strat_fvg(df15, symbol)

                if symbol not in active_trades:
                    strat_ema_pullback(df15, symbol)

                if symbol not in active_trades:
                    strat_breakout(df15, symbol)

                if symbol not in active_trades:
                    print(f"[{symbol}] No signal this candle")

                time.sleep(1)

            scan_count += 1
            print(f"\n[✓] Scan #{scan_count} done!")
            print(f"[→] Next: waiting for 15min candle close")

        except Exception as e:
            print(f"[MAIN ERR] {e}")
            time.sleep(60)

if __name__ == '__main__':
    run()
