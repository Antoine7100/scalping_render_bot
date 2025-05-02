import ccxt
import os
import pandas as pd
import time
import logging
from datetime import datetime
import requests
import numpy as np
from flask import Flask, request
import threading
from telegram import Update, constants
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, filters
from telegram.inline.inlinekeyboardbutton import InlineKeyboardButton
from telegram.inline.inlinekeyboardmarkup import InlineKeyboardMarkup
import schedule

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Clés API
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# Clés Telegram
TELEGRAM_BOT_TOKEN = "7558300482:AAGu9LaSHOYlfvfxI5uWbC19bgzOXJx6oCQ"
TELEGRAM_CHAT_ID = "1440739670"
TELEGRAM_USER_ID = 1440739670

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
limit = 150
log_file = "trades_log.csv"

active_position = False
entry_price = 0.0
highest_price = 0.0
last_order_info = {}
bot_running = True
bot_lock = threading.Lock()

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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": constants.ParseMode.HTML}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

# Commandes Telegram
def restricted(func):
    def wrapper(update: Update, context: CallbackContext):
        if update.effective_user.id != TELEGRAM_USER_ID:
            context.bot.send_message(chat_id=update.effective_chat.id, text="⛔️ Accès refusé.")
            return
        return func(update, context)
    return wrapper

@restricted
def start_bot(update: Update, context: CallbackContext):
    global bot_running
    bot_running = True
    send_telegram_message("▶️ Bot lancé.")

@restricted
def stop_bot(update: Update, context: CallbackContext):
    global bot_running
    bot_running = False
    send_telegram_message("⏸ Bot arrêté.")

@restricted
def status_bot(update: Update, context: CallbackContext):
    if bot_running:
        send_telegram_message("✅ Bot actif.")
    else:
        send_telegram_message("⛔ Bot en pause.")

@restricted
def force_sell(update: Update, context: CallbackContext):
    global active_position, last_order_info
    if active_position:
        try:
            qty = last_order_info.get("amount", 0)
            price = exchange.fetch_ticker(symbol)['last']
            exchange.create_market_sell_order(symbol, qty)
            send_telegram_message(f"❌ Vente forcée à {price:.4f} pour {qty} ADA")
        except Exception as e:
            send_telegram_message(f"Erreur force_sell : {e}")
    else:
        send_telegram_message("Aucune position à clôturer.")

@restricted
def menu(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer le bot", callback_data='startbot'),
         InlineKeyboardButton("⏸ Stopper le bot", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'),
         InlineKeyboardButton("❌ Fermer position", callback_data='close')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.bot.send_message(chat_id=update.effective_chat.id, text="Menu de contrôle :", reply_markup=reply_markup)

def handle_button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    command = query.data
    if command == 'startbot':
        start_bot(update, context)
    elif command == 'stopbot':
        stop_bot(update, context)
    elif command == 'status':
        status_bot(update, context)
    elif command == 'close':
        force_sell(update, context)

# Lancement du bot Telegram
updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
dp = updater.dispatcher
dp.add_handler(CommandHandler("startbot", start_bot))
dp.add_handler(CommandHandler("stopbot", stop_bot))
dp.add_handler(CommandHandler("status", status_bot))
dp.add_handler(CommandHandler("menu", menu))
dp.add_handler(CommandHandler("close", force_sell))
dp.add_handler(CallbackQueryHandler(handle_button))
updater.start_polling()

# Thread Flask
threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 10000}).start()

# Boucle de scalping
while True:
    if bot_running:
        try:
            df = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(df, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['ema20'] = df['close'].ewm(span=20).mean()
            df['ema50'] = df['close'].ewm(span=50).mean()
            df['macd'] = df['close'].ewm(span=12).mean() - df['close'].ewm(span=26).mean()
            df['macdsignal'] = df['macd'].ewm(span=9).mean()
            df['rsi'] = 100 - (100 / (1 + df['close'].diff().where(lambda x: x > 0, 0).rolling(14).mean() / df['close'].diff().where(lambda x: x < 0, 0).abs().rolling(14).mean()))

            last = df.iloc[-1]
            price = last['close']

            if not active_position:
                if last['ema20'] > last['ema50'] and last['macd'] > last['macdsignal'] and 45 < last['rsi'] < 70:
                    balance = exchange.fetch_balance()
                    usdt = balance['total']['USDT']
                    qty = round(usdt / price, 1)
                    exchange.create_market_buy_order(symbol, qty)
                    entry_price = price
                    highest_price = price
                    active_position = True
                    last_order_info = {"amount": qty, "entry_price": entry_price}
                    tp = round(price * 1.02, 4)
                    sl = round(price * 0.985, 4)
                    send_telegram_message(f"Achat ADA à {entry_price} | TP: {tp} | SL: {sl}")
            else:
                current_price = df['close'].iloc[-1]
                highest_price = max(highest_price, current_price)
                tp = entry_price * 1.02
                sl = entry_price * 0.985
                trailing_trigger = entry_price * 1.015
                trailing_sl = highest_price * 0.993
                qty = last_order_info['amount']

                if current_price >= tp:
                    exchange.create_market_sell_order(symbol, qty)
                    send_telegram_message(f"✅ TP atteint à {current_price:.4f} 💰 Position fermée.")
                    active_position = False
                elif current_price <= sl:
                    exchange.create_market_sell_order(symbol, qty)
                    send_telegram_message(f"⛔️ SL touché à {current_price:.4f} ❌ Position coupée.")
                    active_position = False
                elif current_price > trailing_trigger and current_price <= trailing_sl:
                    exchange.create_market_sell_order(symbol, qty)
                    send_telegram_message(f"🔁 Trailing SL activé à {current_price:.4f} 🛑 Position clôturée.")
                    active_position = False

        except Exception as e:
            logging.error(f"💥 Erreur loop: {e}")
            send_telegram_message(f"Erreur boucle : {e}")
    time.sleep(30)
