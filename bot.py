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
NTFY_URL         = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS          = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
TF_ENTRY         = '15m'
PERF_FILE        = 'bot_performance.json'
SCAN_SLEEP       = 60
DAILY_LOSS_LIMIT = 3
MIN_SCORE        = 7

HIGH_IMPACT_NEWS = [
    (8, 30), (12, 30), (14, 0), (18, 0), (19, 0),
]
NEWS_BLOCK_MIN = 45

# ═══════════════════════════════════
# LOGGING
# ═══════════════════════════════════
def log(msg):
    now = datetime.now(timezone.utc).strftime('%d-%b %H:%M:%S')
    print(f"[{now}] {msg}", flush=True)

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
        log(f"NOTIF: {title} status:{r.status_code}")
    except Exception as e:
        log(f"NOTIF ERR: {e}")

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
            'daily': {},
            'active_trades': {},
            'daily_losses': 0,
            'kill_switch_until': '',
            'last_daily_report': ''
        }

    def _save(self):
        try:
            with open(PERF_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log(f"SAVE ERR: {e}")

    def is_kill_switch_active(self):
        ks = self.data.get('kill_switch_until', '')
        if not ks:
            return False
        try:
            until = datetime.fromisoformat(ks)
            if datetime.now(timezone.utc) < until:
                log(f"KILL SWITCH active until {ks}")
                return True
            else:
                self.data['kill_switch_until'] = ''
                self.data['daily_losses'] = 0
                self._save()
                return False
        except Exception:
            return False

    def trigger_kill_switch(self):
        until = datetime.now(timezone.utc) + timedelta(hours=24)
        self.data['kill_switch_until'] = until.isoformat()
        self._save()
        notify(
            "KILL SWITCH ACTIVATED",
            f"Daily loss limit {DAILY_LOSS_LIMIT} hit!\nAll trading stopped for 24 hours.\nResumes: {until.strftime('%d-%b %H:%M')} UTC",
            tags="rotating_light,red_circle"
        )
        log(f"KILL SWITCH activated until {until}")

    def get_weight(self, strategy):
        s = self.data['strategies'].get(strategy, {})
        base_w = s.get('weight', 1.0)
        recent = s.get('recent_trades', [])
        if not recent:
            return base_w
        now = datetime.now(timezone.utc)
        decayed = 0.0
        total_w = 0.0
        for t in recent[-20:]:
            try:
                age_h   = (now - datetime.fromisoformat(t['time'])).total_seconds() / 3600
                decay   = max(0.1, 1.0 - (age_h / 48))
                val     = 1.0 if t['result'] == 'win' else -1.0
                decayed += val * decay
                total_w += decay
            except Exception:
                pass
        if total_w > 0:
            adj = decayed / total_w * 0.3
            base_w = max(0.2, min(2.0, base_w + adj))
        return base_w

    def record_signal(self, symbol, strategy, entry, sl, tp1, tp2, score):
        rr = round((tp1 - entry) / max(entry - sl, 1e-10), 2)
        trade = {
            'symbol': symbol, 'strategy': strategy,
            'entry': entry, 'sl': sl, 'tp1': tp1, 'tp2': tp2,
            'rr': rr, 'score': score, 'status': 'open',
            'dca_done': False,
            'tp1_hit': False,
            'trailing_sl': sl,
            'time': datetime.now(timezone.utc).isoformat()
        }
        self.data['active_trades'][symbol] = trade
        self._save()
        log(f"OPT: Signal recorded {symbol} {strategy} score:{score}")

    def close_trade(self, symbol, result):
        if symbol not in self.data['active_trades']:
            return
        trade = self.data['active_trades'].pop(symbol)
        trade['result'] = result
        trade['status'] = 'closed'
        trade['closed'] = datetime.now(timezone.utc).isoformat()
        strat = trade['strategy']
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if strat not in self.data['strategies']:
            self.data['strategies'][strat] = {
                'wins': 0, 'losses': 0,
                'weight': 1.0, 'recent_trades': []
            }
        rec = self.data['strategies'][strat]
        if result == 'win':
            rec['wins']   += 1
            rec['weight']  = min(2.0, rec['weight'] + 0.1)
        else:
            rec['losses'] += 1
            rec['weight']  = max(0.2, rec['weight'] - 0.1)
            self.data['daily_losses'] = self.data.get('daily_losses', 0) + 1
        rec['recent_trades'].append({
            'result': result,
            'time': datetime.now(timezone.utc).isoformat()
        })
        if len(rec['recent_trades']) > 50:
            rec['recent_trades'] = rec['recent_trades'][-50:]
        pair = symbol
        if pair not in self.data['pairs']:
            self.data['pairs'][pair] = {'wins': 0, 'losses': 0, 'weight': 1.0}
        self.data['pairs'][pair][result + 's'] += 1
        if today not in self.data['daily']:
            self.data['daily'][today] = {'wins': 0, 'losses': 0, 'trades': []}
        self.data['daily'][today][result + 's'] += 1
        self.data['daily'][today]['trades'].append(trade)
        self._save()
        log(f"OPT: Trade closed {symbol} {result}")
        if self.data.get('daily_losses', 0) >= DAILY_LOSS_LIMIT:
            self.trigger_kill_switch()

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

    def reset_daily_losses(self):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        last  = self.data.get('last_loss_reset', '')
        if last != today:
            self.data['daily_losses']   = 0
            self.data['last_loss_reset'] = today
            self._save()
            log("Daily loss counter reset")

    def daily_report(self):
        now   = datetime.now(timezone.utc)
        today = now.strftime('%Y-%m-%d')
        yest  = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        if self.data.get('last_daily_report') == today:
            return
        if now.hour != 6:
            return
        d     = self.data['daily'].get(yest, {})
        wins  = d.get('wins', 0)
        loss  = d.get('losses', 0)
        total = wins + loss
        wr    = round(wins / total * 100, 1) if total > 0 else 0
        best_s, best_p = self.get_best()
        lines = ""
        for s, v in self.data['strategies'].items():
            w  = v.get('wins', 0)
            l  = v.get('losses', 0)
            wt = v.get('weight', 1.0)
            lines += f"  {s}: {w}W/{l}L wt:{wt:.1f}\n"
        msg = (
            f"Date: {yest}\n"
            f"Total: {total} | W:{wins} L:{loss}\n"
            f"Win Rate: {wr}%\n\n"
            f"Strategies:\n{lines}\n"
            f"Best Strategy: {best_s}\n"
            f"Best Pair: {best_p}\n"
            f"Daily Losses Today: {self.data.get('daily_losses', 0)}/{DAILY_LOSS_LIMIT}"
        )
        notify("Daily Report", msg, tags="bar_chart,calendar")
        self.data['last_daily_report'] = today
        self._save()
        log(f"Daily report sent for {yest}")

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

def get_df(exchange, exchange_name, symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        log(f"DATA {symbol} {timeframe} {len(df)} candles via {exchange_name}")
        return df
    except Exception as e:
        log(f"FETCH ERR {symbol}: {e}")
        return None

def check_market_type(df):
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    recent = df['volume'].iloc[-5:].mean()
    mkt    = "TRENDING" if recent > vol_ma * 1.3 else "RANGING"
    log(f"MKT: vol_ma:{vol_ma:.0f} recent:{recent:.0f} -> {mkt}")
    return mkt

# ═══════════════════════════════════
# ADAPTIVE TARGETS
# ═══════════════════════════════════
def calc_adaptive_targets(entry, atr_v, atr_avg):
    is_fast = atr_v > atr_avg * 1.2
    if is_fast:
        tp1_pct = 0.02
        tp2_pct = 0.03
        log(f"TARGETS: Fast market mode TP1:2% TP2:3%")
    else:
        tp1_pct = 0.003
        tp2_pct = 0.005
        log(f"TARGETS: Scalp mode TP1:0.3% TP2:0.5%")
    sl  = entry - atr_v * 1.5
    tp1 = entry * (1 + tp1_pct)
    tp2 = entry * (1 + tp2_pct)
    return sl, tp1, tp2

# ═══════════════════════════════════
# QUALITY SCORE CALCULATOR
# ═══════════════════════════════════
def calc_score(df, strategy, optimizer, extra_points=0):
    score = 0
    c      = df.iloc[-1]
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    atr_v  = calc_atr(df).iloc[-1]
    atr_avg= calc_atr(df).mean()
    body   = abs(c['close'] - c['open'])
    avg_b  = abs(df['close'] - df['open']).rolling(20).mean().iloc[-1]
    if c['volume'] > vol_ma * 1.2:
        score += 2
        log(f"SCORE +2 volume spike")
    if c['close'] > c['open']:
        score += 1
        log(f"SCORE +1 bullish candle")
    if atr_v > atr_avg:
        score += 1
        log(f"SCORE +1 high ATR")
    if body > avg_b * 1.5:
        score += 1
        log(f"SCORE +1 big candle body")
    w = optimizer.get_weight(strategy)
    if w >= 1.5:
        score += 2
        log(f"SCORE +2 strategy weight high")
    elif w >= 1.0:
        score += 1
        log(f"SCORE +1 strategy weight ok")
    score += extra_points
    log(f"SCORE total:{score}/10 strategy:{strategy}")
    return score

# ═══════════════════════════════════
# DCA SUPPORT ZONE
# ═══════════════════════════════════
def find_dca_zone(df):
    recent_lows = df['low'].iloc[-20:]
    support     = recent_lows.min()
    log(f"DCA zone support: {support:.4f}")
    return support

# ═══════════════════════════════════
# SIGNAL SENDER
# ═══════════════════════════════════
def send_signal(active_trades, optimizer, df, symbol, strategy, market, score):
    c       = df.iloc[-1]
    entry   = c['close']
    atr_v   = calc_atr(df).iloc[-1]
    atr_avg = calc_atr(df).mean()
    sl, tp1, tp2 = calc_adaptive_targets(entry, atr_v, atr_avg)
    rr      = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    dca_z   = find_dca_zone(df)
    msg = (
        f"Coin: {symbol}\n"
        f"Strategy: {strategy}\n"
        f"Market: {market}\n"
        f"Score: {score}/10\n"
        f"Entry:  {entry:.4f}\n"
        f"TP1:    {tp1:.4f}  (+{((tp1-entry)/entry*100):.2f}%)\n"
        f"TP2:    {tp2:.4f}  (+{((tp2-entry)/entry*100):.2f}%)\n"
        f"SL:     {sl:.4f}   (-{((entry-sl)/entry*100):.2f}%)\n"
        f"DCA Zone: {dca_z:.4f}\n"
        f"RR: 1:{rr}"
    )
    notify(f"BUY | {strategy}", msg)
    active_trades[symbol] = {
        'entry':      entry,
        'tp1':        tp1,
        'tp2':        tp2,
        'sl':         sl,
        'trailing_sl': sl,
        'strategy':   strategy,
        'tp1_hit':    False,
        'dca_done':   False,
        'dca_zone':   dca_z,
        'score':      score
    }
    optimizer.record_signal(symbol, strategy, entry, sl, tp1, tp2, score)
    log(f"SIGNAL: {symbol} {strategy} entry:{entry:.4f} score:{score} rr:1:{rr}")

# ═══════════════════════════════════
# STRATEGIES
# ═══════════════════════════════════
def run_sweep(df, symbol, active_trades, optimizer):
    strat = 'SWEEP'
    try:
        c      = df.iloc[-1]
        p      = df.iloc[-2]
        swing  = df['low'].iloc[-20:-1].min()
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        swept  = p['low'] < swing and c['close'] > swing
        bull   = c['close'] > c['open']
        vol_ok = c['volume'] > vol_ma * 1.2
        log(f"SWEEP {symbol} swept:{swept} bull:{bull} vol:{vol_ok}")
        if swept and bull and vol_ok:
            score = calc_score(df, strat, optimizer, extra_points=2)
            if score >= MIN_SCORE:
                send_signal(active_trades, optimizer, df, symbol, strat, 'RANGING', score)
            else:
                log(f"SWEEP {symbol} score:{score} below {MIN_SCORE} skip")
    except Exception as e:
        log(f"SWEEP ERR: {e}")

def run_ema_pullback(df, symbol, active_trades, optimizer):
    strat = 'EMA_PULLBACK'
    try:
        df2        = df.copy()
        df2['e20'] = calc_ema(df2, 20)
        df2['e50'] = calc_ema(df2, 50)
        df2['e200']= calc_ema(df2, 200)
        vol_ma     = df2['volume'].rolling(20).mean().iloc[-1]
        c          = df2.iloc[-1]
        p          = df2.iloc[-2]
        trend      = c['e50'] > c['e200']
        touched    = p['low'] <= p['e20'] * 1.002
        bounced    = c['close'] > c['e20'] and c['close'] > c['open']
        vol_ok     = c['volume'] > vol_ma
        log(f"EMA {symbol} trend:{trend} touch:{touched} bounce:{bounced} vol:{vol_ok}")
        if touched and bounced and vol_ok:
            extra = 2 if trend else 1
            score = calc_score(df, strat, optimizer, extra_points=extra)
            if score >= MIN_SCORE:
                send_signal(active_trades, optimizer, df, symbol, strat, 'TRENDING', score)
            else:
                log(f"EMA {symbol} score:{score} below {MIN_SCORE} skip")
    except Exception as e:
        log(f"EMA ERR: {e}")

def run_breakout(df, symbol, active_trades, optimizer):
    strat = 'BREAKOUT'
    try:
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        resist = df['high'].iloc[-20:-2].max()
        c      = df.iloc[-1]
        p      = df.iloc[-2]
        broke    = p['close'] > resist and p['volume'] > vol_ma * 1.5
        retested = c['low'] <= resist * 1.002 and c['close'] > resist
        bull     = c['close'] > c['open']
        log(f"BREAKOUT {symbol} broke:{broke} retest:{retested} bull:{bull}")
        if broke and retested and bull:
            score = calc_score(df, strat, optimizer, extra_points=2)
            if score >= MIN_SCORE:
                send_signal(active_trades, optimizer, df, symbol, strat, 'TRENDING', score)
            else:
                log(f"BREAKOUT {symbol} score:{score} below {MIN_SCORE} skip")
    except Exception as e:
        log(f"BREAKOUT ERR: {e}")

def run_day_breakout(df, symbol, active_trades, optimizer):
    strat = 'DAY_BREAKOUT'
    try:
        vol_ma   = df['volume'].rolling(20).mean().iloc[-1]
        day_high = df['high'].iloc[-96:-1].max()
        c        = df.iloc[-1]
        broke_h  = c['close'] > day_high and c['volume'] > vol_ma * 1.3 and c['close'] > c['open']
        log(f"DAY {symbol} day_high:{day_high:.4f} close:{c['close']:.4f} broke:{broke_h}")
        if broke_h:
            score = calc_score(df, strat, optimizer, extra_points=2)
            if score >= MIN_SCORE:
                send_signal(active_trades, optimizer, df, symbol, strat, 'TRENDING', score)
            else:
                log(f"DAY {symbol} score:{score} below {MIN_SCORE} skip")
    except Exception as e:
        log(f"DAY ERR: {e}")

def run_opportunity(df, symbol, active_trades, optimizer):
    strat = 'OPPORTUNITY'
    try:
        c      = df.iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        atr_v  = calc_atr(df).iloc[-1]
        atr_avg= calc_atr(df).mean()
        body   = abs(c['close'] - c['open'])
        avg_b  = abs(df['close'] - df['open']).rolling(20).mean().iloc[-1]
        big_v  = c['volume'] > vol_ma * 2.5
        high_v = atr_v > atr_avg * 1.2
        bull   = c['close'] > c['open']
        log(f"OPP {symbol} big_vol:{big_v} high_atr:{high_v} bull:{bull}")
        if big_v and high_v and bull:
            score = calc_score(df, strat, optimizer, extra_points=1)
            if score >= MIN_SCORE:
                send_signal(active_trades, optimizer, df, symbol, strat, 'ANY', score)
            else:
                log(f"OPP {symbol} score:{score} below {MIN_SCORE} skip")
    except Exception as e:
        log(f"OPP ERR: {e}")

# ═══════════════════════════════════
# MONITOR — TP, TRAILING SL, DCA
# ═══════════════════════════════════
def monitor_trade(df, symbol, active_trades, optimizer):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    log(f"MONITOR {symbol} high:{c['high']:.4f} low:{c['low']:.4f} tp1:{t['tp1']:.4f} tp2:{t['tp2']:.4f} sl:{t['trailing_sl']:.4f}")

    if c['high'] >= t['tp2']:
        notify(
            "TP2 HIT! Full Target!",
            f"Coin: {symbol}\nFull target!\nTP2: {t['tp2']:.4f}\nStrategy: {t['strategy']}\nScore: {t.get('score',0)}/10",
            tags="trophy,fire"
        )
        optimizer.close_trade(symbol, 'win')
        active_trades.pop(symbol, None)
        return

    if c['high'] >= t['tp1'] and not t.get('tp1_hit', False):
        new_sl = t['entry'] * 1.002
        t['tp1_hit']    = True
        t['trailing_sl'] = new_sl
        notify(
            "TP1 HIT! Lock Profit!",
            f"Coin: {symbol}\nTP1: {t['tp1']:.4f} HIT!\nUpdate SL to: {new_sl:.4f} (Profit Zone)\nTrailing SL activated!",
            tags="money_bag,lock"
        )
        log(f"TP1 hit {symbol} trailing SL set to {new_sl:.4f}")

    if t.get('tp1_hit', False):
        trail_step = t['entry'] * 0.002
        new_trail  = c['close'] - trail_step
        if new_trail > t['trailing_sl']:
            t['trailing_sl'] = new_trail
            log(f"TRAIL SL updated {symbol} -> {new_trail:.4f}")

    if c['low'] <= t['trailing_sl']:
        result = 'win' if t.get('tp1_hit', False) else 'loss'
        notify(
            f"SL HIT {'(Profit Locked)' if result == 'win' else ''}",
            f"Coin: {symbol}\nTrailing SL: {t['trailing_sl']:.4f} hit\nResult: {result.upper()}\nStrategy: {t['strategy']}",
        )
        log(f"TP1 hit {symbol} trailing SL set to {new_sl:.4f}")

    if t.get('tp1_hit', False):
        trail_step = t['entry'] * 0.002
        new_trail  = c['close'] - trail_step
        if new_trail > t['trailing_sl']:
            t['trailing_sl'] = new_trail
            log(f"TRAIL SL updated {symbol} -> {new_trail:.4f}")

    if c['low'] <= t['trailing_sl']:
        result = 'win' if t.get('tp1_hit', False) else 'loss'
        notify(
            f"SL HIT {'(Profit Locked)' if result == 'win' else ''}",
            f"Coin: {symbol}\nTrailing SL: {t['trailing_sl']:.4f} hit\nResult: {result.upper()}\nStrategy: {t['strategy']}",
            tags="money_bag" if result == 'win' else "red_circle"
        )
        optimizer.close_trade(symbol, result)
        active_trades.pop(symbol, None)
        return

    if not t.get('dca_done', False) and c['low'] < t['entry'] * 0.995:
        dca_z = t.get('dca_zone', t['entry'] * 0.99)
        notify(
            "DCA Alert!",
            f"Coin: {symbol}\nPrice dropped below entry!\nConsider DCA at: {dca_z:.4f}\nOriginal Entry: {t['entry']:.4f}\nNew Avg Entry if DCA: {((t['entry'] + dca_z) / 2):.4f}",
            tags="warning,chart_with_downwards_trend"
        )
        t['dca_done'] = True
        log(f"DCA alert sent {symbol} zone:{dca_z:.4f}")

# ═══════════════════════════════════
# HOURLY REPORT
# ═══════════════════════════════════
def maybe_hourly(state, active_trades, optimizer, exchange_name):
    if time.time() - state['last_report'] < 3600:
        return
    best_s, best_p = optimizer.get_best()
    active = list(active_trades.keys()) or ["None"]
    notify(
        "Bot Active Alhamdulillah",
        f"Scans: {state['scan_count']}\n"
        f"Active Trades: {', '.join(active)}\n"
        f"Daily Losses: {optimizer.data.get('daily_losses', 0)}/{DAILY_LOSS_LIMIT}\n"
        f"Kill Switch: {'ON' if optimizer.is_kill_switch_active() else 'OFF'}\n"
        f"Exchange: {exchange_name}\n"
        f"Best Strategy: {best_s}\n"
        f"Best Pair: {best_p}",
        tags="robot,white_check_mark"
    )
    state['last_report']  = time.time()
    state['scan_count']   = 0

# ═══════════════════════════════════
# NEWS CHECK
# ═══════════════════════════════════
def is_high_impact_news():
    now = datetime.now(timezone.utc)
    cur = now.hour * 60 + now.minute
    for (h, m) in HIGH_IMPACT_NEWS:
        if abs(cur - (h * 60 + m)) <= NEWS_BLOCK_MIN:
            log(f"High impact news block: {h}:{m:02d} UTC")
            return True
    return False

# ═══════════════════════════════════
# MAIN
# ═══════════════════════════════════
def main():
    exchange, exchange_name = connect_exchange()
    optimizer     = Optimizer()
    active_trades = {}
    start_time    = time.time()
    state         = {'last_report': time.time(), 'scan_count': 0}

    log("Genius Adaptive Sniper Bot starting...")
    notify(
        "Bot Started",
        f"Genius Sniper Bot Live!\nBTC ETH SOL AVAX\nExchange: {exchange_name}\n24/7 Mode | 100% Halal Spot\nMin Score: {MIN_SCORE}/10",
        tags="rocket"
    )
    log("Startup notification sent!")

    while True:
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr done restarting now", tags="arrows_counterclockwise")
            log("Time limit restart")
            break

        try:
            optimizer.daily_report()
            optimizer.reset_daily_losses()
            maybe_hourly(state, active_trades, optimizer, exchange_name)

            now = datetime.now(timezone.utc)
            log(f"TIME {now.strftime('%d-%b %H:%M')} UTC")

            if optimizer.is_kill_switch_active():
                log("KILL SWITCH ON — skip all trading")
                time.sleep(SCAN_SLEEP)
                continue

            if is_high_impact_news():
                log("SKIP: High impact news 10min wait")
                time.sleep(600)
                continue

            state['scan_count'] += 1
            log(f"===== SCAN #{state['scan_count']} START =====")

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

            log(f"===== SCAN #{state['scan_count']} DONE =====")
            log(f"Waiting {SCAN_SLEEP}s...")
            time.sleep(SCAN_SLEEP)

        except Exception as e:
            log(f"MAIN ERR: {e}")
            time.sleep(60)
            continue

if __name__ == '__main__':
    main()
