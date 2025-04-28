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
    'options': {
        'defaultType': 'future'
    }
})

symbol = "ADA/USDT:USDT"  # Nouvelle paire ADA/USDT
leverage = 3  # Levier augmenté à 3

try:
    exchange.set_leverage(leverage, symbol)
except Exception as e:
    logging.warning(f"⚠️ Impossible de définir le levier : {e}")

timeframe = '1m'
limit = 100
risk_percent = 0.02
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

# Web interface
app = Flask(__name__)

@app.route("/")
def index():
    return "<h2>Bienvenue sur le bot de trading Bybit. Accédez à <a href='/trades'>/trades</a> pour voir les trades du jour.</h2>"

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
        return "<h2>Aucun trade enregistré.</h2>"

    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    today_trades = df[df['datetime'].dt.date == today]

    if today_trades.empty:
        return "<h2>Aucun trade exécuté aujourd'hui.</h2>"

    pnl = daily_pnl()
    html = f"<h1>📄 Trades du jour</h1><h3>📈 PNL du jour : {pnl} USDT</h3><table border='1' cellpadding='5'>"
    html += "<tr><th>Heure</th><th>Action</th><th>Prix</th><th>Quantité</th><th>TP</th><th>SL</th></tr>"

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
    buys = today_trades[today_trades['action'].str.startswith("BUY")]['price'] * today_trades[today_trades['action'].str.startswith("BUY")]['qty']
    sells = today_trades[today_trades['action'].str.startswith("SELL")]['price'] * today_trades[today_trades['action'].str.startswith("SELL")]['qty']
    return round(sells.sum() - buys.sum(), 2)

# Fonction pour envoyer un message Telegram
def send_telegram_message(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

# Fonction pour calculer les niveaux de Fibonacci
def fibonacci_levels(high, low):
    diff = high - low
    return {
        "0.236": high - diff * 0.236,
        "0.382": high - diff * 0.382,
        "0.5": high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "0.786": high - diff * 0.786
    }

# Fonction pour récupérer les données de marché
def get_ohlcv():
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# RSI, MACD, EMA, Bollinger
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

    df['bb_upper'] = df['close'].rolling(window=20).mean() + 2 * df['close'].rolling(window=20).std()
    df['bb_lower'] = df['close'].rolling(window=20).mean() - 2 * df['close'].rolling(window=20).std()

    return df

# Enregistrer un trade dans le log CSV
def log_trade(action, price, qty, tp, sl):
    df = pd.DataFrame([[datetime.now(), action, price, qty, tp, sl]], columns=["datetime", "action", "price", "qty", "take_profit", "stop_loss"])
    if os.path.exists(log_file):
        df.to_csv(log_file, mode='a', header=False, index=False)
    else:
        df.to_csv(log_file, mode='w', header=True, index=False)
    send_telegram_message(f"📝 Trade enregistré : {action} à {price} USDT, quantité: {qty}")

# Graphique avec Fibonacci + annotations
def show_chart(df, fibs, price):
    plt.figure(figsize=(12, 6))
    plt.plot(df['timestamp'], df['close'], label='Prix de clôture')
    plt.plot(df['timestamp'], df['ema'], label='EMA 20', linestyle='--')
    for level, p in fibs.items():
        plt.axhline(y=p, color='gray', linestyle='--', label=f'Fibo {level}')
    plt.title(f"ADA/USDT avec indicateurs (Prix actuel: {price:.4f})")
    plt.xlabel("Temps")
    plt.ylabel("Prix (USDT)")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig("fibonacci_chart.png")
    plt.close()
    logging.info("📊 Graphique sauvegardé : fibonacci_chart.png")



# Fonction principale

def run():
    global active_position, entry_price, highest_price, last_order_info
    df = get_ohlcv()
    df = get_indicators(df)

    high = df['high'].max()
    low = df['low'].min()
    last_price = df['close'].iloc[-1]
    rsi = df['rsi'].iloc[-1]
    macd = df['macd'].iloc[-1]
    macdsignal = df['macdsignal'].iloc[-1]
    ema = df['ema'].iloc[-1]
    bb_lower = df['bb_lower'].iloc[-1]

    fibs = fibonacci_levels(high, low)

    logging.info(f"Prix actuel: {last_price:.2f} USDT")
    logging.info(f"RSI: {rsi:.2f} | MACD: {macd:.2f} | Signal: {macdsignal:.2f} | EMA: {ema:.2f} | BB-Lower: {bb_lower:.2f}")
    logging.info(f"Niveaux Fibonacci: {fibs}")

    show_chart(df, fibs, last_price)

    try:
        balance = exchange.fetch_balance()
        available_usdt = balance['total']['USDT']
        logging.info(f"Solde disponible: {available_usdt:.2f} USDT")
    except Exception as e:
        logging.error(f"Erreur récupération solde: {e}")
        return

    if available_usdt < 1:
        logging.warning("Solde insuffisant pour trader.")
        return

    position_size = available_usdt * leverage
    max(round(position_size / last_price, 2), 1.0)

    if not active_position:
    buy_signal = (
        rsi < 65 and macd > macdsignal
    )
    if buy_signal:
        try:
            balance = exchange.fetch_balance()
            available_usdt = balance['total']['USDT']
            position_size = available_usdt * leverage
            amount_qty = max(round(position_size / last_price, 2), 1.0)  # Calcul sécurisé ici ✅

            order = exchange.create_market_buy_order(symbol, amount_qty)
            logging.info(f"💵 Achat exécuté: {order['amount']} {symbol} à {last_price:.4f}")
            entry_price = last_price
            highest_price = last_price
            active_position = True
            last_order_info = order

            tp = round(entry_price * (1 + profit_target), 4)
            sl = round(entry_price * (1 - stop_loss_percent), 4)

            send_telegram_message(f"✅ Achat: {amount_qty} {symbol} à {entry_price} USDT\n🎯 TP: {tp} | 🛑 SL: {sl}")
            log_trade("BUY", entry_price, amount_qty, tp, sl)

        except Exception as e:
            logging.error(f"Erreur achat: {e}")
            send_telegram_message(f"❌ Erreur achat : {e}")
    else:
        if last_price > highest_price:
            highest_price = last_price
            logging.info(f"📈 Nouveau plus haut atteint: {highest_price:.2f}")

        trigger_price = entry_price * (1 + trailing_stop_trigger)
        if last_price >= trigger_price:
            trailing_sl = highest_price * (1 - trailing_stop_distance)
            if last_price <= trailing_sl:
                active_position = False
                logging.info(f"🔽 Trailing Stop activé à {last_price:.2f} (seuil: {trailing_sl:.2f})")
                send_telegram_message(f"🔽 Vente Trailing Stop à {last_price:.2f} USDT")
                log_trade("SELL-TRAIL", last_price, amount_qty, highest_price, trailing_sl)

        stop_loss_price = round(entry_price * (1 - stop_loss_percent), 2)
        if last_price <= stop_loss_price:
            active_position = False
            logging.info(f"❌ Stop Loss déclenché à {last_price:.2f}")
            send_telegram_message(f"🚨 SL déclenché à {last_price:.2f} USDT.")
            log_trade("SELL-SL", last_price, amount_qty, highest_price, stop_loss_price)

        take_profit_price = round(entry_price * (1 + profit_target), 2)
        if last_price >= take_profit_price:
            active_position = False
            logging.info(f"🏆 Take Profit atteint à {last_price:.2f}")
            send_telegram_message(f"💰 TP atteint à {last_price:.2f} USDT !")
            log_trade("SELL-TP", last_price, amount_qty, take_profit_price, stop_loss_price)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": int(os.environ.get("PORT", 5000))})
    flask_thread.start()

    while True:
        try:
            run()
        except Exception as e:
            logging.error(f"💥 Erreur fatale dans la boucle : {e}")
            send_telegram_message(f"❌ Le bot a planté : {e}")
        time.sleep(30)

