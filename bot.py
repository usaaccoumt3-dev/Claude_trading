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
NTFY_URL          = "https://ntfy.sh/Mrunknown_786"
SYMBOLS           = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'BNB/USDT']
TF_ENTRY          = '15m'
TF_BIAS           = '1h'
PERF_FILE         = 'performance.json'
SCAN_SLEEP        = 60
DAILY_LOSS_LIMIT  = 3
BASE_MIN_CONF     = 55
FLOOR_MIN_CONF    = 40
COOLDOWN_MINUTES  = 30
CONSEC_LOSS_LIMIT = 3
RETIRE_HOURS      = 24

HIGH_IMPACT_NEWS = [(8,30),(12,30),(14,0),(18,0),(19,0)]
NEWS_BLOCK_MIN   = 45

# ═══════════════════════════════════
# LOGGING
# ═══════════════════════════════════
def log(msg):
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    print(f"[{now}] {msg}", flush=True)

# ═══════════════════════════════════
# NOTIFICATION
# ═══════════════════════════════════
def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        r = requests.post(
            NTFY_URL, data=msg.encode('utf-8'),
            headers={"Title": title, "Priority": "high", "Tags": tags},
            timeout=15
        )
        log(f"NOTIF: {title} [{r.status_code}]")
    except Exception as e:
        log(f"NOTIF ERR: {e}")

# ═══════════════════════════════════
# EXCHANGE
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
            log(f"Exchange: {name} connected")
            return ex, name
        except Exception as e:
            log(f"Exchange {name} failed: {e}")
    log("FATAL: all exchanges failed")
    exit(1)

def safe_fetch(exchange, symbol, timeframe, limit=200):
    for attempt in range(3):
        try:
            data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        except Exception as e:
            if '451' in str(e) or '403' in str(e):
                log(f"Binance block! Switching to Bybit for {symbol}")
                try:
                    fallback = ccxt.bybit()
                    data = fallback.fetch_ohlcv(symbol, timeframe, limit=limit)
                    return pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
                except: pass
            log(f"Fetch retry {attempt+1} {symbol}: {e}")
            time.sleep(2)
    return None


# Binance order book — always used just for whale data (public, no key needed)
_binance_public = None
def get_binance_public():
    global _binance_public
    if _binance_public is None:
        try:
            _binance_public = ccxt.binance({'enableRateLimit': True})
        except Exception as e:
            log(f"Binance public init ERR: {e}")
    return _binance_public

# ═══════════════════════════════════
# ORDER FLOW ANALYZER — Whale Detection
# ═══════════════════════════════════
def analyze_order_flow(symbol):
    try:
        ex = get_binance_public()
        if ex is None:
            return 0, "no_data"
        ob = ex.fetch_order_book(symbol, limit=50)
        bid_vol = sum([b[1] for b in ob['bids'][:20]])
        ask_vol = sum([a[1] for a in ob['asks'][:20]])
        total   = bid_vol + ask_vol
        if total == 0:
            return 0, "no_liquidity"
        imbalance = (bid_vol - ask_vol) / total
        if imbalance > 0.25:
            return 15, "whale_buy"
        elif imbalance > 0.10:
            return 8, "buy_pressure"
        elif imbalance < -0.25:
            return -15, "whale_sell"
        elif imbalance < -0.10:
            return -8, "sell_pressure"
        return 0, "neutral"
    except Exception as e:
        log(f"ORDERFLOW ERR {symbol}: {e}")
        return 0, "error"

# ═══════════════════════════════════
# SENTIMENT READER — Fear & Greed
# ═══════════════════════════════════
_fng_cache = {'value': 50, 'label': 'Neutral', 'time': 0}
def get_fear_greed():
    global _fng_cache
    if time.time() - _fng_cache['time'] < 3600:
        return _fng_cache['value'], _fng_cache['label']
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        d = r.json()
        val   = int(d['data'][0]['value'])
        label = d['data'][0]['value_classification']
        _fng_cache = {'value': val, 'label': label, 'time': time.time()}
        log(f"SENTIMENT: {val} ({label})")
        return val, label
    except Exception as e:
        log(f"SENTIMENT ERR: {e}")
        return _fng_cache['value'], _fng_cache['label']

def sentiment_adjustment():
    val, label = get_fear_greed()
    if val <= 20:
        return 10, label
    if val >= 80:
        return -10, label
    return 0, label

# ═══════════════════════════════════
# ML SCORER — candle body + volume spike heuristic
# ═══════════════════════════════════
def ml_score(df):
    c      = df.iloc[-1]
    body   = abs(c['close'] - c['open'])
    rng    = max(c['high'] - c['low'], 1e-10)
    body_r = body / rng
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    vol_z  = (c['volume'] - vol_ma) / max(df['volume'].rolling(20).std().iloc[-1], 1e-10)
    score  = 0
    if body_r > 0.6:
        score += 6
    if vol_z > 1.5:
        score += 8
    elif vol_z > 0.8:
        score += 4
    return score

# ═══════════════════════════════════
# STORAGE
# ═══════════════════════════════════
class Storage:
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
            'strategies': {}, 'pairs': {},
            'sessions': {'Asia': {}, 'London': {}, 'NewYork': {}, 'Off': {}},
            'daily': {}, 'weekly': {}, 'monthly': {},
            'active_trades': {}, 'missed_opportunities': [],
            'signal_cooldowns': {}, 'watchlist_scores': {},
            'daily_losses': 0, 'last_loss_reset': '',
            'kill_switch_until': '', 'recovery_mode': False,
            'last_daily_report': '', 'last_weekly_report': '',
            'last_monthly_report': '', 'trades_found_today': 0,
            'today_date': '', 'retired_strategies': {},
            'notified_events': {}
        }

    def save(self):
        try:
            with open(PERF_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log(f"SAVE ERR: {e}")

    # --- daily trade counter for dynamic confidence ---
    def reset_today_counter(self):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self.data.get('today_date') != today:
            self.data['today_date'] = today
            self.data['trades_found_today'] = 0
            self.save()

    def bump_trade_found(self):
        self.data['trades_found_today'] = self.data.get('trades_found_today', 0) + 1
        self.save()

    def get_min_confidence(self):
        found = self.data.get('trades_found_today', 0)
        if found < 2:
            return FLOOR_MIN_CONF
        return BASE_MIN_CONF

    # --- notification lock ---
    def already_notified(self, symbol, event):
        key = f"{symbol}_{event}"
        return self.data['notified_events'].get(key, False)

    def mark_notified(self, symbol, event):
        key = f"{symbol}_{event}"
        self.data['notified_events'][key] = True
        self.save()

    def clear_notify_lock(self, symbol):
        for ev in ['tp1', 'tp2', 'sl']:
            self.data['notified_events'].pop(f"{symbol}_{ev}", None)
        self.save()

    # --- kill switch ---
    def is_kill_switch(self):
        ks = self.data.get('kill_switch_until', '')
        if not ks:
            return False
        try:
            until = datetime.fromisoformat(ks)
            if datetime.now(timezone.utc) < until:
                return True
            self.data['kill_switch_until'] = ''
            self.data['daily_losses'] = 0
            self.data['recovery_mode'] = False
            self.save()
            return False
        except Exception:
            return False

    def activate_kill_switch(self):
        until = datetime.now(timezone.utc) + timedelta(hours=24)
        self.data['kill_switch_until'] = until.isoformat()
        self.data['recovery_mode'] = True
        self.save()
        notify("KILL SWITCH ON", f"Daily loss limit hit!\nStopped 24hrs\nResumes: {until.strftime('%d-%b %H:%M')} UTC", tags="rotating_light,red_circle")

    def reset_daily(self):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self.data.get('last_loss_reset') != today:
            self.data['daily_losses'] = 0
            self.data['last_loss_reset'] = today
            self.save()

    # --- cooldown ---
    def is_cooldown(self, symbol, strategy):
        key  = f"{symbol}_{strategy}"
        last = self.data['signal_cooldowns'].get(key, '')
        if not last:
            return False
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            return elapsed < COOLDOWN_MINUTES * 60
        except Exception:
            return False

    def set_cooldown(self, symbol, strategy):
        key = f"{symbol}_{strategy}"
        self.data['signal_cooldowns'][key] = datetime.now(timezone.utc).isoformat()
        self.save()

    # --- strategy retirement (auto healer) ---
    def is_strategy_retired(self, strategy):
        until = self.data['retired_strategies'].get(strategy, '')
        if not until:
            return False
        try:
            u = datetime.fromisoformat(until)
            if datetime.now(timezone.utc) < u:
                return True
            self.data['retired_strategies'].pop(strategy, None)
            self.save()
            return False
        except Exception:
            return False

    def check_auto_heal(self, strategy):
        s = self.data['strategies'].get(strategy, {})
        recent = s.get('recent_trades', [])[-CONSEC_LOSS_LIMIT:]
        if len(recent) == CONSEC_LOSS_LIMIT and all(t['result'] == 'loss' for t in recent):
            until = datetime.now(timezone.utc) + timedelta(hours=RETIRE_HOURS)
            self.data['retired_strategies'][strategy] = until.isoformat()
            self.save()
            notify("Strategy Auto-Disabled", f"{strategy} hit {CONSEC_LOSS_LIMIT} losses in a row.\nDisabled for {RETIRE_HOURS}h.", tags="warning")
            log(f"AUTO-HEAL: {strategy} retired until {until}")

    def get_strategy_weight(self, strategy):
        s      = self.data['strategies'].get(strategy, {})
        base_w = s.get('weight', 1.0)
        recent = s.get('recent_trades', [])
        if not recent:
            return base_w
        now, decayed, total_w = datetime.now(timezone.utc), 0.0, 0.0
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
            base_w = max(0.2, min(2.0, base_w + (decayed / total_w) * 0.3))
        return base_w

    def get_strategy_winrate(self, strategy):
        s = self.data['strategies'].get(strategy, {})
        w, l = s.get('wins', 0), s.get('losses', 0)
        if w + l < 5:
            return None
        return w / (w + l)

    def get_pair_weight(self, symbol):
        return self.data['pairs'].get(symbol, {}).get('weight', 1.0)

    def update_watchlist(self, symbol, score):
        self.data['watchlist_scores'].setdefault(symbol, [])
        self.data['watchlist_scores'][symbol].append({'score': score, 'time': datetime.now(timezone.utc).isoformat()})
        self.data['watchlist_scores'][symbol] = self.data['watchlist_scores'][symbol][-20:]

    def get_watchlist_priority(self):
        scores = {}
        for sym, entries in self.data['watchlist_scores'].items():
            if entries:
                recent = entries[-5:]
                scores[sym] = sum(e['score'] for e in recent) / len(recent)
        return sorted(scores.keys(), key=lambda x: scores.get(x, 0), reverse=True)

    def record_signal(self, symbol, strategy, session, entry, sl, tp1, tp2, confidence):
        rr = round((tp1 - entry) / max(entry - sl, 1e-10), 2)
        self.data['active_trades'][symbol] = {
            'symbol': symbol, 'strategy': strategy, 'session': session,
            'entry': entry, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'rr': rr,
            'confidence': confidence, 'status': 'open',
            'tp1_hit': False, 'dca_done': False, 'trailing_sl': sl,
            'time': datetime.now(timezone.utc).isoformat()
        }
        self.clear_notify_lock(symbol)
        self.set_cooldown(symbol, strategy)
        self.bump_trade_found()
        self.save()

    def record_missed(self, symbol, strategy, reason, score):
        self.data['missed_opportunities'].append({'symbol': symbol, 'strategy': strategy, 'reason': reason, 'score': score, 'time': datetime.now(timezone.utc).isoformat()})
        self.data['missed_opportunities'] = self.data['missed_opportunities'][-100:]
        self.save()

    def close_trade(self, symbol, result):
        if symbol not in self.data['active_trades']:
            return
        trade = self.data['active_trades'].pop(symbol)
        trade['result'], trade['status'], trade['closed'] = result, 'closed', datetime.now(timezone.utc).isoformat()
        strat, session = trade['strategy'], trade.get('session', 'Off')
        now = datetime.now(timezone.utc)
        today, week, month = now.strftime('%Y-%m-%d'), f"{now.year}-W{now.isocalendar()[1]}", now.strftime('%Y-%m')

        self.data['strategies'].setdefault(strat, {'wins': 0, 'losses': 0, 'weight': 1.0, 'recent_trades': []})
        s = self.data['strategies'][strat]
        if result == 'win':
            s['wins'] += 1
            s['weight'] = min(2.0, s['weight'] + 0.1)
        else:
            s['losses'] += 1
            s['weight'] = max(0.2, s['weight'] - 0.1)
            self.data['daily_losses'] = self.data.get('daily_losses', 0) + 1
        s['recent_trades'].append({'result': result, 'time': trade['closed']})
        s['recent_trades'] = s['recent_trades'][-50:]
        self.check_auto_heal(strat)

        self.data['pairs'].setdefault(symbol, {'wins': 0, 'losses': 0, 'weight': 1.0})
        p = self.data['pairs'][symbol]
        p[result + 's'] += 1
        p['weight'] = min(2.0, p['weight'] + 0.1) if result == 'win' else max(0.2, p['weight'] - 0.1)

        self.data['sessions'].setdefault(session, {})
        self.data['sessions'][session].setdefault(strat, {'wins': 0, 'losses': 0})
        self.data['sessions'][session][strat][result + 's'] += 1

        self.data['daily'].setdefault(today, {'wins': 0, 'losses': 0, 'trades': []})
        self.data['daily'][today][result + 's'] += 1
        self.data['daily'][today]['trades'].append(trade)

        self.data['weekly'].setdefault(week, {'wins': 0, 'losses': 0})
        self.data['weekly'][week][result + 's'] += 1
        self.data['monthly'].setdefault(month, {'wins': 0, 'losses': 0})
        self.data['monthly'][month][result + 's'] += 1

        if self.data.get('daily_losses', 0) >= DAILY_LOSS_LIMIT:
            self.activate_kill_switch()

        self.save()
        log(f"Trade closed: {symbol} {result}")

    def get_best(self):
        # StrategyHealer — prioritize strategies with win rate > 65%
        candidates = {}
        for k in self.data['strategies']:
            wr = self.get_strategy_winrate(k)
            w  = self.get_strategy_weight(k)
            candidates[k] = w + (0.5 if wr and wr > 0.65 else 0)
        best_s = max(candidates, key=lambda x: candidates[x], default='N/A')
        best_p = max(self.data['pairs'], key=lambda x: self.data['pairs'][x].get('weight', 1.0), default='N/A')
        return best_s, best_p

    def send_reports(self):
        now   = datetime.now(timezone.utc)
        today = now.strftime('%Y-%m-%d')
        yest  = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        week  = f"{now.year}-W{now.isocalendar()[1]}"
        lw    = f"{(now-timedelta(weeks=1)).year}-W{(now-timedelta(weeks=1)).isocalendar()[1]}"
        month = now.strftime('%Y-%m')
        lm    = (now - timedelta(days=30)).strftime('%Y-%m')

        if now.hour == 6 and self.data.get('last_daily_report') != today:
            d  = self.data['daily'].get(yest, {})
            w, l = d.get('wins', 0), d.get('losses', 0)
            t  = w + l
            wr = round(w / t * 100, 1) if t else 0
            best_s, best_p = self.get_best()
            strat_lines = "".join(f"  {sn}: {sv.get('wins',0)}W/{sv.get('losses',0)}L wt:{sv.get('weight',1.0):.1f}\n" for sn, sv in self.data['strategies'].items())
            notify("Daily Report", f"Date: {yest}\nTotal:{t} W:{w} L:{l} WR:{wr}%\n\nStrategies:\n{strat_lines}\nBest Strategy: {best_s}\nBest Pair: {best_p}", tags="bar_chart,calendar")
            self.data['last_daily_report'] = today
            self.save()

        if now.weekday() == 0 and now.hour == 7 and self.data.get('last_weekly_report') != week:
            wd = self.data['weekly'].get(lw, {})
            ww, wl = wd.get('wins', 0), wd.get('losses', 0)
            wt = ww + wl
            wwr = round(ww / wt * 100, 1) if wt else 0
            notify("Weekly Report", f"Week: {lw}\nTotal:{wt} W:{ww} L:{wl} WR:{wwr}%", tags="bar_chart")
            self.data['last_weekly_report'] = week
            self.save()

        if now.day == 1 and now.hour == 8 and self.data.get('last_monthly_report') != month:
            md = self.data['monthly'].get(lm, {})
            mw, ml = md.get('wins', 0), md.get('losses', 0)
            mt = mw + ml
            mwr = round(mw / mt * 100, 1) if mt else 0
            notify("Monthly Report", f"Month: {lm}\nTotal:{mt} W:{mw} L:{ml} WR:{mwr}%", tags="bar_chart,tada")
            self.data['last_monthly_report'] = month
            self.save()

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

def calc_rsi(df, p=14):
    delta = df['close'].diff()
    gain  = delta.where(delta > 0, 0).rolling(p).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(p).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def get_ema_slope(df, p=20):
    e = calc_ema(df, p)
    return (e.iloc[-1] - e.iloc[-5]) / max(abs(e.iloc[-5]), 1e-10) * 100

# ═══════════════════════════════════
# MARKET DETECTION
# ═══════════════════════════════════
def detect_market(df):
    atr_v, atr_avg = calc_atr(df).iloc[-1], calc_atr(df).mean()
    vol_ma  = df['volume'].rolling(20).mean().iloc[-1]
    recent  = df['volume'].iloc[-5:].mean()
    slope   = get_ema_slope(df, 20)
    high_vol, high_atr, trending = recent > vol_ma * 1.5, atr_v > atr_avg * 1.3, abs(slope) > 0.05
    if high_vol and high_atr:
        mkt = "VOLATILE"
    elif trending and high_vol:
        mkt = "TRENDING"
    elif not trending and not high_vol:
        mkt = "QUIET"
    else:
        mkt = "RANGING"
    log(f"MKT: {mkt} slope:{slope:.3f} atr_r:{atr_v/max(atr_avg,1e-10):.2f}")
    return mkt, atr_v, atr_avg

def get_1h_bias(exchange, symbol):
    try:
        data = exchange.fetch_ohlcv(symbol, TF_BIAS, limit=50)
        df   = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
        e20, e50, c = calc_ema(df, 20).iloc[-1], calc_ema(df, 50).iloc[-1], df['close'].iloc[-1]
        if c > e20 and e20 > e50:
            return "BULLISH"
        if c < e20 and e20 < e50:
            return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

# ═══════════════════════════════════
# SESSION
# ═══════════════════════════════════
def get_session():
    h = datetime.now(timezone.utc).hour
    if 1 <= h < 8:   return "Asia"
    if 8 <= h < 13:  return "London"
    if 13 <= h < 18: return "NewYork"
    return "Off"

def session_bonus(storage, session, strategy):
    s = storage.data['sessions'].get(session, {}).get(strategy, {})
    w, l = s.get('wins', 0), s.get('losses', 0)
    if w + l < 3:
        return 0
    wr = w / (w + l)
    return 10 if wr >= 0.7 else 5 if wr >= 0.5 else -5

# ═══════════════════════════════════
# CONFIDENCE — Adaptive + ML + Orderflow + Sentiment
# ═══════════════════════════════════
def calc_confidence(df, strategy, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=0):
    score, reasons = 0, []
    c      = df.iloc[-1]
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]

    if c['volume'] > vol_ma * 2.0:
        score += 20; reasons.append("strong_vol")
    elif c['volume'] > vol_ma * 1.2:
        score += 10; reasons.append("good_vol")

    if c['close'] > c['open']:
        score += 8; reasons.append("bull_candle")

    if atr_v > atr_avg * 1.2:
        score += 10; reasons.append("high_atr")
    elif atr_v < atr_avg * 0.7:
        score -= 5; reasons.append("low_atr")

    slope = get_ema_slope(df, 20)
    if slope > 0.05:
        score += 8; reasons.append("bull_slope")

    rsi_v = calc_rsi(df).iloc[-1]
    if 40 <= rsi_v <= 65:
        score += 5; reasons.append("rsi_ok")
    elif rsi_v > 75:
        score -= 10; reasons.append("overbought")

    w = storage.get_strategy_weight(strategy)
    if w >= 1.5:   score += 15; reasons.append("strat_high")
    elif w >= 1.0: score += 8;  reasons.append("strat_ok")
    else:          score -= 10; reasons.append("strat_low")

    wr = storage.get_strategy_winrate(strategy)
    if wr and wr > 0.65:
        score += 10; reasons.append("high_wr")

    if storage.get_pair_weight(symbol) >= 1.3:
        score += 5; reasons.append("pair_good")

    sb = session_bonus(storage, session, strategy)
    score += sb
    if sb > 0: reasons.append("sess_bonus")

    if bias == "BULLISH":  score += 8; reasons.append("1h_bull")
    elif bias == "BEARISH": score -= 8; reasons.append("1h_bear")

    if mkt in ["TRENDING","VOLATILE"] and strategy in ['EMA_PULLBACK','BREAKOUT','DAY_BREAKOUT']:
        score += 8; reasons.append("mkt_fit")
    elif mkt == "RANGING" and strategy == 'SWEEP':
        score += 8; reasons.append("mkt_fit")
    elif mkt == "QUIET":
        score -= 10; reasons.append("quiet")

    if storage.data.get('recovery_mode'):
        score -= 15; reasons.append("recovery")

    # ML Scorer
    mls = ml_score(df)
    score += mls
    if mls > 0: reasons.append("ml_boost")

    # Order Flow (whale detection)
    of_score, of_label = analyze_order_flow(symbol)
    score += of_score
    if of_score != 0: reasons.append(of_label)

    # Sentiment
    sent_adj, sent_label = sentiment_adjustment()
    score += sent_adj
    if sent_adj != 0: reasons.append(f"fng_{sent_label.lower().replace(' ','_')}")

    score = max(0, min(100, score + extra))
    log(f"CONF {strategy} {symbol}: {score}/100 [{','.join(reasons)}]")
    return score, reasons

# ═══════════════════════════════════
# DYNAMIC TARGETS
# ═══════════════════════════════════
def calc_dynamic_targets(entry, atr_v, atr_avg, confidence):
    is_fast = atr_v > atr_avg * 1.2
    tp1_pct, tp2_pct = (0.02, 0.03) if is_fast else (0.003, 0.005)
    sl  = entry - atr_v * 1.5
    tp1 = entry * (1 + tp1_pct)
    tp2 = entry * (1 + tp2_pct)
    return sl, tp1, tp2

def find_dca_zone(df):
    return df['low'].iloc[-20:].min()

# ═══════════════════════════════════
# SIGNAL SENDER
# ═══════════════════════════════════
def send_signal(active_trades, storage, df, symbol, strategy, market, confidence, reasons, session):
    c       = df.iloc[-1]
    entry   = c['close']
    atr_v, atr_avg = calc_atr(df).iloc[-1], calc_atr(df).mean()
    sl, tp1, tp2 = calc_dynamic_targets(entry, atr_v, atr_avg, confidence)
    rr        = round((tp1 - entry) / max(entry - sl, 1e-10), 1)
    dca_z     = find_dca_zone(df)
    profit_sl = entry * 1.002
    msg = (
        f"Coin: {symbol}\nStrategy: {strategy}\nMarket: {market}\nSession: {session}\n"
        f"Confidence: {confidence}/100\nSignals: {', '.join(reasons[:4])}\n"
        f"Entry: {entry:.4f}\nTP1: {tp1:.4f} (+{((tp1-entry)/entry*100):.2f}%)\n"
        f"TP2: {tp2:.4f} (+{((tp2-entry)/entry*100):.2f}%)\nSL: {sl:.4f} (-{((entry-sl)/entry*100):.2f}%)\n"
        f"DCA: {dca_z:.4f}\nRR: 1:{rr}"
    )
    notify(f"BUY | {strategy}", msg)
    active_trades[symbol] = {
        'entry': entry, 'tp1': tp1, 'tp2': tp2, 'sl': sl,
        'trailing_sl': sl, 'profit_sl': profit_sl,
        'strategy': strategy, 'session': session,
        'tp1_hit': False, 'dca_done': False,
        'dca_zone': dca_z, 'confidence': confidence
    }
    storage.record_signal(symbol, strategy, session, entry, sl, tp1, tp2, confidence)
    storage.update_watchlist(symbol, confidence)
    log(f"SIGNAL: {symbol} {strategy} conf:{confidence} entry:{entry:.4f} rr:1:{rr}")

# ═══════════════════════════════════
# STRATEGIES
# ═══════════════════════════════════
def run_sweep(df, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias):
    strat = 'SWEEP'
    if storage.is_strategy_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    try:
        c, p = df.iloc[-1], df.iloc[-2]
        swing  = df['low'].iloc[-20:-1].min()
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        swept  = p['low'] < swing and c['close'] > swing
        bull   = c['close'] > c['open']
        vol_ok = c['volume'] > vol_ma * 1.2
        if swept and bull and vol_ok:
            conf, reasons = calc_confidence(df, strat, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=10)
            min_conf = storage.get_min_confidence()
            if conf >= min_conf:
                send_signal(active_trades, storage, df, symbol, strat, mkt, conf, reasons, session)
            else:
                storage.record_missed(symbol, strat, f"low_conf:{conf}", conf)
    except Exception as e:
        log(f"SWEEP ERR: {e}")

def run_ema_pullback(df, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias):
    strat = 'EMA_PULLBACK'
    if storage.is_strategy_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    try:
        df2 = df.copy()
        df2['e20'], df2['e50'], df2['e200'] = calc_ema(df2,20), calc_ema(df2,50), calc_ema(df2,200)
        vol_ma = df2['volume'].rolling(20).mean().iloc[-1]
        c, p   = df2.iloc[-1], df2.iloc[-2]
        trend   = c['e50'] > c['e200']
        touched = p['low'] <= p['e20'] * 1.002
        bounced = c['close'] > c['e20'] and c['close'] > c['open']
        vol_ok  = c['volume'] > vol_ma
        if touched and bounced and vol_ok:
            extra = 12 if trend else 5
            conf, reasons = calc_confidence(df, strat, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=extra)
            min_conf = storage.get_min_confidence()
            if conf >= min_conf:
                send_signal(active_trades, storage, df, symbol, strat, mkt, conf, reasons, session)
            else:
                storage.record_missed(symbol, strat, f"low_conf:{conf}", conf)
    except Exception as e:
        log(f"EMA ERR: {e}")

def run_breakout(df, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias):
    strat = 'BREAKOUT'
    if storage.is_strategy_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    try:
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        resist = df['high'].iloc[-20:-2].max()
        c, p   = df.iloc[-1], df.iloc[-2]
        broke    = p['close'] > resist and p['volume'] > vol_ma * 1.5
        retested = c['low'] <= resist * 1.002 and c['close'] > resist
        bull     = c['close'] > c['open']
        if broke and retested and bull:
            conf, reasons = calc_confidence(df, strat, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=12)
            min_conf = storage.get_min_confidence()
            if conf >= min_conf:
                send_signal(active_trades, storage, df, symbol, strat, mkt, conf, reasons, session)
            else:
                storage.record_missed(symbol, strat, f"low_conf:{conf}", conf)
    except Exception as e:
        log(f"BREAKOUT ERR: {e}")

def run_day_breakout(df, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias):
    strat = 'DAY_BREAKOUT'
    if storage.is_strategy_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    try:
        vol_ma   = df['volume'].rolling(20).mean().iloc[-1]
        day_high = df['high'].iloc[-96:-1].max()
        c        = df.iloc[-1]
        broke_h  = c['close'] > day_high and c['volume'] > vol_ma * 1.3 and c['close'] > c['open']
        if broke_h:
            conf, reasons = calc_confidence(df, strat, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=10)
            min_conf = storage.get_min_confidence()
            if conf >= min_conf:
                send_signal(active_trades, storage, df, symbol, strat, mkt, conf, reasons, session)
            else:
                storage.record_missed(symbol, strat, f"low_conf:{conf}", conf)
    except Exception as e:
        log(f"DAY ERR: {e}")

def run_opportunity(df, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias):
    strat = 'OPPORTUNITY'
    if storage.is_cooldown(symbol, strat):
        return
    try:
        c      = df.iloc[-1]
        vol_ma = df['volume'].rolling(20).mean().iloc[-1]
        body   = abs(c['close'] - c['open'])
        avg_b  = abs(df['close'] - df['open']).rolling(20).mean().iloc[-1]
        big_v  = c['volume'] > vol_ma * 2.5
        big_c  = body > avg_b * 2.0
        bull   = c['close'] > c['open']
        if big_v and big_c and bull:
            conf, reasons = calc_confidence(df, strat, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=5)
            min_conf = storage.get_min_confidence()
            if conf >= min_conf:
                send_signal(active_trades, storage, df, symbol, strat, mkt, conf, reasons, session)
    except Exception as e:
        log(f"OPP ERR: {e}")

# AI_WHALE — triggers purely on order book imbalance, even if other strategies neutral
def run_ai_whale(df, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias):
    strat = 'AI_WHALE'
    if storage.is_strategy_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    try:
        of_score, of_label = analyze_order_flow(symbol)
        c = df.iloc[-1]
        if of_label == "whale_buy" and c['close'] > c['open']:
            conf, reasons = calc_confidence(df, strat, storage, session, bias, mkt, atr_v, atr_avg, symbol, extra=15)
            min_conf = storage.get_min_confidence()
            if conf >= min_conf:
                send_signal(active_trades, storage, df, symbol, strat, mkt, conf, reasons, session)
            else:
                storage.record_missed(symbol, strat, f"low_conf:{conf}", conf)
    except Exception as e:
        log(f"WHALE ERR: {e}")

# ═══════════════════════════════════
# MONITOR — Notification Lock + Smart Break-Even Trailing
# ═══════════════════════════════════
def monitor_trade(df, symbol, active_trades, storage):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    log(f"MON {symbol} H:{c['high']:.4f} L:{c['low']:.4f} TSL:{t['trailing_sl']:.4f}")

    # TP2
    if c['high'] >= t['tp2']:
        if not storage.already_notified(symbol, 'tp2'):
            notify("TP2 HIT! Full Target!", f"Coin: {symbol}\nTP2: {t['tp2']:.4f}\nStrategy: {t['strategy']}\nConf: {t.get('confidence',0)}/100", tags="trophy,fire")
            storage.mark_notified(symbol, 'tp2')
        storage.close_trade(symbol, 'win')
        active_trades.pop(symbol, None)
        return

    # TP1 — move SL to break-even, NOT trailing yet
    if c['high'] >= t['tp1'] and not t.get('tp1_hit'):
        t['tp1_hit']     = True
        t['trailing_sl'] = t['profit_sl']   # break-even + small buffer
        if not storage.already_notified(symbol, 'tp1'):
            notify("TP1 HIT! Break-Even Set!", f"Coin: {symbol}\nTP1: {t['tp1']:.4f} HIT!\nSL moved to Break-Even: {t['profit_sl']:.4f}\nLoose trailing now active.", tags="money_bag,lock")
            storage.mark_notified(symbol, 'tp1')
        log(f"TP1 hit {symbol} SL->BE {t['profit_sl']:.4f}")

    # Loose trailing AFTER TP1 only, with 0.5% buffer to avoid noise exits
    if t.get('tp1_hit'):
        buffer    = t['entry'] * 0.005
        new_trail = c['close'] - buffer
        if new_trail > t['trailing_sl']:
            t['trailing_sl'] = new_trail
            log(f"TRAIL updated {symbol} -> {new_trail:.4f}")

    # SL hit
    if c['low'] <= t['trailing_sl']:
        result = 'win' if t.get('tp1_hit') else 'loss'
        event  = 'sl'
        if not storage.already_notified(symbol, event):
            notify(
                f"SL HIT {'(Profit Locked)' if result == 'win' else '(Loss)'}",
                f"Coin: {symbol}\nSL: {t['trailing_sl']:.4f}\nResult: {result.upper()}\nStrategy: {t['strategy']}",
                tags="money_bag" if result == 'win' else "red_circle"
            )
            storage.mark_notified(symbol, event)
        storage.close_trade(symbol, result)
        active_trades.pop(symbol, None)
        return

    # DCA alert — once
    if not t.get('dca_done') and c['low'] < t['entry'] * 0.995:
        dca_z = t.get('dca_zone', t['entry'] * 0.99)
        notify("DCA Alert!", f"Coin: {symbol}\nPrice below entry!\nDCA Zone: {dca_z:.4f}\nEntry: {t['entry']:.4f}\nAvg if DCA: {((t['entry']+dca_z)/2):.4f}", tags="warning")
        t['dca_done'] = True

# ═══════════════════════════════════
# HOURLY STATUS
# ═══════════════════════════════════
def hourly_status(state, active_trades, storage, exchange_name):
    if time.time() - state['last_report'] < 3600:
        return
    best_s, best_p = storage.get_best()
    active   = list(active_trades.keys()) or ["None"]
    priority = storage.get_watchlist_priority()[:3] or ["N/A"]
    fng_val, fng_label = get_fear_greed()
    notify(
        "Bot Active Alhamdulillah",
        f"Scans: {state['scan_count']}\nActive: {', '.join(active)}\n"
        f"Daily Losses: {storage.data.get('daily_losses',0)}/{DAILY_LOSS_LIMIT}\n"
        f"Kill Switch: {'ON' if storage.is_kill_switch() else 'OFF'}\n"
        f"Min Confidence: {storage.get_min_confidence()}\n"
        f"Exchange: {exchange_name}\nBest Strategy: {best_s}\nBest Pair: {best_p}\n"
        f"Top Watch: {', '.join(priority)}\nFear&Greed: {fng_val} ({fng_label})",
        tags="robot,white_check_mark"
    )
    state['last_report'] = time.time()
    state['scan_count']  = 0

# ═══════════════════════════════════
# NEWS
# ═══════════════════════════════════
def is_high_impact_news():
    now = datetime.now(timezone.utc)
    cur = now.hour * 60 + now.minute
    return any(abs(cur - (h*60+m)) <= NEWS_BLOCK_MIN for h, m in HIGH_IMPACT_NEWS)

# ═══════════════════════════════════
# MAIN
# ═══════════════════════════════════
def main():
    exchange, exchange_name = connect_exchange()
    storage       = Storage()
    active_trades = {}
    start_time    = time.time()
    state         = {'last_report': time.time(), 'scan_count': 0}

    log("Genius Institutional Sniper Bot v3 starting...")
    notify("Bot Started", f"Genius Sniper v3 Live!\nBTC ETH SOL AVAX BNB\nExchange: {exchange_name}\nWhale+Sentiment+ML enabled\n24/7 Halal Spot", tags="rocket")

    while True:
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr done restarting", tags="arrows_counterclockwise")
            break

        try:
            storage.send_reports()
            storage.reset_daily()
            storage.reset_today_counter()
            hourly_status(state, active_trades, storage, exchange_name)

            now = datetime.now(timezone.utc)
            log(f"TIME {now.strftime('%d-%b %H:%M')} UTC")

            if storage.is_kill_switch():
                log("KILL SWITCH — skip trading")
                time.sleep(SCAN_SLEEP)
                continue

            if is_high_impact_news():
                log("News block 10min")
                time.sleep(600)
                continue

            session = get_session()
            state['scan_count'] += 1
            log(f"=== SCAN #{state['scan_count']} | {session} | min_conf:{storage.get_min_confidence()} ===")

            priority_syms = storage.get_watchlist_priority()
            scan_order = [s for s in priority_syms if s in SYMBOLS] + [s for s in SYMBOLS if s not in priority_syms]

            for symbol in scan_order:
                log(f"-- {symbol} --")
                df15 = safe_fetch(exchange, symbol, TF_ENTRY)
                if df15 is None or df15.empty or len(df15) < 100:
                    log(f"{symbol}: no data skip")
                    continue

                if symbol in active_trades:
                    monitor_trade(df15, symbol, active_trades, storage)
                    continue

                try:
                    mkt, atr_v, atr_avg = detect_market(df15)
                    bias = get_1h_bias(exchange, symbol)
                    log(f"{symbol} mkt:{mkt} bias:{bias}")

                    if mkt == "QUIET":
                        log(f"{symbol}: quiet skip")
                        continue

                    if mkt in ["RANGING", "VOLATILE"]:
                        run_sweep(df15, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias)
                    if symbol not in active_trades:
                        run_ema_pullback(df15, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias)
                    if symbol not in active_trades:
                        run_breakout(df15, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias)
                    if symbol not in active_trades:
                        run_day_breakout(df15, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias)
                    if symbol not in active_trades:
                        run_ai_whale(df15, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias)
                    if symbol not in active_trades:
                        run_opportunity(df15, symbol, active_trades, storage, mkt, atr_v, atr_avg, session, bias)

                    storage.update_watchlist(symbol, atr_v / max(atr_avg, 1e-10) * 50)

                except Exception as e:
                    log(f"Symbol ERR {symbol}: {e}")
                    continue

                time.sleep(1)

            log(f"=== SCAN #{state['scan_count']} DONE | wait {SCAN_SLEEP}s ===")
            time.sleep(SCAN_SLEEP)

        except Exception as e:
            log(f"MAIN ERR: {e}")
            time.sleep(60)
            continue

if __name__ == '__main__':
    main()
