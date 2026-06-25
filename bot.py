import time
import requests
import ccxt
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════
# CONFIG
# ═══════════════════════════════════
NTFY_URL       = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS        = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
TF_ENTRY       = '15m'
TF_TREND       = '1h'
PERF_FILE      = 'bot_performance.json'
SCAN_SLEEP     = 60

# High impact news only (UTC)
HIGH_IMPACT_NEWS = [
    (8, 30),   # NFP / CPI
    (12, 30),  # US Core CPI
    (14, 0),   # Fed Decision
    (18, 0),   # Fed Chair Speech
    (19, 0),   # FOMC Minutes
]
NEWS_BLOCK_MIN = 45

# ═══════════════════════════════════
# EXCHANGE FAILOVER
# ═══════════════════════════════════
def connect_exchange():
    brokers = [
        ('MEXC',    ccxt.mexc,    {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
        ('Binance', ccxt.binance, {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
        ('Bybit',   ccxt.bybit,   {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    ]
    for name, cls, cfg in brokers:
        try:
            ex = cls(cfg)
            ex.fetch_ticker('BTC/USDT')
            log(f"Exchange connected: {name}")
            return ex, name
        except Exception as e:
            log(f"Exchange {name} failed: {e}")
    log("FATAL: All exchanges failed!")
    exit(1)

# ═══════════════════════════════════
# LOGGING
# ═══════════════════════════════════
def log(msg):
    now = datetime.now(timezone.utc).strftime('%d-%b %H:%M:%S')
    print(f"[{now}] {msg}")

# ═══════════════════════════════════
# NOTIFICATION
# ═══════════════════════════════════
def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        headers = {"Title": title, "Priority": "high", "Tags": tags}
        r = requests.post(
            NTFY_URL,
            data=msg.encode('utf-8'),
            headers=headers,
            timeout=15
        )
        log(f"NOTIF sent: {title} status:{r.status_code}")
    except Exception as e:
        log(f"NOTIF ERR: {e}")

# ═══════════════════════════════════
# PERFORMANCE / OPTIMIZER
# ═══════════════════════════════════
class Optimizer:
    def __init__(self):
        self.data = self._load()

    def _load(self):
        if os.path.exists(PERF_FILE):
            try:
                with open(PERF_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'strategies': {},
            'pairs': {},
            'sessions': {},
            'daily': {},
            'active_trades': {},
            'last_daily_report': ''
        }

    def _save(self):
        try:
            with open(PERF_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log(f"SAVE ERR: {e}")

    def get_weight(self, strategy):
        return self.data['strategies'].get(strategy, {}).get('weight', 1.0)

    def record_signal(self, symbol, strategy, session, entry, sl, tp1, tp2):
        rr = round((tp1 - entry) / max(entry - sl, 1e-10), 2)
        trade = {
            'symbol': symbol, 'strategy': strategy,
            'session': session, 'entry': entry,
            'sl': sl, 'tp1': tp1, 'tp2': tp2,
            'rr': rr, 'status': 'open',
            'time': datetime.now(timezone.utc).isoformat()
        }
        self.data['active_trades'][symbol] = trade
        self._save()
        log(f"OPT: Trade recorded {symbol} {strategy}")

    def close_trade(self, symbol, result):
        if symbol not in self.data['active_trades']:
            return
        trade = self.data['active_trades'].pop(symbol)
        trade['result'] = result
        trade['status'] = 'closed'
        trade['closed'] = datetime.now(timezone.utc).isoformat()
        strat   = trade['strategy']
        pair    = symbol
        session = trade.get('session', 'Unknown')
        today   = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        for scope, key in [('strategies', strat), ('pairs', pair), ('sessions', session)]:
            if key not in self.data[scope]:
                self.data[scope][key] = {'wins': 0, 'losses': 0, 'weight': 1.0}
            rec = self.data[scope][key]
            if result == 'win':
                rec['wins']   += 1
                rec['weight']  = min(2.0, rec['weight'] + 0.1)
            else:
                rec['losses'] += 1
                rec['weight']  = max(0.2, rec['weight'] - 0.15)
        if today not in self.data['daily']:
            self.data['daily'][today] = {'wins': 0, 'losses': 0, 'trades': []}
        self.data['daily'][today][result + 's'] += 1
        self.data['daily'][today]['trades'].append(trade)
        self._save()
        log(f"OPT: Trade closed {symbol} result:{result}")

    def get_best(self):
        best_s = max(
            self.data['strategies'],
            key=lambda x: self.data['strategies'][x].get('weight', 1.0),
            default='N/A'
        )
        best_p = max(
            self.data['pairs'],
            key=lambda x: self.data['pairs'][x].get('weight', 1.0),
            default='N/A'
        )
        return best_s, best_p

    def daily_report(self):
        now   = datetime.now(timezone.utc)
        today = now.strftime('%Y-%m-%d')
        yest  = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        if self.data.get('last_daily_report') == today:
            return
        if now.hour != 6:
            return
        d      = self.data['daily'].get(yest, {})
        wins   = d.get('wins', 0)
        losses = d.get('losses', 0)
        total  = wins + losses
        wr     = round(wins / total * 100, 1) if total > 0 else 0
        best_s, best_p = self.get_best()
        lines  = ""
        for s, v in self.data['strategies'].items():
            w  = v.get('wins', 0)
            l  = v.get('losses', 0)
            wt = v.get('weight', 1.0)
            lines += f"  {s}: {w}W/{l}L wt:{wt:.1f}\n"
        msg = (
            f"Date: {yest}\n"
            f"Total: {total} | W:{wins} L:{losses}\n"
            f"Win Rate: {wr}%\n\n"
            f"Strategies:\n{lines}\n"
            f"Best Strategy: {best_s}\n"
            f"Best Pair: {best_p}"
        )
        notify("Daily Report", msg, tags="bar_chart,calendar")
        self.data['last_daily_report'] = today
        self._save()
        log(f"Daily report sent for {yest}")

# ═══════════════════════════════════
# FILTERS
# ═══════════════════════════════════
def is_high_impact_news():
    now = datetime.now(timezone.utc)
    cur = now.hour * 60 + now.minute
    for (h, m) in HIGH_IMPACT_NEWS:
        if abs(cur - (h * 60 + m)) <= NEWS_BLOCK_MIN:
            log(f"High impact news block active: {h}:{m:02d} UTC")
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

# ═══════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════
def get_df(exchange, exchange_name, symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        log(f"DATA {symbol} {timeframe} {len(df)} candles via {exchange_name}")
        return df
    except Exception as e:
        log(f"FETCH ERR {symbol}: {e}")
        return None

# ═══════════════════════════════════
# INDICATORS
# ═══════════════════════════════════
def calc_ema(df, p):
    return df['close'].ewm(span=p, adjust=False).mean()

def calc_atr(df, p=14):
    hl  = df['high'] - df['low']
    hpc = abs(df['high'] - df['close'].shift(1))
    lpc = abs(df['low']  - df['close'].shift(1))
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

def check_uptrend(exchange, exchange_name, symbol):
    df = get_df(exchange, exchange_name, symbol, TF_TREND, 210)
    if df is None:
        return False
    result = df['close'].iloc[-1] > calc_ema(df, 200).iloc[-1]
    log(f"TREND {symbol}: {'UP' if result else 'DOWN'}")
    return result

def check_market_type(df):
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    recent = df['volume'].iloc[-5:].mean()
    mkt    = "TRENDING" if recent > vol_ma * 1.3 else "RANGING"
    log(f"MKT vol_ma:{vol_ma:.0f} recent:{recent:.0f} -> {mkt}")
    return mkt

def calc_targets(entry, atr_v, rr1=2.5, rr2=4.5, sl_m=1.5):
    sl   = entry - atr_v * sl_m
    risk = entry - sl
    tp1  = entry + risk * rr1
    tp2  = entry + risk * rr2
    return sl, tp1, tp2

# ═══════════════════════════════════
# SIGNAL SENDER
# ═══════════════════════════════════
def send_signal(active_trades, optimizer, symbol, strategy, market, entry, sl, tp1, tp2):
    weight  = optimizer.get_weight(strategy)
    rr      = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    session = get_session_name()
    msg = (
        f"Coin: {symbol}\n"
        f"Strategy: {strategy}\n"
        f"Market: {market}\n"
        f"Session: {session}\n"
        f"Entry:  {entry:.4f}\n"
        f"TP1:    {tp1:.4f}  (+{((tp1-entry)/entry*100):.1f}%)\n"
        f"TP2:    {tp2:.4f}  (+{((tp2-entry)/entry*100):.1f}%)\n"
        f"SL:     {sl:.4f}   (-{((entry-sl)/entry*100):.1f}%)\n"
        f"RR:     1:{rr}\n"
        f"Weight: {weight:.1f}/2.0"
    )
    notify(f"BUY | {strategy}", msg)
    active_trades[symbol] = {
        'entry': entry, 'tp1': tp1, 'tp2': tp2,
        'sl': sl, 'strategy': strategy, 'session': session
    }
    optimizer.record_signal(symbol, strategy, session, entry, sl, tp1, tp2)
    log(f"SIGNAL sent: {symbol} {strategy} entry:{entry:.4f} rr:1:{rr}")

# ═══════════════════════════════════
# STRATEGIES
# ═══════════════════════════════════
def run_sweep(df, symbol, active_trades, optimizer):
    strat = 'SWEEP'
    w     = optimizer.get_weight(strat)
    if w < 0.3:
        log(f"SWEEP skipped low weight:{w:.1f}")
        return
    try:
        c      = df.iloc[-1]
        p      = df.iloc[-2]
        swing  = df['low'].iloc[-20:-1].min()
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        atr_v  = calc_atr(df).iloc[-1]
        swept  = p['low'] < swing and c['close'] > swing
        bull   = c['close'] > c['open']
        vol_ok = c['volume'] > vol_ma * 1.2
        log(f"SWEEP {symbol} swept:{swept} bull:{bull} vol:{vol_ok} w:{w:.1f}")
        if swept and bull and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.5, 4.5)
            send_signal(active_trades, optimizer, symbol, strat, 'RANGING', c['close'], sl, tp1, tp2)
    except Exception as e:
        log(f"SWEEP ERR: {e}")

def run_ema_pullback(df, symbol, active_trades, optimizer):
    strat = 'EMA_PULLBACK'
    w     = optimizer.get_weight(strat)
    if w < 0.3:
        log(f"EMA skipped low weight:{w:.1f}")
        return
    try:
        df         = df.copy()
        df['e20']  = calc_ema(df, 20)
        df['e50']  = calc_ema(df, 50)
        df['e200'] = calc_ema(df, 200)
        atr_v      = calc_atr(df).iloc[-1]
        vol_ma     = df['volume'].rolling(20).mean().iloc[-1]
        c          = df.iloc[-1]
        p          = df.iloc[-2]
        trend      = c['e50'] > c['e200']
        touched    = p['low'] <= p['e20'] * 1.002
        bounced    = c['close'] > c['e20'] and c['close'] > c['open']
        vol_ok     = c['volume'] > vol_ma
        log(f"EMA {symbol} trend:{trend} touch:{touched} bounce:{bounced} vol:{vol_ok} w:{w:.1f}")
        if trend and touched and bounced and vol_ok:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(active_trades, optimizer, symbol, strat, 'TRENDING', c['close'], sl, tp1, tp2)
    except Exception as e:
        log(f"EMA ERR: {e}")

def run_breakout(df, symbol, active_trades, optimizer):
    strat = 'BREAKOUT'
    w     = optimizer.get_weight(strat)
    if w < 0.3:
        log(f"BREAKOUT skipped low weight:{w:.1f}")
        return
    try:
        atr_v  = calc_atr(df).iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        resist = df['high'].iloc[-20:-2].max()
        c      = df.iloc[-1]
        p      = df.iloc[-2]
        broke    = p['close'] > resist and p['volume'] > vol_ma * 1.5
        retested = c['low'] <= resist * 1.002 and c['close'] > resist
        bull     = c['close'] > c['open']
        log(f"BREAKOUT {symbol} broke:{broke} retest:{retested} bull:{bull} w:{w:.1f}")
        if broke and retested and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.5, 5.5)
            send_signal(active_trades, optimizer, symbol, strat, 'TRENDING', c['close'], sl, tp1, tp2)
    except Exception as e:
        log(f"BREAKOUT ERR: {e}")

def run_day_breakout(df, symbol, active_trades, optimizer):
    strat = 'DAY_BREAKOUT'
    w     = optimizer.get_weight(strat)
    if w < 0.3:
        log(f"DAY_BREAKOUT skipped low weight:{w:.1f}")
        return
    try:
        atr_v    = calc_atr(df).iloc[-1]
        vol_ma   = df['volume'].rolling(20).mean().iloc[-1]
        day_high = df['high'].iloc[-96:-1].max()
        c        = df.iloc[-1]
        broke_h  = c['close'] > day_high and c['volume'] > vol_ma * 1.3 and c['close'] > c['open']
        log(f"DAY_BREAKOUT {symbol} day_high:{day_high:.4f} close:{c['close']:.4f} broke:{broke_h} w:{w:.1f}")
        if broke_h:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 3.0, 5.0)
            send_signal(active_trades, optimizer, symbol, strat, 'TRENDING', c['close'], sl, tp1, tp2)
    except Exception as e:
        log(f"DAY ERR: {e}")

def run_opportunity(df, symbol, active_trades, optimizer):
    try:
        c      = df.iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        atr_v  = calc_atr(df).iloc[-1]
        body   = abs(c['close'] - c['open'])
        avg_b  = abs(df['close'] - df['open']).rolling(20).mean().iloc[-1]
        big_c  = body > avg_b * 2.0
        big_v  = c['volume'] > vol_ma * 2.0
        bull   = c['close'] > c['open']
        log(f"OPP {symbol} big_c:{big_c} big_v:{big_v} bull:{bull}")
        if big_c and big_v and bull:
            sl, tp1, tp2 = calc_targets(c['close'], atr_v, 2.0, 4.0)
            send_signal(active_trades, optimizer, symbol, 'OPPORTUNITY', 'ANY', c['close'], sl, tp1, tp2)
    except Exception as e:
        log(f"OPP ERR: {e}")

# ═══════════════════════════════════
# MONITOR
# ═══════════════════════════════════
def monitor_trade(df, symbol, active_trades, optimizer):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    log(f"MONITOR {symbol} high:{c['high']:.4f} low:{c['low']:.4f} tp1:{t['tp1']:.4f} tp2:{t['tp2']:.4f} sl:{t['sl']:.4f}")
    if c['high'] >= t['tp2']:
        notify("TP2 HIT!", f"{symbol}\nFull target!\nTP2:{t['tp2']:.4f}\nStrategy:{t['strategy']}", tags="trophy,fire")
        optimizer.close_trade(symbol, 'win')
        active_trades.pop(symbol, None)
    elif c['high'] >= t['tp1']:
        notify("TP1 HIT!", f"{symbol}\nTP1:{t['tp1']:.4f} hit!\nMove SL to entry!", tags="money_bag")
    elif c['low'] <= t['sl']:
        notify("SL HIT", f"{symbol}\nSL:{t['sl']:.4f}\nStrategy:{t['strategy']}", tags="red_circle")
        optimizer.close_trade(symbol, 'loss')
        active_trades.pop(symbol, None)

# ═══════════════════════════════════
# HOURLY REPORT
# ═══════════════════════════════════
def maybe_hourly(last_report, scan_count, active_trades, optimizer, exchange_name):
    if time.time() - last_report < 3600:
        return last_report, scan_count
    best_s, best_p = optimizer.get_best()
    active = list(active_trades.keys()) or ["None"]
    notify(
        "Bot Active Alhamdulillah",
        f"Scans: {scan_count}\n"
        f"Active: {', '.join(active)}\n"
        f"Session: {'ON' if is_good_session() else 'OFF'}\n"
        f"News: {'BLOCKED' if is_high_impact_news() else 'CLEAR'}\n"
        f"Exchange: {exchange_name}\n"
        f"Best Strategy: {best_s}\n"
        f"Best Pair: {best_p}",
        tags="robot,white_check_mark"
    )
    return time.time(), 0

# ═══════════════════════════════════
# MAIN
# ═══════════════════════════════════
def main():
    exchange, exchange_name = connect_exchange()
    optimizer    = Optimizer()
    active_trades = {}
    start_time   = time.time()
    last_report  = time.time()
    scan_count   = 0

    log("Genius Trading Bot starting...")
    notify(
        "Bot Started",
        f"Genius Bot Live!\nBTC ETH SOL AVAX\nExchange:{exchange_name}\n100% Halal Spot",
        tags="rocket"
    )
    log("Startup notification sent!")

    while True:
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr done restarting", tags="arrows_counterclockwise")
            log("Time limit restart")
            break

        try:
            optimizer.daily_report()
            last_report, scan_count = maybe_hourly(
                last_report, scan_count, active_trades, optimizer, exchange_name
            )

            now = datetime.now(timezone.utc)
            log(f"TIME {now.strftime('%d-%b %H:%M')} UTC")

            if is_high_impact_news():
                log("SKIP: High impact news — 10min wait")
                time.sleep(600)
                continue

            if not is_good_session():
                log(f"SKIP: Outside London/NY session")
                time.sleep(SCAN_SLEEP)
                continue

            scan_count += 1
            log(f"===== SCAN #{scan_count} START =====")

            for symbol in SYMBOLS:
                log(f"--- {symbol} ---")

                df15 = get_df(exchange, exchange_name, symbol, TF_ENTRY)
                if df15 is None or df15.empty:
                    log(f"{symbol}: no data skip")
                    continue

                if symbol in active_trades:
                    monitor_trade(df15, symbol, active_trades, optimizer)
                    log(f"{symbol}: monitoring active trade")
                    continue

                if not check_uptrend(exchange, exchange_name, symbol):
                    log(f"{symbol}: below EMA200 skip")
                    continue

                mkt = check_market_type(df15)

                if mkt == "RANGING":
                    run_sweep(df15, symbol, active_trades, optimizer)
                else:
                    run_ema_pullback(df15, symbol, active_trades, optimizer)
                    if symbol not in active_trades:
                        run_breakout(df15, symbol, active_trades, optimizer)
                    if symbol not in active_trades:
                        run_day_breakout(df15, symbol, active_trades, optimizer)

                if symbol not in active_trades:
                    run_opportunity(df15, symbol, active_trades, optimizer)

                time.sleep(1)

            log(f"===== SCAN #{scan_count} DONE =====")
            log(f"Waiting {SCAN_SLEEP}s for next scan...")
            time.sleep(SCAN_SLEEP)

        except Exception as e:
            log(f"MAIN ERR: {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()
