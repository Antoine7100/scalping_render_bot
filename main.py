import ccxt
import os
import pandas as pd
import time
import logging
import matplotlib.pyplot as plt
from datetime import datetime
import requests
import numpy as np
from flask import Flask, jsonify
import threading

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Clés API
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# Clés Telegram
TELEGRAM_BOT_TOKEN = "7558300482:AAGu9LaSHOYlfvfxI5uWbC19bgzOXJx6oCQ"
TELEGRAM_CHAT_ID = "1440739670"

# Initialiser Bybit
exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

symbol = "ADA/USDT:USDT"
leverage = 3

try:
    exchange.set_leverage(leverage, symbol)
except Exception as e:
    logging.warning(f"⚠️ Impossible de définir le levier : {e}")

# Configuration
timeframe = '1m'
limit = 100
profit_target = 0.05
stop_loss_percent = 0.01
trailing_stop_trigger = 0.015
trailing_stop_distance = 0.007
log_file = "trades_log.csv"

# Variables d'état
active_position = False
entry_price = 0.0
highest_price = 0.0
last_order_info = {}

# Interface Web
app = Flask(__name__)

@app.route("/")
def index():
    return "<h2>Bot actif. Voir <a href='/trades'>/trades</a> pour les trades du jour.</h2>"

@app.route("/status")
def status():
    return jsonify({
        "active_position": active_position,
        "entry_price": entry_price,
        "highest_price": highest_price,
        "last_order": last_order_info
    })

@app.route("/trades")
def trades():
    if not os.path.exists(log_file):
        return "<h3>Pas de trades.</h3>"
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    today_trades = df[df['datetime'].dt.date == today]
    if today_trades.empty:
        return "<h3>Pas de trades aujourd'hui.</h3>"
    pnl = daily_pnl()
    html = f"<h1>📈 Trades du jour - PNL: {pnl} USDT</h1><table border='1'><tr><th>Date</th><th>Action</th><th>Prix</th><th>Quantité</th><th>TP</th><th>SL</th></tr>"
    for _, row in today_trades.iterrows():
        html += f"<tr><td>{row['datetime']}</td><td>{row['action']}</td><td>{row['price']}</td><td>{row['qty']}</td><td>{row['take_profit']}</td><td>{row['stop_loss']}</td></tr>"
    html += "</table>"
    return html

def daily_pnl():
    if not os.path.exists(log_file):
        return 0
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    today_trades = df[df['datetime'].dt.date == today]
    buys = today_trades[today_trades['action'].str.startswith("BUY")]['price'] * today_trades['qty']
    sells = today_trades[today_trades['action'].str.startswith("SELL")]['price'] * today_trades['qty']
    return round(sells.sum() - buys.sum(), 2)

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

def get_indicators(df):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['ema'] = df['close'].ewm(span=20, adjust=False).mean()
    return df

def log_trade(action, price, qty, tp, sl):
    df = pd.DataFrame([[datetime.now(), action, price, qty, tp, sl]], columns=["datetime", "action", "price", "qty", "take_profit", "stop_loss"])
    if os.path.exists(log_file):
        df.to_csv(log_file, mode='a', header=False, index=False)
    else:
        df.to_csv(log_file, mode='w', header=True, index=False)
    send_telegram_message(f"📝 Trade enregistré : {action} à {price} USDT, quantité: {qty}")

# Fonction principale
def run():
    global active_position, entry_price, highest_price, last_order_info
    try:
        df = get_ohlcv()
        df = get_indicators(df)

        high = df['high'].max()
        low = df['low'].min()
        last_price = df['close'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        ema = df['ema'].iloc[-1]

        logging.info(f"Prix: {last_price:.4f} | RSI: {rsi:.2f} | EMA: {ema:.4f}")

        balance = exchange.fetch_balance()
        available_usdt = balance['total']['USDT']

        if available_usdt < 1:
            logging.warning("Solde insuffisant pour trader.")
            return

        position_size = available_usdt * leverage
        amount_qty = max(round(position_size / last_price, 2), 10.0)

        if not active_position:
            buy_signal = (rsi < 65 and last_price > ema)

            if buy_signal:
                order = exchange.create_market_buy_order(symbol, amount_qty)
                logging.info(f"💵 Achat exécuté: {order['amount']} ADA à {last_price:.4f}")
                entry_price = last_price
                highest_price = last_price
                active_position = True
                last_order_info = order

                tp = round(entry_price * (1 + profit_target), 4)
                sl = round(entry_price * (1 - stop_loss_percent), 4)

                send_telegram_message(f"✅ Achat {amount_qty} ADA à {entry_price} USDT\n🎯 TP: {tp} | 🛑 SL: {sl}")
                log_trade("BUY", entry_price, amount_qty, tp, sl)

        else:
            if last_price > highest_price:
                highest_price = last_price

            trigger_price = entry_price * (1 + trailing_stop_trigger)
            if last_price >= trigger_price:
                trailing_sl = highest_price * (1 - trailing_stop_distance)
                if last_price <= trailing_sl:
                    exchange.create_market_sell_order(symbol, amount_qty)
                    active_position = False
                    send_telegram_message(f"🔽 Trailing stop vendu à {last_price:.4f} USDT")
                    log_trade("SELL-TRAIL", last_price, amount_qty, highest_price, trailing_sl)

            if last_price <= entry_price * (1 - stop_loss_percent):
                exchange.create_market_sell_order(symbol, amount_qty)
                active_position = False
                send_telegram_message(f"🚨 Stop Loss vendu à {last_price:.4f} USDT")
                log_trade("SELL-SL", last_price, amount_qty, highest_price, entry_price * (1 - stop_loss_percent))

            if last_price >= entry_price * (1 + profit_target):
                exchange.create_market_sell_order(symbol, amount_qty)
                active_position = False
                send_telegram_message(f"💰 Take Profit atteint à {last_price:.4f} USDT")
                log_trade("SELL-TP", last_price, amount_qty, entry_price * (1 + profit_target), entry_price * (1 - stop_loss_percent))

    except Exception as e:
        logging.error(f"Erreur dans run(): {e}")
        send_telegram_message(f"❌ Erreur dans le bot : {e}")

# Démarrage Flask
threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 10000}).start()

# Boucle principale
while True:
    run()
    time.sleep(30)


