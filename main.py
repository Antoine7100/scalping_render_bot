import ccxt
import os
import pandas as pd
import time
import logging
from datetime import datetime
import requests

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Clés API (à définir dans l'environnement)
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# Telegram (à personnaliser)
TELEGRAM_BOT_TOKEN = "TON_TOKEN"
TELEGRAM_CHAT_ID = "TON_CHAT_ID"

# Initialisation Bybit
exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

symbol = "BTC/USDT"
timeframe = '1h'
limit = 100
risk_percent = 0.02
profit_target = 0.02
stop_loss_percent = 0.015
log_file = "trades_log.csv"

def send_telegram_message(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

def fibonacci_levels(high, low):
    diff = high - low
    return {
        "0.236": high - diff * 0.236,
        "0.382": high - diff * 0.382,
        "0.5": high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "0.786": high - diff * 0.786
    }

def get_ohlcv():
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def log_trade(action, price, qty, tp, sl):
    df = pd.DataFrame([[datetime.now(), action, price, qty, tp, sl]], columns=["datetime", "action", "price", "qty", "take_profit", "stop_loss"])
    if os.path.exists(log_file):
        df.to_csv(log_file, mode='a', header=False, index=False)
    else:
        df.to_csv(log_file, mode='w', header=True, index=False)

def run():
    df = get_ohlcv()
    high = df['high'].max()
    low = df['low'].min()
    last_price = df['close'].iloc[-1]
    fibs = fibonacci_levels(high, low)
    open_price = df['open'].iloc[-1]

    logging.info(f"Prix actuel: {last_price:.2f} USDT")
    logging.info(f"Niveaux Fibonacci: {fibs}")

    amount_usdt = 20
    amount_qty = round(amount_usdt / last_price, 5)

    # Achat agressif si le prix est sous plusieurs niveaux Fibonacci
    for level in ['0.786', '0.618', '0.5']:
        if last_price <= fibs[level]:
            try:
                order = exchange.create_market_buy_order(symbol, amount_qty)
                tp = round(last_price * (1 + profit_target), 2)
                sl = round(last_price * (1 - stop_loss_percent), 2)
                send_telegram_message(f"✅ LONG: {amount_qty} BTC @ {last_price} (TP: {tp}, SL: {sl})")
                log_trade("BUY", last_price, amount_qty, tp, sl)
                break
            except Exception as e:
                send_telegram_message(f"❌ Erreur achat : {e}")
                break

    # Vente agressive (short) si prix est au-dessus de certains niveaux
    for level in ['0.236', '0.382']:
        if last_price >= fibs[level]:
            try:
                order = exchange.create_market_sell_order(symbol, amount_qty)
                tp = round(last_price * (1 - profit_target), 2)
                sl = round(last_price * (1 + stop_loss_percent), 2)
                send_telegram_message(f"🔻 SHORT: {amount_qty} BTC @ {last_price} (TP: {tp}, SL: {sl})")
                log_trade("SELL", last_price, amount_qty, tp, sl)
                break
            except Exception as e:
                send_telegram_message(f"❌ Erreur short : {e}")
                break

if __name__ == "__main__":
    while True:
        run()
        time.sleep(60)

