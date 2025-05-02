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
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler
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
def notify_last_trade():
    if not os.path.exists(log_file):
        return
    df = pd.read_csv(log_file)
    last = df.tail(1).iloc[0]
     msg = f"📈 Trade clôturé:\n{last['datetime']} | {last['action']} à {last['price']} USDT"
{last['datetime']} | {last['action']} à {last['price']} USDT"
    send_telegram_message(msg)
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

def send_telegram_message(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

def start_telegram_bot_once():
    if bot_lock.locked():
        logging.warning("Le bot Telegram est déjà en cours d'exécution.")
        return

    with bot_lock:
        updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("close", force_sell))
        dp.add_handler(CommandHandler("startbot", start_bot))
        dp.add_handler(CommandHandler("stopbot", stop_bot))
        dp.add_handler(CommandHandler("status", status_bot))
        dp.add_handler(CommandHandler("menu", menu))
        dp.add_handler(CallbackQueryHandler(handle_button))
        dp.add_handler(CommandHandler("lasttrades", last_trades))
        updater.start_polling()

threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 10000}).start()
threading.Thread(target=start_telegram_bot_once).start()

def restricted(func):
    def wrapper(update, context):
        if update.effective_user.id != TELEGRAM_USER_ID:
            context.bot.send_message(chat_id=update.effective_chat.id, text="⛔️ Accès refusé.")
            return
        return func(update, context)
    return wrapper

@restricted
def force_sell(update, context):
    global active_position, last_order_info
    if active_position:
        try:
            qty = last_order_info.get("amount", 0)
            price = exchange.fetch_ticker(symbol)['last']
            exchange.create_market_sell_order(symbol, qty)
            send_telegram_message(f"🛑 Vente forcée exécutée à {price:.4f} pour {qty} ADA")
            active_position = False
        except Exception as e:
            send_telegram_message(f"❌ Erreur lors de la vente forcée : {e}")
    else:
        send_telegram_message("ℹ️ Aucune position ouverte à fermer.")

@restricted
def start_bot(update, context):
    global bot_running
    bot_running = True
    send_telegram_message("▶️ Bot redémarré et actif.")

@restricted
def stop_bot(update, context):
    global bot_running
    bot_running = False
    send_telegram_message("⏸ Bot mis en pause.")

@restricted
def status_bot(update, context):
    if bot_running:
        send_telegram_message("✅ Bot actif.")
    else:
        send_telegram_message("⛔ Bot en pause.")

@restricted
def menu(update, context):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer le bot", callback_data='startbot'),
         InlineKeyboardButton("⏸ Stopper le bot", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'),
         InlineKeyboardButton("❌ Fermer la position", callback_data='close')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.bot.send_message(chat_id=update.effective_chat.id, text="📋 Menu de contrôle :", reply_markup=reply_markup)

def handle_button(update, context):
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
@restricted
def last_trades(update, context):
    if not os.path.exists(log_file):
        context.bot.send_message(chat_id=update.effective_chat.id, text="Aucun trade enregistré.")
        return

    df = pd.read_csv(log_file)
    df = df.tail(5)
    message = "🧾 Derniers trades:
"
    for _, row in df.iterrows():
        message += f"{row['datetime']} | {row['action']} à {row['price']} USDT
"

    context.bot.send_message(chat_id=update.effective_chat.id, text=message)

def get_indicators(df):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macdsignal'] = df['macd'].ewm(span=9, adjust=False).mean()
    return df

def run():
    global active_position, entry_price, highest_price, last_order_info, trade_count, trade_wins, trade_losses, last_trade_type

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

    if not active_position:
        buy_signal = macd > macdsignal and ema20 > ema50 and 40 < rsi < 70

        if buy_signal:
            try:
                balance = exchange.fetch_balance()
                available_usdt = balance['total']['USDT']

                if available_usdt < 5:
                    logging.warning("Solde insuffisant pour trade.")
                    return

                amount_qty = round(available_usdt / last_price, 2)
                order = exchange.create_market_buy_order(symbol, amount_qty)

                entry_price = last_price
                highest_price = last_price
                active_position = True
                last_order_info = order
                tp = round(entry_price * 1.02, 4)
                sl = round(entry_price * 0.985, 4)

                send_telegram_message(f"💰 Achat: {amount_qty} à {entry_price} USDT | TP: {tp} | SL: {sl}")
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},BUY,{entry_price},{amount_qty},{tp},{sl}
")
            except Exception as e:
                logging.error(f"Erreur achat: {e}")
                send_telegram_message(f"❌ Erreur: {e}")

    else:
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
                send_telegram_message(f"✅ TP atteint à {current_price:.4f} 💰 Position fermée.")
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},SELL_TP,{current_price},{amount_qty},-,-
")
                active_position = False
                trade_count += 1
                trade_wins += 1
                last_trade_type = "SELL_TP"
                notify_last_trade()

            elif current_price <= sl:
                exchange.create_market_sell_order(symbol, amount_qty)
                send_telegram_message(f"⛔️ SL touché à {current_price:.4f} ❌ Position coupée.")
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},SELL_SL,{current_price},{amount_qty},-,-
")
                active_position = False
                trade_count += 1
                trade_losses += 1
                last_trade_type = "SELL_SL"
                notify_last_trade()

            elif current_price > trailing_trigger and current_price <= trailing_sl:
                exchange.create_market_sell_order(symbol, amount_qty)
                send_telegram_message(f"🔁 Trailing SL déclenché à {current_price:.4f} 🛑 Fermeture de position.")
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},SELL_TRAIL,{current_price},{amount_qty},-,-
")
                active_position = False
                trade_count += 1
                trade_losses += 1
                last_trade_type = "SELL_TRAIL"
                notify_last_trade()

        except Exception as e:
            logging.error(f"❌ Erreur de vente : {e}")
            send_telegram_message(f"❌ Erreur lors de la vente : {e}")

while True:
    if bot_running:
        try:
            run()
        except Exception as e:
            logging.error(f"💥 Crash: {e}")
            send_telegram_message(f"❌ Crash: {e}")
    time.sleep(30)
