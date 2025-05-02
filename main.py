import ccxt
import os
import pandas as pd
import time
import logging
import matplotlib.pyplot as plt
from datetime import datetime
import requests
import numpy as np
from flask import Flask, request
import threading
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

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

symbols = ["ADA/USDT:USDT", "BTC/USDT:USDT", "ETH/USDT:USDT"]
leverage = 3

for sym in symbols:
    try:
        exchange.set_leverage(leverage, sym)
    except Exception as e:
        logging.warning(f"⚠️ Levier non modifié pour {sym} : {e}")

timeframe = '1m'
limit = 150
log_file = "trades_log.csv"

active_position = False
entry_price = 0.0
highest_price = 0.0
last_order_info = {}
bot_running = True

trade_count = 0
trade_wins = 0
trade_losses = 0
last_trade_type = ""
aggressive_mode = False

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

def send_telegram_message(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

def get_indicators(df):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ma10'] = df['close'].rolling(window=10).mean()
    df['ma100'] = df['close'].rolling(window=100).mean()
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macdsignal'] = df['macd'].ewm(span=9, adjust=False).mean()
    return df

def run():
    global active_position, entry_price, highest_price, last_order_info

    for symbol in symbols:
        df = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(df, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = get_indicators(df)

        last_price = df['close'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        macd = df['macd'].iloc[-1]
        macdsignal = df['macdsignal'].iloc[-1]
        ema20 = df['ema20'].iloc[-1]
        ema50 = df['ema50'].iloc[-1]
        ma10 = df['ma10'].iloc[-1]
        ma100 = df['ma100'].iloc[-1]
        volume = df['volume'].iloc[-1]
        volume_avg = df['volume'].rolling(window=20).mean().iloc[-1]
        volume_ok = volume > volume_avg

        rsi_min = 35 if aggressive_mode else 40
        rsi_max = 75 if aggressive_mode else 70

        trend_up = ema20 > ema50 and ma10 > ma100
        signal_ok = macd > macdsignal and rsi_min < rsi < rsi_max and volume_ok

        if not active_position and trend_up and signal_ok:
            try:
                balance = exchange.fetch_balance()
                available_usdt = balance['total']['USDT']

                if available_usdt < 5:
                    send_telegram_message("⚠️ Solde insuffisant pour trade.")
                    return

                amount_qty = 1
                order = exchange.create_market_buy_order(symbol, amount_qty)

                entry_price = last_price
                highest_price = last_price
                active_position = True
                last_order_info = {"amount": amount_qty, "entry_price": entry_price}

                tp = round(entry_price * 1.02, 4)
                sl = round(entry_price * 0.985, 4)

                send_telegram_message(
                    f"🟢 Achat {symbol} à {entry_price:.4f} | TP: {tp} | SL: {sl}
"
                    f"MACD: {macd:.4f} > Signal: {macdsignal:.4f}, RSI: {rsi:.2f}, EMA20 > EMA50, MA10 > MA100, Volume OK
"
                    f"Mode agressif : {'OUI' if aggressive_mode else 'NON'}"
                )
            except Exception as e:
                send_telegram_message(f"❌ Erreur achat : {e}")

        elif active_position:
            current_price = df['close'].iloc[-1]
            highest_price = max(highest_price, current_price)

            tp = entry_price * 1.02
            sl = entry_price * 0.985
            trailing_trigger = entry_price * 1.015
            trailing_sl = highest_price * 0.993

            amount_qty = last_order_info.get("amount", 0)

            try:
                if current_price >= tp:
                    exchange.create_market_sell_order(symbol, amount_qty)
                    send_telegram_message(f"🎯 TP atteint à {current_price:.4f}")
                    active_position = False

                elif current_price <= sl:
                    exchange.create_market_sell_order(symbol, amount_qty)
                    send_telegram_message(f"⛔️ SL touché à {current_price:.4f}")
                    active_position = False

                elif current_price > trailing_trigger and current_price <= trailing_sl:
                    exchange.create_market_sell_order(symbol, amount_qty)
                    send_telegram_message(f"🔁 Trailing SL activé à {current_price:.4f}")
                    active_position = False
            except Exception as e:
                send_telegram_message(f"❌ Erreur vente : {e}")

def start_bot(update=None, context=None):
    global bot_running
    bot_running = True
    if update:
        context.bot.send_message(chat_id=update.effective_chat.id, text="▶️ Bot activé !")

def stop_bot(update=None, context=None):
    global bot_running
    bot_running = False
    if update:
        context.bot.send_message(chat_id=update.effective_chat.id, text="⏸ Bot désactivé.")

def status_bot(update, context):
    message = "✅ Bot en cours d'exécution." if bot_running else "⛔ Bot désactivé."
    context.bot.send_message(chat_id=update.effective_chat.id, text=message)

def force_sell(update, context):
    global active_position, last_order_info
    if active_position:
        qty = last_order_info.get("amount", 0)
        price = exchange.fetch_ticker(symbols[0])['last']
        try:
            exchange.create_market_sell_order(symbols[0], qty)
            send_telegram_message(f"🛑 Vente forcée exécutée à {price:.4f} pour {qty} unités.")
            active_position = False
        except Exception as e:
            send_telegram_message(f"❌ Erreur lors de la vente forcée : {e}")
    else:
        send_telegram_message("ℹ️ Aucune position à fermer.")

def mode_agressif(update, context):
    global aggressive_mode
    aggressive_mode = not aggressive_mode
    status = "activé" if aggressive_mode else "désactivé"
    context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚡ Mode agressif {status.upper()} !
Le RSI est maintenant entre 35 et 75.")

def menu(update, context):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer le bot", callback_data='startbot'),
         InlineKeyboardButton("⏸ Stopper le bot", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'),
         InlineKeyboardButton("❌ Fermer la position", callback_data='close')],
        [InlineKeyboardButton("⚡ Mode agressif", callback_data='agressif')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.bot.send_message(chat_id=update.effective_chat.id, text="📋 Menu de contrôle :", reply_markup=reply_markup)

def handle_button(update, context):
    query = update.callback_query
    query.answer()
    command = query.data
    fake_update = update
    fake_update.effective_user = update.effective_user
    fake_update.effective_chat = update.effective_chat

    if command == 'startbot':
        start_bot(fake_update, context)
    elif command == 'stopbot':
        stop_bot(fake_update, context)
    elif command == 'status':
        status_bot(fake_update, context)
    elif command == 'close':
        force_sell(fake_update, context)
    elif command == 'agressif':
        mode_agressif(fake_update, context)

def start_telegram_bot():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("startbot", start_bot))
    dp.add_handler(CommandHandler("stopbot", stop_bot))
    dp.add_handler(CommandHandler("status", status_bot))
    dp.add_handler(CommandHandler("menu", menu))
    dp.add_handler(CommandHandler("agressif", mode_agressif))
    dp.add_handler(CommandHandler("close", force_sell))
    dp.add_handler(CallbackQueryHandler(handle_button))
    updater.start_polling()

threading.Thread(target=start_telegram_bot).start()

while True:
    if bot_running:
        try:
            run()
        except Exception as e:
            logging.error(f"💥 Crash: {e}")
            send_telegram_message(f"❌ Crash: {e}")
    time.sleep(30)

