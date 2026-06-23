import time
import requests
import ccxt
import pandas as pd
from datetime import datetime

NTFY_URL = "https://ntfy.sh/raokaif_secret_trading_786"
SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']

exchange = ccxt.mexc()

def notify(title, msg):
    try:
        requests.post(NTFY_URL, data=msg.encode('utf-8'), headers={"Title": title, "Priority": "high"})
    except: pass

def get_df(symbol):
    try:
        data = exchange.fetch_ohlcv(symbol, '15m', limit=100)
        return pd.DataFrame(data, columns=['ts','open','high','low','close','volume'])
    except: return None

def run():
    while True:
        for symbol in SYMBOLS:
            df = get_df(symbol)
            if df is None: continue
            
            c = df.iloc[-1]
            atr = (df['high'] - df['low']).mean()
            
            # Bullish FVG/Sweep logic
            if c['close'] > df['close'].iloc[-2]: 
                entry = c['close']
                sl = entry - (atr * 1.5)
                tp = entry + (atr * 3.0)
                
                msg = (f"Coin: {symbol}\n"
                       f"Action: BUY (Bullish Signal)\n"
                       f"Entry: {entry:.4f}\n"
                       f"SL: {sl:.4f}\n"
                       f"TP: {tp:.4f}")
                notify("SIGNAL DETECTED", msg)
        
        time.sleep(900)

if __name__ == '__main__':
    run()
    
