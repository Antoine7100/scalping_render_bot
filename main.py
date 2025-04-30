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

# Initialiser Bybit en Perp
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
    logging.warning(f"⚠️ Levier non modifié : {e}")

timeframe = '1m'
limit = 100
profit_target = 0.05
stop_loss_percent = 0.01
trailing_stop_trigger = 0.015
trailing_stop_distance = 0.007
log_file = "trades_log.csv"

active_position = False
entry_price = 0.0
highest_price = 0.0
last_order_info = {}

# Suivi des performances
trade_count = 0
trade_wins = 0
trade_losses = 0
last_trade_type = ""

app = Flask(__name__)

@app.route("/")
def index():
    return "<h2>Bot actif - Voir <a href='/trades'>/trades</a> et <a href='/status'>/status</a></h2>"

@app.route("/trades")
def trades():
    if not os.path.exists(log_file):
        return "<h2>Aucun trade enregistré.</h2>"
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    today_trades = df[df['datetime'].dt.date == today]
    if today_trades.empty:
        return "<h2>Aucun trade exécuté aujourd'hui.</h2>"
    html = "<table border='1'><tr><th>Date</th><th>Action</th><th>Prix</th><th>Qté</th><th>TP</th><th>SL</th></tr>"
    for _, row in today_trades.iterrows():
        html += f"<tr><td>{row['datetime']}</td><td>{row['action']}</td><td>{row['price']}</td><td>{row['qty']}</td><td>{row['take_profit']}</td><td>{row['stop_loss']}</td></tr>"
    html += "</table>"
    return html

@app.route("/status")
def status():
    global active_position, entry_price, highest_price, trade_count, trade_wins, trade_losses, last_trade_type

    if active_position:
        tp = round(entry_price * 1.02, 4)
        sl = round(entry_price * 0.985, 4)
        status_html = f"""
        <h2>📊 Statut du Bot</h2>
        <ul>
            <li>✅ Position ouverte</li>
            <li>💰 Prix d'entrée : {entry_price:.4f} USDT</li>
            <li>📈 Plus haut atteint : {highest_price:.4f} USDT</li>
            <li>🌟 TP : {tp:.4f} | ⛔ SL : {sl:.4f}</li>
        </ul>
        """
    else:
        status_html = "<h2>📊 Statut du Bot</h2><ul><li>❌ Aucune position ouverte actuellement</li></ul>"

    stats_html = f"""
    <h3>📈 Performance</h3>
    <ul>
        <li>Total trades : {trade_count}</li>
        <li>✅ Gagnants : {trade_wins}</li>
        <li>❌ Perdants : {trade_losses}</li>
        <li>📦 Dernier trade : {last_trade_type}</li>
    </ul>
    """

    return status_html + stats_html

# Fonction pour envoyer un message Telegram
def send_telegram_message(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

# Fonction pour récupérer les données OHLCV
def get_ohlcv():
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# Fonction pour calculer les indicateurs
def get_indicators(df):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macdsignal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['ema'] = df['close'].ewm(span=20, adjust=False).mean()
    return df

# Fonction pour enregistrer un trade
def log_trade(action, price, qty, tp, sl):
    global trade_count, trade_wins, trade_losses, last_trade_type

    df = pd.DataFrame([[datetime.now(), action, price, qty, tp, sl]], columns=["datetime", "action", "price", "qty", "take_profit", "stop_loss"])
    if os.path.exists(log_file):
        df.to_csv(log_file, mode='a', header=False, index=False)
    else:
        df.to_csv(log_file, mode='w', header=True, index=False)

    trade_count += 1
    last_trade_type = action

    if "SELL_TP" in action:
        trade_wins += 1
    elif "SELL_SL" in action or "SELL_TRAIL" in action:
        trade_losses += 1

    send_telegram_message(f"📝 Trade enregistré : {action} à {price} USDT, quantité: {qty}")

# Fonction principale du bot
def run():
    global active_position, entry_price, highest_price, last_order_info

    logging.info("🔥 Bot agressif en cours...")

    df = get_ohlcv()
    df = get_indicators(df)
    last_price = df['close'].iloc[-1]
    rsi = df['rsi'].iloc[-1]
    macd = df['macd'].iloc[-1]
    macdsignal = df['macdsignal'].iloc[-1]

    if not active_position:
        buy_signal = macd > macdsignal and rsi < 75

        if buy_signal:
            try:
                balance = exchange.fetch_balance()
                available_usdt = balance['total']['USDT']
                logging.info(f"💵 Solde dispo : {available_usdt:.2f} USDT")

                if available_usdt < 5:
                    logging.warning("❌ Pas assez de solde.")
                    send_telegram_message("⚠️ Solde insuffisant pour trader.")
                    return

                amount_qty = 1  # Forçage 1 ADA
                logging.warning("⚠️ Achat forcé d'1 ADA pour éviter retCode 110007")

                order = exchange.create_market_buy_order(symbol, amount_qty)

                logging.info(f"✅ Achat : {amount_qty} {symbol} à {last_price:.4f}")

                entry_price = last_price
                highest_price = last_price
                active_position = True
                last_order_info = {
                    "amount": amount_qty,
                    "entry_price": entry_price
                }

                tp = round(entry_price * 1.02, 4)
                sl = round(entry_price * 0.985, 4)

                send_telegram_message(f"🚀 Achat : {amount_qty} ADA à {entry_price} | TP : {tp} | SL : {sl}")
                log_trade("BUY", entry_price, amount_qty, tp, sl)

            except Exception as e:
                logging.error(f"❌ Erreur achat : {e}")
                send_telegram_message(f"❌ Erreur achat : {e}")

    else:
        current_price = df['close'].iloc[-1]
        highest_price = max(highest_price, current_price)

        tp = entry_price * 1.02
        sl = entry_price * 0.985
        trailing_trigger = entry_price * 1.015
        trailing_sl = highest_price * 0.993

        logging.info(f"📊 Suivi position : entrée {entry_price:.4f} | actuel {current_price:.4f} | haut {highest_price:.4f}")

        amount_qty = last_order_info.get("amount", 0)

        try:
            if current_price >= tp:
                exchange.create_market_sell_order(symbol, amount_qty)
                send_telegram_message(f"✅ TP atteint à {current_price:.4f} 💰 Position fermée.")
                log_trade("SELL_TP", current_price, amount_qty, "-", "-")
                active_position = False

            elif current_price <= sl:
                exchange.create_market_sell_order(symbol, amount_qty)
                send_telegram_message(f"⛔️ SL touché à {current_price:.4f} ❌ Position coupée.")
                log_trade("SELL_SL", current_price, amount_qty, "-", "-")
                active_position = False

            elif current_price > trailing_trigger and current_price <= trailing_sl:
                exchange.create_market_sell_order(symbol, amount_qty)
                send_telegram_message(f"🔁 Trailing SL déclenché à {current_price:.4f} 🛑 Fermeture de position.")
                log_trade("SELL_TRAIL", current_price, amount_qty, "-", "-")
                active_position = False

        except Exception as e:
            logging.error(f"❌ Erreur vente : {e}")
            send_telegram_message(f"❌ Erreur lors de la vente : {e}")

# Démarre le serveur Flask en tâche de fond
threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 10000}).start()

# Boucle principale
while True:
    try:
        run()
    except Exception as e:
        logging.error(f"💥 Crash: {e}")
        send_telegram_message(f"❌ Crash: {e}")
    time.sleep(30)



