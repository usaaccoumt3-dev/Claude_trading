
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
NTFY_URL           = "https://ntfy.sh/Mrunknown_786"
SYMBOLS            = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'BNB/USDT']
PERF_FILE          = 'performance.json'
SCAN_SLEEP         = 60
DAILY_LOSS_LIMIT   = 3
CONSEC_LOSS_LIMIT  = 3
RETIRE_HOURS       = 24
COOLDOWN_MINUTES   = 30
API_COOLDOWN_MIN   = 5
STALE_CANDLE_SEC   = 120
ACCOUNT_BALANCE    = 10.0
MAX_RISK_PER_TRADE = 0.005
MAX_TOTAL_RISK     = 0.01
DAILY_PROFIT_TARGET= 0.03
BASE_MIN_CONF      = 60
FLOOR_MIN_CONF     = 45 # This will be effectively ignored by get_min_conf

SCALP_TF1          = '5m'
SCALP_TF2          = '15m'
SWING_TF1          = '1h'
SWING_TF2          = '4h'
SCALP_RR           = 1.5
SCALP_PARTIAL_RR   = 1.2 # Increased from 1.0 for better partial TP
SWING_RR           = 4.0
SWING_PARTIAL_RR   = 2.0

HIGH_IMPACT_NEWS   = [(8,30),(12,30),(14,0),(18,0),(19,0)]
NEWS_BLOCK_MIN     = 45

# ═══════════════════════════════════
# LOG
# ═══════════════════════════════════
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

# ═══════════════════════════════════
# NOTIFY
# ═══════════════════════════════════
def notify(title, msg, tags="chart_with_upwards_trend"):
    try:
        r = requests.post(
            NTFY_URL, data=msg.encode('utf-8'),
            headers={"Title": title, "Priority": "high", "Tags": tags},
            timeout=15
        )
        log(f"NOTIFY: {title} [{r.status_code}]")
    except Exception as e:
        log(f"NOTIFY ERR: {e}")

# ═══════════════════════════════════
# MULTI-EXCHANGE FAILOVER
# Binance first, then Bybit, then MEXC
# ═══════════════════════════════════
EXCHANGE_CONFIGS = [
    ('Binance', ccxt.binance, {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    ('Bybit',   ccxt.bybit,   {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
    ('MEXC',    ccxt.mexc,    {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}),
]

class ExchangeManager:
    def __init__(self):
        self.ex   = None
        self.name = None
        self.idx  = 0
        self.connect()

    def connect(self):
        tried = 0
        while tried < len(EXCHANGE_CONFIGS):
            name, cls, cfg = EXCHANGE_CONFIGS[self.idx % len(EXCHANGE_CONFIGS)]
            try:
                ex = cls(cfg)
                ex.fetch_ticker('BTC/USDT')
                self.ex   = ex
                self.name = name
                log(f"Exchange: {name} connected")
                return
            except Exception as e:
                log(f"Exchange {name} failed: {e}")
                self.idx += 1
                tried    += 1
                time.sleep(2)
        log("FATAL: all exchanges failed")
        exit(1)

    def failover(self):
        log(f"Failover from {self.name}")
        self.idx += 1
        self.connect()

    def fetch(self, symbol, timeframe, limit=300):
        for attempt in range(3):
            try:
                data = self.ex.fetch_ohlcv(symbol, timeframe, limit=limit)
                return data
            except ccxt.PermissionDenied as e:
                log(f"451/Permission error {symbol}: {e} — failover")
                self.failover()
            except ccxt.NetworkError as e:
                log(f"Network error attempt {attempt+1} {symbol}: {e}")
                time.sleep(3)
            except Exception as e:
                log(f"Fetch error attempt {attempt+1} {symbol}: {e}")
                time.sleep(2)
        return None

    def fetch_ob(self, symbol, limit=50):
        for attempt in range(2):
            try:
                return self.ex.fetch_order_book(symbol, limit=limit)
            except Exception as e:
                log(f"OB fetch err {symbol}: {e}")
                time.sleep(2)
        return None

# ═══════════════════════════════════
# DATA INTEGRITY — stale candle check
# ═══════════════════════════════════
def is_stale(data):
    if not data or len(data) == 0:
        return True
    last_ts  = data[-1][0] / 1000
    now_ts   = datetime.now(timezone.utc).timestamp()
    age      = now_ts - last_ts
    if age > STALE_CANDLE_SEC:
        log(f"STALE candle: {age:.0f}s old — skip")
        return True
    return False

def to_df(data):
    df = pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    return df

# ═══════════════════════════════════
# SENTIMENT
# ═══════════════════════════════════
_fng = {'v': 50, 'l': 'Neutral', 't': 0}
def get_fng():
    global _fng
    if time.time() - _fng['t'] < 3600:
        return _fng['v'], _fng['l']
    try:
        r   = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        d   = r.json()['data'][0]
        _fng = {'v': int(d['value']), 'l': d['value_classification'], 't': time.time()}
        log(f"FNG: {_fng['v']} ({_fng['l']})")
    except Exception as e:
        log(f"FNG ERR: {e}")
    return _fng['v'], _fng['l']

def sentiment_boost():
    v, l = get_fng()
    if v <= 20: return 15, l
    if v >= 80: return -10, l
    return 0, l

# ═══════════════════════════════════
# ORDER FLOW
# ═══════════════════════════════════
def analyze_order_flow(exmgr, symbol):
    ob = exmgr.fetch_ob(symbol, 50)
    if not ob:
        return 0, "no_data"
    try:
        bid_vol = sum(b[1] for b in ob['bids'][:20])
        ask_vol = sum(a[1] for a in ob['asks'][:20])
        total   = bid_vol + ask_vol
        if total == 0:
            return 0, "no_liq"
        imb = (bid_vol - ask_vol) / total
        if imb > 0.30:   return 20, "whale_buy"
        if imb > 0.15:   return 10, "buy_pressure"
        if imb < -0.30:  return -20, "whale_sell"
        if imb < -0.15:  return -10, "sell_pressure"
        return 0, "neutral"
    except Exception as e:
        log(f"OF ERR {symbol}: {e}")
        return 0, "error"

# ═══════════════════════════════════
# ML SCORER
# ═══════════════════════════════════
def ml_score(df):
    c     = df.iloc[-1]
    rng   = max(c['high'] - c['low'], 1e-10)
    lwck  = max(c['open'] - c['low'] if c['close'] > c['open'] else c['close'] - c['low'], 0)
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    vol_std = df['volume'].rolling(20).std().iloc[-1]
    vol_z  = (c['volume'] - vol_ma) / max(vol_std, 1e-10)
    score  = 0
    if lwck / rng > 0.4:   score += 8
    if vol_z > 2.0:         score += 10
    elif vol_z > 1.0:       score += 5
    if abs(c['close']-c['open']) / rng > 0.6: score += 5
    return score

# ═══════════════════════════════════
# INDICATORS
# ═══════════════════════════════════
def calc_atr(df, p=14):
    hl  = df['high'] - df['low']
    hpc = abs(df['high'] - df['close'].shift(1))
    lpc = abs(df['low']  - df['close'].shift(1))
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

# ═══════════════════════════════════
# SMC DETECTION
# ═══════════════════════════════════
def find_swing_lows(df, lookback=30):
    lows = []
    for i in range(2, min(lookback, len(df)-1)):
        if df['low'].iloc[-i] < df['low'].iloc[-i-1] and df['low'].iloc[-i] < df['low'].iloc[-i+1]:
            lows.append(df['low'].iloc[-i])
    return lows

def find_swing_highs(df, lookback=30):
    highs = []
    for i in range(2, min(lookback, len(df)-1)):
        if df['high'].iloc[-i] > df['high'].iloc[-i-1] and df['high'].iloc[-i] > df['high'].iloc[-i+1]:
            highs.append(df['high'].iloc[-i])
    return highs

def detect_fvg_bull(df):
    for i in range(3, min(20, len(df)-2)):
        c1 = df.iloc[-i-1]
        c3 = df.iloc[-i+1]
        if c3['low'] > c1['high']:
            return True, c1['high'], c3['low']
    return False, 0, 0

def detect_order_block_bull(df):
    for i in range(3, min(25, len(df)-1)):
        c = df.iloc[-i]
        n = df.iloc[-i+1]
        if c['close'] < c['open'] and n['close'] > n['open']:
            ob_top = c['open']
            ob_bot = c['low']
            cur    = df.iloc[-1]
            if ob_bot <= cur['close'] <= ob_top:
                return True, ob_bot, ob_top
    return False, 0, 0

def detect_liquidity_sweep_bull(df, lookback=30):
    lows = find_swing_lows(df, lookback)
    if not lows:
        return False, 0
    c = df.iloc[-1]
    p = df.iloc[-2]
    for sl_price in lows[:3]:
        if p['low'] < sl_price and c['close'] > sl_price and c['close'] > c['open']:
            return True, sl_price
    return False, 0

def detect_mss_bull(df):
    highs = find_swing_highs(df, 20)
    if not highs:
        return False
    c = df.iloc[-1]
    # Added: Require volume to be higher than average on the break for stronger MSS
    vol_ma = df['volume'].rolling(20).mean().iloc[-1]
    return c['close'] > highs[0] and c['close'] > c['open'] and c['volume'] > vol_ma

def detect_4h_sweep(df):
    lows = find_swing_lows(df, 20)
    if not lows:
        return False, 0
    c = df.iloc[-1]
    p = df.iloc[-2]
    for sl_price in lows[:2]:
        if p['low'] < sl_price and c['close'] > sl_price and c['close'] > c['open']:
            return True, sl_price
    return False, 0

def get_1h_bias(exmgr, symbol):
    try:
        # Increased lookback from 50 to 100 for better context
        data = exmgr.fetch(symbol, '1h', 100)
        if not data or is_stale(data):
            return "NEUTRAL"
        df    = to_df(data)
        highs = find_swing_highs(df, 15)
        lows  = find_swing_lows(df, 15)
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[0] > highs[1]
            hl = lows[0]  > lows[1]
            lh = highs[0] < highs[1]
            ll = lows[0]  < lows[1]
            if hh and hl: return "BULLISH"
            if lh and ll: return "BEARISH"
        return "NEUTRAL"
    except Exception as e:
        log(f"BIAS ERR {symbol}: {e}")
        return "NEUTRAL"

# ═══════════════════════════════════
# SESSION
# ═══════════════════════════════════
def get_session():
    h = datetime.now(timezone.utc).hour
    if 1  <= h < 8:  return "Asia"
    if 8  <= h < 13: return "London"
    if 13 <= h < 18: return "NewYork"
    return "Off"

def is_hv_session():
    s = get_session()
    return s in ["London", "NewYork"]

# ═══════════════════════════════════
# RISK & POSITION SIZING
# ═══════════════════════════════════
def calc_position_size(entry, sl):
    risk_usd  = ACCOUNT_BALANCE * MAX_RISK_PER_TRADE
    risk_pct  = abs(entry - sl) / entry
    if risk_pct == 0:
        return 0
    size_usd  = risk_usd / risk_pct
    return round(size_usd, 2)

def get_total_open_risk(active_trades):
    total = 0
    for sym, t in active_trades.items():
        risk_pct = abs(t['entry'] - t['sl']) / t['entry']
        total   += risk_pct * MAX_RISK_PER_TRADE
    return total

def fee_check(entry, tp):
    return (tp - entry) / entry > 0.002

# ═══════════════════════════════════
# CONFIDENCE
# ═══════════════════════════════════
def calc_confidence(df, strat, storage, symbol, bias, has_sweep, has_ob, has_fvg, has_mss, of_score, of_label, wide=False):
    smc_count = sum([has_sweep, has_ob, has_fvg, has_mss])
    if smc_count == 0:
        return 0, ["no_smc"]

    score, reasons = 0, []
    if has_sweep: score += 30; reasons.append("sweep")
    if has_ob:    score += 25; reasons.append("ob")
    if has_fvg:   score += 15; reasons.append("fvg")
    if has_mss:   score += 10; reasons.append("mss")
    if wide:      score +=  5; reasons.append("wide_zone")

    if bias == "BULLISH":  score += 10; reasons.append("1h_bull")
    elif bias == "BEARISH": score -= 20; reasons.append("1h_bear")

    score += of_score
    if of_score != 0: reasons.append(of_label)

    mls = ml_score(df)
    score += mls
    if mls > 0: reasons.append("ml")

    sa, sl = sentiment_boost()
    score += sa
    if sa != 0: reasons.append(f"fng_{sl.replace(' ','_').lower()}")

    w  = storage.get_weight(strat)
    wr = storage.get_winrate(strat)
    if w >= 1.5:   score += 10; reasons.append("strat_hot")
    elif w >= 1.0: score +=  5; reasons.append("strat_ok")
    else:          score -=  5; reasons.append("strat_cold")
    if wr and wr > 0.65: score += 8; reasons.append("high_wr")

    if storage.data.get('recovery_mode'): score -= 10; reasons.append("recovery")

    score = max(0, min(100, score))
    log(f"CONF {symbol} {strat}: {score}/100 [{','.join(reasons)}]")
    return score, reasons

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
            'strategies': {}, 'pairs': {}, 'sessions': {},
            'daily': {}, 'weekly': {}, 'monthly': {},
            'active_trades': {}, 'missed': [],
            'cooldowns': {}, 'api_cooldowns': {},
            'watchlist': {}, 'daily_losses': 0,
            'last_loss_reset': '', 'kill_switch_until': '',
            'recovery_mode': False, 'standby_mode': False,
            'daily_pnl': 0.0, 'last_pnl_reset': '',
            'last_daily_report': '', 'last_weekly_report': '',
            'last_monthly_report': '', 'trades_today': 0,
            'today_date': '', 'retired': {}, 'alert_tracker': {}
        }

    def save(self):
        try:
            with open(PERF_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log(f"SAVE ERR: {e}")

    def can_notify(self, symbol, event):
        return not self.data['alert_tracker'].get(f"{symbol}|{event}", False)

    def mark_notified(self, symbol, event):
        self.data['alert_tracker'][f"{symbol}|{event}"] = True
        self.save()

    def clear_alerts(self, symbol):
        for ev in ['tp1','tp2','sl','dca','partial']:
            self.data['alert_tracker'].pop(f"{symbol}|{ev}", None)
        self.save()

    def reset_today(self):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self.data.get('today_date') != today:
            self.data.update({'today_date': today, 'trades_today': 0, 'daily_losses': 0, 'daily_pnl': 0.0, 'standby_mode': False})
            self.save()

    def bump_trade(self):
        self.data['trades_today'] = self.data.get('trades_today', 0) + 1
        self.save()

    def get_min_conf(self):
        # Removed relaxed filtering for the first two trades
        return BASE_MIN_CONF  # Always maintain high standards

    def update_daily_pnl(self, pnl_pct):
        self.data['daily_pnl'] = self.data.get('daily_pnl', 0.0) + pnl_pct
        if self.data['daily_pnl'] >= DAILY_PROFIT_TARGET:
            self.data['standby_mode'] = True
            notify("STANDBY MODE", f"Daily profit target {DAILY_PROFIT_TARGET*100:.1f}% reached!\nBot in standby — no new trades.", tags="tada,zzz")
        self.save()

    def is_kill_switch(self):
        ks = self.data.get('kill_switch_until', '')
        if not ks:
            return False
        try:
            u = datetime.fromisoformat(ks)
            if datetime.now(timezone.utc) < u:
                return True
            self.data.update({'kill_switch_until': '', 'recovery_mode': False})
            self.save()
            return False
        except Exception:
            return False

    def activate_kill_switch(self):
        u = datetime.now(timezone.utc) + timedelta(hours=24)
        self.data['kill_switch_until'] = u.isoformat()
        self.data['recovery_mode'] = True
        self.save()
        notify("KILL SWITCH ON", f"Daily loss limit hit!\nStopped 24h\nResumes: {u.strftime('%d-%b %H:%M')} UTC", tags="rotating_light,red_circle")

    def is_api_cooldown(self, symbol):
        last = self.data['api_cooldowns'].get(symbol, '')
        if not last:
            return False
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < API_COOLDOWN_MIN * 60
        except Exception:
            return False

    def set_api_cooldown(self, symbol):
        self.data['api_cooldowns'][symbol] = datetime.now(timezone.utc).isoformat()
        self.save()

    def is_cooldown(self, symbol, strat):
        last = self.data['cooldowns'].get(f"{symbol}_{strat}", '')
        if not last:
            return False
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < COOLDOWN_MINUTES * 60
        except Exception:
            return False

    def set_cooldown(self, symbol, strat):
        self.data['cooldowns'][f"{symbol}_{strat}"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def is_retired(self, strat):
        u = self.data['retired'].get(strat, '')
        if not u:
            return False
        try:
            u = datetime.fromisoformat(u)
            if datetime.now(timezone.utc) < u:
                return True
            self.data['retired'].pop(strat, None)
            self.save()
            return False
        except Exception:
            return False

    def check_auto_heal(self, strat):
        recent = self.data['strategies'].get(strat, {}).get('recent', [])[-CONSEC_LOSS_LIMIT:]
        if len(recent) == CONSEC_LOSS_LIMIT and all(t['r'] == 'loss' for t in recent):
            u = datetime.now(timezone.utc) + timedelta(hours=RETIRE_HOURS)
            self.data['retired'][strat] = u.isoformat()
            self.save()
            notify("Strategy Disabled", f"{strat}: {CONSEC_LOSS_LIMIT} consecutive losses\nDisabled {RETIRE_HOURS}h", tags="warning")
            log(f"AUTO-HEAL: {strat} retired")

    def get_weight(self, strat):
        s      = self.data['strategies'].get(strat, {})
        w      = s.get('weight', 1.0)
        recent = s.get('recent', [])
        if not recent:
            return w
        now, dec, tot = datetime.now(timezone.utc), 0.0, 0.0
        for t in recent[-20:]:
            try:
                age   = (now - datetime.fromisoformat(t['ts'])).total_seconds() / 3600
                decay = max(0.1, 1.0 - age / 48)
                val   = 1.0 if t['r'] == 'win' else -1.0
                dec  += val * decay
                tot  += decay
            except Exception:
                pass
        if tot > 0:
            w = max(0.2, min(2.0, w + (dec/tot) * 0.3))
        return w

    def get_winrate(self, strat):
        s    = self.data['strategies'].get(strat, {})
        w, l = s.get('wins', 0), s.get('losses', 0)
        return w / (w+l) if (w+l) >= 5 else None

    def has_active(self, symbol):
        return symbol in self.data['active_trades']

    def get_active(self, symbol):
        return self.data['active_trades'].get(symbol)

    def record(self, symbol, strat, mode, entry, sl, tp1, tp2, tp_partial, sl_partial, conf, session, size):
        self.data['active_trades'][symbol] = {
            'symbol': symbol, 'strat': strat, 'mode': mode,
            'session': session, 'entry': entry, 'sl': sl,
            'tp1': tp1, 'tp2': tp2,
            'tp_partial': tp_partial, 'sl_partial': sl_partial,
            'conf': conf, 'size': size,
            'tp1_hit': False, 'partial_done': False,
            'trailing_sl': sl, 'be_sl': entry,
            'time': datetime.now(timezone.utc).isoformat()
        }
        self.clear_alerts(symbol)
        self.set_cooldown(symbol, strat)
        self.bump_trade()
        self.save()

    def close(self, symbol, result, pnl_pct=0.0):
        if symbol not in self.data['active_trades']:
            return
        trade = self.data['active_trades'].pop(symbol)
        trade['result'] = result
        trade['closed'] = datetime.now(timezone.utc).isoformat()
        strat   = trade['strat']
        session = trade.get('session', 'Off')
        now     = datetime.now(timezone.utc)
        today   = now.strftime('%Y-%m-%d')
        week    = f"{now.year}-W{now.isocalendar()[1]}"
        month   = now.strftime('%Y-%m')

        self.data['strategies'].setdefault(strat, {'wins': 0, 'losses': 0, 'weight': 1.0, 'recent': []})
        s = self.data['strategies'][strat]
        if result == 'win':
            s['wins']  += 1
            s['weight'] = min(2.0, s['weight'] + 0.1)
        else:
            s['losses'] += 1
            s['weight']  = max(0.2, s['weight'] - 0.1)
            self.data['daily_losses'] = self.data.get('daily_losses', 0) + 1
        s['recent'].append({'r': result, 'ts': trade['closed']})
        s['recent'] = s['recent'][-50:]
        self.check_auto_heal(strat)

        self.data['pairs'].setdefault(symbol, {'wins': 0, 'losses': 0, 'weight': 1.0})
        p = self.data['pairs'][symbol]
        p[result+'s'] += 1
        p['weight'] = min(2.0, p['weight']+0.1) if result=='win' else max(0.2, p['weight']-0.1)

        self.data['sessions'].setdefault(session, {})
        self.data['sessions'][session].setdefault(strat, {'wins': 0, 'losses': 0})
        self.data['sessions'][session][strat][result+'s'] += 1

        self.data['daily'].setdefault(today, {'wins': 0, 'losses': 0, 'trades': []})
        self.data['daily'][today][result+'s'] += 1
        self.data['daily'][today]['trades'].append(trade)

        self.data['weekly'].setdefault(week, {'wins': 0, 'losses': 0})
        self.data['weekly'][week][result+'s'] += 1

        self.data['monthly'].setdefault(month, {'wins': 0, 'losses': 0})
        self.data['monthly'][month][result+'s'] += 1

        self.update_daily_pnl(pnl_pct)

        if self.data.get('daily_losses', 0) >= DAILY_LOSS_LIMIT:
            self.activate_kill_switch()

        self.save()
        log(f"Closed: {symbol} {result} pnl:{pnl_pct:.3f}")

    def get_best(self):
        cands = {k: self.get_weight(k) + (0.5 if (self.get_winrate(k) or 0) > 0.65 else 0)
                 for k in self.data['strategies']}
        bs = max(cands, key=lambda x: cands[x], default='N/A')
        bp = max(self.data['pairs'], key=lambda x: self.data['pairs'][x].get('weight', 1.0), default='N/A')
        return bs, bp

    def send_reports(self):
        now   = datetime.now(timezone.utc)
        today = now.strftime('%Y-%m-%d')
        yest  = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        week  = f"{now.year}-W{now.isocalendar()[1]}"
        lw    = f"{(now-timedelta(weeks=1)).year}-W{(now-timedelta(weeks=1)).isocalendar()[1]}"
        month = now.strftime('%Y-%m')
        lm    = (now - timedelta(days=30)).strftime('%Y-%m')

        if now.hour == 6 and self.data.get('last_daily_report') != today:
            d    = self.data['daily'].get(yest, {})
            w, l = d.get('wins', 0), d.get('losses', 0)
            t    = w + l
            wr   = round(w/t*100, 1) if t else 0
            bs, bp = self.get_best()
            lines = "".join(f"  {sn}: {sv.get('wins',0)}W/{sv.get('losses',0)}L wt:{sv.get('weight',1.0):.1f}\n"
                            for sn, sv in self.data['strategies'].items())
            fv, fl = get_fng()
            notify("Daily Report",
                   f"Date: {yest}\nTotal:{t} W:{w} L:{l} WR:{wr}%\n"
                   f"Daily PnL: {self.data.get('daily_pnl',0)*100:.2f}%\n\n"
                   f"Strategies:\n{lines}\nBest: {bs} | Pair: {bp}\n"
                   f"Fear&Greed: {fv} ({fl})",
                   tags="bar_chart,calendar")
            self.data['last_daily_report'] = today
            self.save()

        if now.weekday() == 0 and now.hour == 7 and self.data.get('last_weekly_report') != week:
            d    = self.data['weekly'].get(lw, {})
            w, l = d.get('wins',0), d.get('losses',0)
            t    = w + l
            notify("Weekly Report", f"Week: {lw}\nTotal:{t} W:{w} L:{l} WR:{round(w/t*100,1) if t else 0}%", tags="bar_chart")
            self.data['last_weekly_report'] = week
            self.save()

        if now.day == 1 and now.hour == 8 and self.data.get('last_monthly_report') != month:
            d    = self.data['monthly'].get(lm, {})
            w, l = d.get('wins',0), d.get('losses',0)
            t    = w + l
            notify("Monthly Report", f"Month: {lm}\nTotal:{t} W:{w} L:{l} WR:{round(w/t*100,1) if t else 0}%", tags="bar_chart,tada")
            self.data['last_monthly_report'] = month
            self.save()

# ═══════════════════════════════════
# SEND SIGNAL
# ═══════════════════════════════════
def send_signal(active_trades, storage, df, symbol, strat, mode, conf, reasons, session, sl_ref, bias):
    c       = df.iloc[-1]
    entry   = c['close']
    atr_v   = calc_atr(df).iloc[-1]
    # Increased ATR multiplier for stop loss from 0.3 to 1.5
    sl      = sl_ref - atr_v * 1.5
    if sl >= entry:
        sl = entry * 0.985

    risk = entry - sl
    if risk <= 0:
        log(f"SKIP {symbol}: invalid risk")
        return

    if mode == 'SCALP':
        tp1         = entry + risk * SCALP_RR
        tp2         = tp1
        # Scalp partial RR increased from 1.0 to 1.2
        tp_partial  = entry + risk * SCALP_PARTIAL_RR
        sl_partial  = entry
    else:
        tp1         = entry + risk * SWING_RR
        tp2         = tp1
        tp_partial  = entry + risk * SWING_PARTIAL_RR
        sl_partial  = entry

    if not fee_check(entry, tp_partial):
        log(f"SKIP {symbol}: fee check fail")
        return

    size = calc_position_size(entry, sl)
    rr   = round((tp1 - entry) / risk, 1)

    msg = (
        f"Coin: {symbol}\nMode: {mode}\nStrategy: {strat}\n"
        f"Session: {session}\nBias: {bias}\nConf: {conf}/100\n"
        f"Signals: {', '.join(reasons[:4])}\n"
        f"Entry: {entry:.4f}\n"
        f"Partial TP: {tp_partial:.4f} (50% close)\n"
        f"Full TP: {tp1:.4f} RR:1:{rr}\n"
        f"SL: {sl:.4f} (-{(risk/entry*100):.2f}%)\n"
        f"Size: ${size:.2f}"
    )
    notify(f"BUY | {mode} | {strat}", msg)
    active_trades[symbol] = {
        'entry': entry, 'sl': sl, 'tp1': tp1,
        'tp_partial': tp_partial, 'sl_partial': sl_partial,
        'trailing_sl': sl, 'be_sl': entry,
        'strat': strat, 'mode': mode, 'session': session,
        'tp1_hit': False, 'partial_done': False, 'conf': conf, 'size': size
    }
    storage.record(symbol, strat, mode, entry, sl, tp1, tp1, tp_partial, sl_partial, conf, session, size)
    log(f"SIGNAL {mode}: {symbol} entry:{entry:.4f} tp:{tp1:.4f} sl:{sl:.4f} rr:1:{rr}")

# ═══════════════════════════════════
# SCALP MODE — 5m/15m, 24/7
# Trigger: FVG, minor OB, MSS
# RR: 1:1.5
# ═══════════════════════════════════
def run_scalp(exmgr, symbol, active_trades, storage, bias, session):
    strat = 'SCALP_SMC'
    if storage.is_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    if storage.has_active(symbol):
        return

    raw5 = exmgr.fetch(symbol, SCALP_TF1, 100)
    if not raw5 or is_stale(raw5):
        storage.set_api_cooldown(symbol)
        return
    df5 = to_df(raw5)

    raw15 = exmgr.fetch(symbol, SCALP_TF2, 200)
    if not raw15 or is_stale(raw15):
        storage.set_api_cooldown(symbol)
        return
    df15 = to_df(raw15)

    fvg_ok, _, _       = detect_fvg_bull(df5)
    ob_ok,  ob_b, ob_t = detect_order_block_bull(df15)
    mss_ok             = detect_mss_bull(df5)
    sweep_ok, sw_low   = detect_liquidity_sweep_bull(df5, 20)

    has_any = fvg_ok or ob_ok or mss_ok or sweep_ok
    if not has_any:
        log(f"SCALP {symbol}: no SMC setup")
        return

    of_sc, of_lb = analyze_order_flow(exmgr, symbol)
    conf, reasons = calc_confidence(
        df15, strat, storage, symbol, bias,
        sweep_ok, ob_ok, fvg_ok, mss_ok,
        of_sc, of_lb,
        wide=storage.data.get('trades_today', 0) < 2
    )

    min_conf = storage.get_min_conf()
    if conf >= min_conf:
        sl_ref = sw_low if sweep_ok else (ob_b if ob_ok else df5['low'].iloc[-5:].min())
        send_signal(active_trades, storage, df15, symbol, strat, 'SCALP', conf, reasons, session, sl_ref, bias)
    else:
        if conf > 0:
            storage.data['missed'].append({'symbol': symbol, 'strat': strat, 'conf': conf, 'time': datetime.now(timezone.utc).isoformat()})
            storage.data['missed'] = storage.data['missed'][-100:]
            storage.save()
        log(f"SCALP {symbol}: conf:{conf} below {min_conf}")

# ═══════════════════════════════════
# SWING MODE — 1H/4H, London/NY only
# Trigger: Daily/4H Liquidity Sweep
# RR: 1:4
# ═══════════════════════════════════
def run_swing(exmgr, symbol, active_trades, storage, bias, session):
    if not is_hv_session():
        return
    strat = 'SWING_SMC'
    if storage.is_retired(strat) or storage.is_cooldown(symbol, strat):
        return
    if storage.has_active(symbol):
        return

    raw1h = exmgr.fetch(symbol, SWING_TF1, 200)
    if not raw1h or is_stale(raw1h):
        storage.set_api_cooldown(symbol)
        return
    df1h = to_df(raw1h)

    raw4h = exmgr.fetch(symbol, SWING_TF2, 100)
    if not raw4h or is_stale(raw4h):
        storage.set_api_cooldown(symbol)
        return
    df4h = to_df(raw4h)

    sweep_1h, sw_low_1h = detect_liquidity_sweep_bull(df1h, 30)
    sweep_4h, sw_low_4h = detect_4h_sweep(df4h)
    ob_ok, ob_b, ob_t   = detect_order_block_bull(df1h)
    fvg_ok, _, _        = detect_fvg_bull(df1h)

    has_any = sweep_1h or sweep_4h or ob_ok
    if not has_any:
        log(f"SWING {symbol}: no major sweep/OB")
        return

    of_sc, of_lb = analyze_order_flow(exmgr, symbol)
    has_sweep    = sweep_1h or sweep_4h
    sw_low       = sw_low_4h if sweep_4h else sw_low_1h

    conf, reasons = calc_confidence(
        df1h, strat, storage, symbol, bias,
        has_sweep, ob_ok, fvg_ok, False,
        of_sc, of_lb,
        wide=storage.data.get('trades_today', 0) < 2
    )

    min_conf = storage.get_min_conf()
    if conf >= min_conf:
        sl_ref = sw_low if has_sweep else ob_b
        send_signal(active_trades, storage, df1h, symbol, strat, 'SWING', conf, reasons, session, sl_ref, bias)
    else:
        if conf > 0:
            storage.data['missed'].append({'symbol': symbol, 'strat': strat, 'conf': conf, 'time': datetime.now(timezone.utc).isoformat()})
            storage.data['missed'] = storage.data['missed'][-100:]
            storage.save()
        log(f"SWING {symbol}: conf:{conf} below {min_conf}")

# ═══════════════════════════════════
# MONITOR — notification lock + break-even
# ═══════════════════════════════════
def monitor_trade(df, symbol, active_trades, storage):
    if symbol not in active_trades:
        return
    t = active_trades[symbol]
    c = df.iloc[-1]
    mode = t.get('mode', 'SCALP')

    # PARTIAL TP — close 50%, move SL to break-even
    if c['high'] >= t['tp_partial'] and not t['partial_done']:
        t['partial_done'] = True
        t['trailing_sl']  = t['sl_partial']
        if storage.can_notify(symbol, 'partial'):
            notify(
                f"PARTIAL TP HIT | {mode}",
                f"Coin: {symbol}\nPartial TP: {t['tp_partial']:.4f} HIT!\nClose 50% of position now!\nSL moved to: {t['sl_partial']:.4f} (Break-Even)\nRemaining target: {t['tp1']:.4f}",
                tags="money_bag,scissors"
            )
            storage.mark_notified(symbol, 'partial')
        log(f"PARTIAL TP {symbol} BE SL:{t['sl_partial']:.4f}")

    # FULL TP
    if c['high'] >= t['tp1']:
        if storage.can_notify(symbol, 'tp1'):
            pnl = (t['tp1'] - t['entry']) / t['entry']
            notify(
                f"FULL TP HIT | {mode}",
                f"Coin: {symbol}\nFull TP: {t['tp1']:.4f}\nStrategy: {t['strat']}\nPnL: +{pnl*100:.2f}%",
                tags="trophy,fire"
            )
            storage.mark_notified(symbol, 'tp1')
        pnl = (t['tp1'] - t['entry']) / t['entry']
        storage.close(symbol, 'win', pnl)
        active_trades.pop(symbol, None)
        return

    # Loose trailing 0.5% buffer ONLY after partial hit
    if t['partial_done']:
        buf       = t['entry'] * 0.005
        new_trail = c['close'] - buf
        if new_trail > t['trailing_sl']:
            t['trailing_sl'] = new_trail
            log(f"TRAIL {symbol} -> {new_trail:.4f}")

    # SL HIT
    if c['low'] <= t['trailing_sl']:
        result = 'win' if t['partial_done'] else 'loss'
        pnl    = (t['trailing_sl'] - t['entry']) / t['entry']
        if storage.can_notify(symbol, 'sl'):
            notify(
                f"SL HIT {'(Secured)' if result=='win' else '(Loss)'} | {mode}",
                f"Coin: {symbol}\nSL: {t['trailing_sl']:.4f}\nResult: {result.upper()}\nStrategy: {t['strat']}\nPnL: {pnl*100:.2f}%",
                tags="money_bag" if result=='win' else "red_circle"
            )
            storage.mark_notified(symbol, 'sl')
        storage.close(symbol, result, pnl)
        active_trades.pop(symbol, None)
        return

    # DCA — once
    if not t.get('dca_done') and c['low'] < t['entry'] * 0.992:
        dca_z = df['low'].iloc[-20:].min()
        if storage.can_notify(symbol, 'dca'):
            notify("DCA Alert!", f"Coin: {symbol}\nPrice below entry!\nDCA Zone: {dca_z:.4f}\nEntry: {t['entry']:.4f}", tags="warning")
            storage.mark_notified(symbol, 'dca')
        t['dca_done'] = True

# ═══════════════════════════════════
# NEWS
# ═══════════════════════════════════
def is_news_time():
    now = datetime.now(timezone.utc)
    cur = now.hour * 60 + now.minute
    return any(abs(cur - (h*60+m)) <= NEWS_BLOCK_MIN for h, m in HIGH_IMPACT_NEWS)

# ═══════════════════════════════════
# HOURLY STATUS
# ═══════════════════════════════════
def hourly_status(state, active_trades, storage, exchange_name):
    if time.time() - state['last_report'] < 3600:
        return
    bs, bp  = storage.get_best()
    active  = list(active_trades.keys()) or ["None"]
    fv, fl  = get_fng()
    notify(
        "Bot Status | Alhamdulillah",
        f"Scans: {state['scans']}\nActive: {', '.join(active)}\n"
        f"Daily Losses: {storage.data.get('daily_losses',0)}/{DAILY_LOSS_LIMIT}\n"
        f"Trades Today: {storage.data.get('trades_today',0)}\n"
        f"Daily PnL: {storage.data.get('daily_pnl',0)*100:.2f}%\n"
        f"Min Conf: {storage.get_min_conf()}\n"
        f"Kill Switch: {'ON' if storage.is_kill_switch() else 'OFF'}\n"
        f"Standby: {'YES' if storage.data.get('standby_mode') else 'NO'}\n"
        f"Exchange: {exchange_name}\n"
        f"Best: {bs} | Pair: {bp}\n"
        f"Fear&Greed: {fv} ({fl})",
        tags="robot,white_check_mark"
    )
    state['last_report'] = time.time()
    state['scans']       = 0

# ═══════════════════════════════════
# MAIN
# ═══════════════════════════════════
def main():
    exmgr         = ExchangeManager()
    storage       = Storage()
    active_trades = {}
    start_time    = time.time()
    state         = {'last_report': time.time(), 'scans': 0}

    log("Hybrid Sniper Bot starting...")
    notify(
        "Hybrid Sniper Bot Started",
        f"SCALP 24/7 + SWING London/NY\n"
        f"BTC ETH SOL AVAX BNB\n"
        f"Exchange: {exmgr.name}\n"
        f"SCALP RR:1:{SCALP_RR} | SWING RR:1:{SWING_RR}\n"
        f"Max Risk/Trade: {MAX_RISK_PER_TRADE*100}%\n"
        f"Daily Target: {DAILY_PROFIT_TARGET*100}%",
        tags="rocket"
    )

    while True:
        if time.time() - start_time > 19800:
            notify("Auto Restart", "5.5hr complete — restarting", tags="arrows_counterclockwise")
            break

        try:
            storage.send_reports()
            storage.reset_today()
            hourly_status(state, active_trades, storage, exmgr.name)

            log(f"TIME {datetime.now(timezone.utc).strftime('%d-%b %H:%M')} UTC | Exchange:{exmgr.name}")

            if storage.is_kill_switch():
                log("KILL SWITCH — skip")
                time.sleep(SCAN_SLEEP)
                continue

            if storage.data.get('standby_mode'):
                log("STANDBY MODE — daily target reached")
                time.sleep(SCAN_SLEEP)
                continue

            if is_news_time():
                log("News block 10min")
                time.sleep(600)
                continue

            session = get_session()
            total_risk = get_total_open_risk(active_trades)
            log(f"Total open risk: {total_risk*100:.2f}% max:{MAX_TOTAL_RISK*100}%")

            state['scans'] += 1
            log(f"=== SCAN #{state['scans']} | {session} | conf_min:{storage.get_min_conf()} ===")

            for symbol in SYMBOLS:
                log(f"-- {symbol} --")

                if storage.is_api_cooldown(symbol):
                    log(f"{symbol}: API cooldown skip")
                    continue

                # Monitor active trade
                if storage.has_active(symbol):
                    raw = exmgr.fetch(symbol, SCALP_TF2, 100)
                    if raw and not is_stale(raw):
                        df_mon = to_df(raw)
                        monitor_trade(df_mon, symbol, active_trades, storage)
                    continue

                # Risk check before new trade
                if total_risk >= MAX_TOTAL_RISK:
                    log(f"Total risk cap reached — skip new trades")
                    break

                try:
                    bias = get_1h_bias(exmgr, symbol)
                    log(f"{symbol} bias:{bias} session:{session}")

                    if bias == "BEARISH":
                        log(f"{symbol}: bearish skip")
                        continue

                    # SCALP — 24/7
                    run_scalp(exmgr, symbol, active_trades, storage, bias, session)

                    # SWING — London/NY only, alongside scalp
                    if not storage.has_active(symbol) or True:
                        run_swing(exmgr, symbol, active_trades, storage, bias, session)

                    total_risk = get_total_open_risk(active_trades)

                except Exception as e:
                    log(f"ERR {symbol}: {e}")
                    storage.set_api_cooldown(symbol)
                    continue

                time.sleep(2)

            log(f"=== SCAN #{state['scans']} DONE | wait {SCAN_SLEEP}s ===")
            time.sleep(SCAN_SLEEP)

        except Exception as e:
            log(f"MAIN ERR: {e}")
            time.sleep(60)
            continue


if __name__ == '__main__':
    main()
