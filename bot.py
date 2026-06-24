import time
import sys
import requests
import ccxt
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timezone, timedelta

# =====================================================
# CONFIGURATION
# =====================================================
NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
TF_ENTRY = '15m'
TF_TREND = '1h'
PERF_FILE = 'bot_performance.json'
NEWS_TIMES_UTC = [(8,30),(14,0),(14,30),(18,0)]
NEWS_BLOCK_MIN = 30

# =====================================================
# MULTI-EXCHANGE FAILOVER
# MEXC -> Binance -> Bybit
# =====================================================
def connect_exchange():
    brokers = [
        ('MEXC', ccxt.mexc, {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
        ('Binance', ccxt.binance, {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
        ('Bybit', ccxt.bybit, {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    ]
    for name, cls, cfg in brokers:
        try:
            ex = cls(cfg)
            ex.fetch_ticker('BTC/USDT')
            print("[EXCHANGE] Connected: " + name, flush=True)
            return ex, name
        except Exception as e:
            print("[EXCHANGE] " + name + " failed: " + str(e), flush=True)
    print("[FATAL] All exchanges failed!", flush=True)
    exit(1)

exchange, exchange_name = connect_exchange()

# =====================================================
# PERFORMANCE TRACKER / SELF-OPTIMIZER
# =====================================================
class Optimizer:
    def __init__(self):
        self.data = self.load()

    def load(self):
        if os.path.exists(PERF_FILE):
            try:
                with open(PERF_FILE, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {
            'strategies': {},
            'pairs': {},
            'sessions': {},
            'daily': {},
            'active_trades': {},
            'last_daily_report': ''
        }

    def save(self):
        try:
            with open(PERF_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print("[SAVE ERR] " + str(e), flush=True)

    def record_signal(self, symbol, strategy, session, entry, sl, tp1, tp2):
        key = symbol + "_" + strategy + "_" + session
        trade = {
            'symbol': symbol,
            'strategy': strategy,
            'session': session,
            'entry': entry,
            'sl': sl,
            'tp1': tp1,
            'tp2': tp2,
            'status': 'open',
            'result': None,
            'rr': round((tp1 - entry) / max(entry - sl, 1e-10), 2),
            'time': datetime.now(timezone.utc).isoformat()
        }
        self.data['active_trades'][symbol] = trade
        self.save()
        print("[OPT] Trade recorded: " + key, flush=True)

    def close_trade(self, symbol, result):
        if symbol not in self.data['active_trades']:
            return
        trade = self.data['active_trades'].pop(symbol)
        trade['result'] = result
        trade['status'] = 'closed'
        trade['closed'] = datetime.now(timezone.utc).isoformat()

        strat = trade['strategy']
        pair = trade['symbol']
        session = trade['session']
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        for scope, key in [('strategies', strat), ('pairs', pair), ('sessions', session)]:
            if key not in self.data[scope]:
                self.data[scope][key] = {'wins': 0, 'losses': 0, 'weight': 1.0, 'trades': []}
            rec = self.data[scope][key]
            if result == 'win':
                rec['wins'] += 1
                rec['weight'] = min(2.0, rec['weight'] + 0.1)
            else:
                rec['losses'] += 1
                rec['weight'] = max(0.2, rec['weight'] - 0.15)
            rec['trades'].append(trade)

        if today not in self.data['daily']:
            self.data['daily'][today] = {'wins': 0, 'losses': 0, 'trades': []}
        self.data['daily'][today][result + 's'] += 1
        self.data['daily'][today]['trades'].append(trade)

        self.save()
        print("[OPT] Trade closed: " + symbol + " -> " + result, flush=True)

    def get_weight(self, strategy):
        s = self.data['strategies'].get(strategy, {})
        return s.get('weight', 1.0)

    def get_best_focus(self):
        best_strat = max(
            self.data['strategies'],
            key=lambda x: self.data['strategies'][x].get('weight', 1.0),
            default=None
        )
        best_pair = max(
            self.data['pairs'],
            key=lambda x: self.data['pairs'][x].get('weight', 1.0),
            default=None
        )
        return best_strat, best_pair

    def daily_report(self):
        now = datetime.now(timezone.utc)
        today = now.strftime('%Y-%m-%d')
        yest = (now - timedelta(days=1)).strftime('%Y-%m-%d')

        if self.data.get('last_daily_report') == today:
            return
        if now.hour != 6:
            return

        d = self.data['daily'].get(yest, {})
        wins = d.get('wins', 0)
        losses = d.get('losses', 0)
        total = wins + losses
        wr = round(wins / total * 100, 1) if total > 0 else 0

        best_s, best_p = self.get_best_focus()

        strat_lines = ""
        for s, v in self.data['strategies'].items():
            w = v.get('wins', 0)
            l = v.get('losses', 0)
            wt = v.get('weight', 1.0)
            strat_lines = strat_lines + "  " + s + ": " + str(w) + "W/" + str(l) + "L (weight:" + str(round(wt, 1)) + ")\n"

        msg = (
            "Date: " + yest + "\n"
            "Total Trades: " + str(total) + "\n"
            "Wins: " + str(wins) + " | Losses: " + str(losses) + "\n"
            "Win Rate: " + str(wr) + "%\n\n"
            "Strategy Performance:\n" + strat_lines + "\n"
            "Best Strategy: " + str(best_s or 'N/A') + "\n"
            "Best Pair: " + str(best_p or 'N/A') + "\n"
            "Exchange: " + exchange_name
        )
        notify("Daily Report", msg, tags="bar_chart,calendar")
        self.data['last_daily_report'] = today
        self.save()
        print("[REPORT] Daily report sent for " + yest, flush=True)

optimizer = Optimizer()

# =====================================================
# ACTIVE TRADES (runtime)
# =====================================================
active_trades = {}

# =====================================================
# NOTIFICATION
# =====================================================
def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        headers = {"Title": title, "Priority": "high", "Tags": tags}
        r = requests.post(NTFY_URL, data=msg.encode('utf-8'), headers=headers, timeout=15)
        print("[NOTIF] " + title + " - " + str(r.status_code), flush=True)
    except Exception as e:
        print("[NOTIF ERR] " + str(e), flush=True)

# =====================================================
# FILTERS
# =====================================================
def is_news_time():
    now = datetime.now(timezone.utc)
    for (h, m) in NEWS_TIMES_UTC:
        if abs((now.hour * 60 + now.minute) - (h * 60 + m)) <= NEWS_BLOCK_MIN:
            return True
    return False

def is_good_session():
    h = datetime.now(timezone.utc).hour
    return (8 <= h < 11) or (13 <= h < 16)

def get_session_name():
    h = datetime.now(timezone.utc).hour
    if 8 <= h < 11:
        return "London"
    if 13 <= h < 16:
        return "NewYork"
    return "Off"

# =====================================================
# DATA FETCH WITH FAILOVER
# =====================================================
def get_df(symbol, timeframe, limit=200):
    global exchange, exchange_name
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        print("[DATA] " + symbol + " " + timeframe + " " + str(len(df)) + " candles - " + exchange_name, flush=True)
        return df
    except Exception as e:
        print("[FETCH ERR] " + exchange_name + " " + symbol + ": " + str(e) + " - trying failover", flush=True)
        exchange, exchange_name = connect_exchange()
        try:
            data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
            print("[DATA] " + symbol + " ok via " + exchange_name, flush=True)
            return df
        except Exception as e2:
            print("[FETCH FATAL] " + symbol + ": " + str(e2), flush=True)
            return None

# =====================================================
# INDICATORS
# =====================================================
def ema(df, p):
    return df['close'].ewm(span=p, adjust=False).mean()

def atr(df, p=14):
    hl = df['high'] - df['low']
    hpc = abs(df['high'] - df['close'].shift(1))
    lpc = abs(df['low'] - df['close'].shift(1))
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

def is_uptrend(symbol):
    df = get_df(symbol, TF_TREND, 210)
    if df is None:
        return False
    result = df['close'].iloc[-1] > ema(df, 200).iloc[-1]
    trend_str = "UP" if result else "DOWN"
    print("[TREND] " + symbol + ": " + trend_str, flush=True)
    return result

def is_volume_spike(df):
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    cur_vol = df['volume'].iloc[-1]
    spike = cur_vol > vol_ma
    print("[VOL] cur:" + str(int(cur_vol)) + " ma:" + str(int(vol_ma)) + " spike:" + str(spike), flush=True)
    return spike, vol_ma

def market_type(df):
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    recent = df['volume'].iloc[-5:].mean()
    mkt = "TRENDING" if recent > vol_ma * 1.3 else "RANGING"
    print("[MKT] recent_vol:" + str(int(recent)) + " ma:" + str(int(vol_ma)) + " - " + mkt, flush=True)
    return mkt

# =====================================================
# TARGETS
# =====================================================
def calc_targets(entry, atr_v, rr1=2.5, rr2=4.5, sl_m=1.5):
    sl = entry - atr_v * sl_m
    risk = entry - sl
    tp1 = entry + risk * rr1
    tp2 = entry + risk * rr2
    return sl, tp1, tp2

def send_signal(symbol, strategy, market, entry, sl, tp1, tp2):
    weight = optimizer.get_weight(strategy)
    rr = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    session = get_session_name()
    tp1_pct = round((tp1 - entry) / entry * 100, 1)
    tp2_pct = round((tp2 - entry) / entry * 100, 1)
    sl_pct = round((entry - sl) / entry * 100, 1)
    msg = (
        "Coin: " + symbol + "\n"
        "Strategy: " + strategy + "\n"
        "Market: " + market + "\n"
        "Session: " + session + "\n"
        "Entry:  " + str(round(entry, 4)) + "\n"
        "TP1:    " + str(round(tp1, 4)) + "  (+" + str(tp1_pct) + "%)\n"
        "TP2:    " + str(round(tp2, 4)) + "  (+" + str(tp2_pct) + "%)\n"
        "SL:     " + str(round(sl, 4)) + "   (-" + str(sl_pct) + "%)\n"
        "RR:     1:" + str(rr) + "\n"
        "Weight: " + str(round(weight, 1)) + "/2.0"
    )
    notify("BUY | " + strategy, msg)
    active_trades[symbol] = {
        'entry': entry, 'tp1': tp1, 'tp2': tp2, 'sl': sl,
        'strategy': strategy, 'session': session
    }
    optimizer.record_signal(symbol, strategy, session, entry, sl, tp1, tp2)

# =====================================================
# STRATEGY 1 - SWEEP (Ranging)
# =====================================================
def strat_sweep(df, symbol):
    try:
        w = optimizer.get_weight('SWEEP')
        if w < 0.3:
            print("[SWEEP] Skipped - low weight " + str(round(w, 1)), flush=True)
            return
        c = df.iloc[-1]
        p = df.iloc[-2]
        swing = df['low'].iloc[-20:-1].min()
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        atr_v = atr(df).iloc[-1]
        swept = p['low'] < swing and c['close'] > swing
        bull = c['close'] > c['open']
        vol_ok = c['volume'] > vol_ma * 1.2
        print("[SWEEP] swept:" + str(swept) + " bull:" + str(bull) + " vol:" + str(vol_ok) + " w:" + str(round(w, 1)), flush=True)
        if swept and bull and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.5, 4.5)
            send_signal(symbol, "SWEEP", "RANGING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print("[SWEEP ERR] " + str(e), flush=True)

# =====================================================
# STRATEGY 2 - EMA PULLBACK (Trending)
# =====================================================
def strat_ema_pullback(df, symbol):
    try:
        w = optimizer.get_weight('EMA_PULLBACK')
        if w < 0.3:
            print("[EMA] Skipped - low weight " + str(round(w, 1)), flush=True)
            return
        df = df.copy()
        df['e20'] = ema(df, 20)
        df['e50'] = ema(df, 50)
        df['e200'] = ema(df, 200)
        atr_v = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        c = df.iloc[-1]
        p = df.iloc[-2]
        trend = c['e50'] > c['e200']
        touched = p['low'] <= p['e20'] * 1.002
        bounced = c['close'] > c['e20'] and c['close'] > c['open']
        vol_ok = c['volume'] > vol_ma
        print("[EMA] trend:" + str(trend) + " touch:" + str(touched) + " bounce:" + str(bounced) + " vol:" + str(vol_ok) + " w:" + str(round(w, 1)), flush=True)
        if trend and touched and bounced and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(symbol, "EMA_PULLBACK", "TRENDING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print("[EMA ERR] " + str(e), flush=True)

# =====================================================
# STRATEGY 3 - BREAKOUT (Trending)
# =====================================================
def strat_breakout(df, symbol):
    try:
        w = optimizer.get_weight('BREAKOUT')
        if w < 0.3:
            print("[BREAK] Skipped - low weight " + str(round(w, 1)), flush=True)
            return
        atr_v = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        resist = df['high'].iloc[-20:-2].max()
        c = df.iloc[-1]
        p = df.iloc[-2]
        broke = p['close'] > resist and p['volume'] > vol_ma * 1.5
        retested = c['low'] <= resist * 1.002 and c['close'] > resist
        bull = c['close'] > c['open']
        print("[BREAK] broke:" + str(broke) + " retest:" + str(retested) + " bull:" + str(bull) + " w:" + str(round(w, 1)), flush=True)
        if broke and retested and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.5, 5.5)
            send_signal(symbol, "BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print("[BREAK ERR] " + str(e), flush=True)

# =====================================================
# STRATEGY 4 - DAY HIGH/LOW BREAKOUT
# 96 candles = 24 hours on 15m TF
# =====================================================
def strat_day_breakout(df, symbol):
    try:
        w = optimizer.get_weight('DAY_BREAKOUT')
        if w < 0.3:
            print("[DAY] Skipped - low weight " + str(round(w, 1)), flush=True)
            return
        atr_v = atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        day_high = df['high'].iloc[-96:-1].max()
        day_low = df['low'].iloc[-96:-1].min()
        c = df.iloc[-1]
        broke_high = c['close'] > day_high and c['volume'] > vol_ma * 1.3
        broke_low = c['close'] < day_low and c['volume'] > vol_ma * 1.3
        print("[DAY] day_high:" + str(round(day_high, 4)) + " day_low:" + str(round(day_low, 4)) + " close:" + str(round(c['close'], 4)), flush=True)
        print("[DAY] broke_high:" + str(broke_high) + " broke_low:" + str(broke_low) + " w:" + str(round(w, 1)), flush=True)
        if broke_high and c['close'] > c['open']:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(symbol, "DAY_BREAKOUT", "TRENDING", c['close'], sl, tp1, tp2)
    except Exception as e:
        print("[DAY ERR] " + str(e), flush=True)

# =====================================================
# BONUS OPPORTUNITY HUNTER
# High volume + big candle = extra signal
# =====================================================
def strat_opportunity(df, symbol):
    try:
        c = df.iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        atr_v = atr(df).iloc[-1]
        body = abs(c['close'] - c['open'])
        avg_b = (abs(df['close'] - df['open'])).rolling(20).mean().iloc[-1]
        big_c = body > avg_b * 2.0
        big_v = c['volume'] > vol_ma * 2.0
        bull = c['close'] > c['open']
        print("[OPP] big_candle:" + str(big_c) + " big_vol:" + str(big_v) + " bull:" + str(bull), flush=True)
        if big_c and big_v and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.0, 4.0)
            send_signal(symbol, "OPPORTUNITY", "ANY", c['close'], sl, tp1, tp2)
    except Exception as e:
        print("[OPP ERR] " + str(e), flush=True)

# =====================================================
# TRADE MONITOR
# =====================================================
def monitor(df, symbol):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    if c['high'] >= t['tp2']:
        notify("TP2 HIT!", symbol + "\nFull target!\nTP2: " + str(round(t['tp2'], 4)) + "\nStrategy: " + t['strategy'], tags="trophy,fire")
        optimizer.close_trade(symbol, 'win')
        active_trades.pop(symbol, None)
    elif c['high'] >= t['tp1']:
        notify("TP1 HIT!", symbol + "\nTP1: " + str(round(t['tp1'], 4)) + "\nMove SL to entry!", tags="money_bag")
    elif c['low'] <= t['sl']:
        notify("SL HIT", symbol + "\nSL: " + str(round(t['sl'], 4)) + "\nStrategy: " + t['strategy'], tags="red_circle")
        optimizer.close_trade(symbol, 'loss')
        active_trades.pop(symbol, None)

# =====================================================
# HOURLY REPORT
# =====================================================
last_report = time.time()
scan_count = 0

def hourly_report():
    global last_report, scan_count
    if time.time() - last_report < 3600:
        return
    best_s, best_p = optimizer.get_best_focus()
    active = list(active_trades.keys()) or ["None"]
    session_status = "ON" if is_good_session() else "OFF"
    news_status = "BLOCKED" if is_news_time() else "CLEAR"
    notify("Bot Active - Alhamdulillah",
        "Scans: " + str(scan_count) + "\n"
        "Active: " + ", ".join(active) + "\n"
        "Session: " + session_status + "\n"
        "News: " + news_status + "\n"
        "Exchange: " + exchange_name + "\n"
        "Best Strategy: " + str(best_s or 'N/A') + "\n"
        "Best Pair: " + str(best_p or 'N/A'),
        tags="robot,white_check_mark")
    last_report = time.time()
    scan_count = 0

# =====================================================
# MAIN LOOP
# =====================================================
def run():
    global scan_count
    start_time = time.time()
    print("[START] Genius Trading Bot starting...", flush=True)
    notify("Bot Started", "Genius Trading Bot Live!\nBTC ETH SOL AVAX\nExchange: " + exchange_name + "\n100% Halal Spot", tags="rocket")
    print("[START] Notification sent!", flush=True)

    while True:
        # 5.5 hour restart for GitHub Actions
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr done - restarting", tags="arrows_counterclockwise")
            print("[EXIT] Restarting...", flush=True)
            break

        try:
            # Daily report at 6am UTC
            optimizer.daily_report()
            hourly_report()

            now = datetime.now(timezone.utc)
            print("\n[TIME] " + now.strftime('%d-%b %H:%M') + " UTC", flush=True)

# =====================================================
# MAIN LOOP
# =====================================================
def run():
    global scan_count
    start_time = time.time()
    print("[START] Genius Trading Bot starting...", flush=True)
    notify("Bot Started", "Genius Trading Bot Live!
BTC ETH SOL AVAX
Exchange: " + exchange_name + "
100% Halal Spot", tags="rocket")
    print("[START] Notification sent!", flush=True)

    while True:
        # 5.5 hour restart for GitHub Actions
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr done - restarting", tags="arrows_counterclockwise")
            print("[EXIT] Restarting...", flush=True)
            break

        try:
            # Daily report at 6am UTC
            optimizer.daily_report()
            hourly_report()

            now = datetime.now(timezone.utc)
            print("
[TIME] " + now.strftime('%d-%b %H:%M') + " UTC", flush=True)

            if is_news_time():
                print("[SKIP] News time - 10min wait", flush=True)
                time.sleep(600)
                continue

            if not is_good_session():
                print("[SKIP] Outside session", flush=True)
                time.sleep(60)
                continue

            print("
" + "="*45, flush=True)
            print("[SCAN #" + str(scan_count+1) + "] " + now.strftime('%d-%b %H:%M') + " UTC | " + exchange_name, flush=True)
            print("="*45, flush=True)

            for symbol in SYMBOLS:
                try:
                    print("
--- " + symbol + " ---", flush=True)
                    df15 = get_df(symbol, TF_ENTRY)
                    if df15 is None or df15.empty:
                        print("[" + symbol + "] No data", flush=True)
                        continue

                    # Monitor active trade
                    if symbol in active_trades:
                        monitor(df15, symbol)
                        print("[" + symbol + "] Monitoring active trade", flush=True)
                        continue

                    # Uptrend filter
                    if not is_uptrend(symbol):
                        print("[" + symbol + "] Below EMA200 - skip", flush=True)
                        continue

                    # Volume check
                    spike, vol_ma = is_volume_spike(df15)

                    # Market type via volume
                    mkt = market_type(df15)

                    if mkt == "RANGING":
                        strat_sweep(df15, symbol)
                    else:
                        strat_ema_pullback(df15, symbol)
                        time.sleep(1)
                        if symbol not in active_trades:
                            strat_breakout(df15, symbol)
                            time.sleep(1)
                            if symbol not in active_trades:
                                strat_day_breakout(df15, symbol)
                                time.sleep(1)
                                if symbol not in active_trades:
                                    strat_opportunity(df15, symbol)

                    time.sleep(2)

                except Exception as e:
                    print("[SYMBOL ERR] " + symbol + ": " + str(e), flush=True)
                    time.sleep(2)

            scan_count += 1
            print("
[OK] Scan complete. Waiting 10 minutes for next cycle...", flush=True)
            time.sleep(30)

        except Exception as e:
            print("[MAIN ERR] " + str(e), flush=True)
            time.sleep(30)

if __name__ == "__main__":
    run()
