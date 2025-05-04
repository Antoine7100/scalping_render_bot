import ccxt
import os
import pandas as pd
import time
import logging
from datetime import datetime
import requests
import numpy as np
from flask import Flask
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncio
import schedule

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = "7962738343:AAEAsom6NSDKo5DyhVkQ1cCCV8ls_iGoUZo"
TELEGRAM_CHAT_ID = "1440739670"
TELEGRAM_USER_ID = 1440739670

exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

symbol = "ADA/USDT:USDT"
leverage = 10
limit = 150
timeframe = '1m'
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

app = Flask(__name__)

@app.route("/")
def index():
    return "<h2>Bot actif - Voir /status et /trades</h2>"

@app.route("/status")
def status():
    if active_position:
        tp = round(entry_price * 1.03, 4)
        sl = round(entry_price * 0.97, 4)
        html = f"<ul><li>✅ Position ouverte</li><li>💰 Entrée : {entry_price}</li><li>📈 Haut : {highest_price}</li><li>TP : {tp} | SL : {sl}</li></ul>"
    else:
        html = "<ul><li>❌ Aucune position ouverte</li></ul>"
    stats = f"<ul><li>Total trades : {trade_count}</li><li>✅ Gagnants : {trade_wins}</li><li>❌ Perdants : {trade_losses}</li><li>Dernier trade : {last_trade_type}</li></ul>"
    return f"<h2>Status Bot</h2>{html}<h3>Performance</h3>{stats}"

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

async def send_telegram_message(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": constants.ParseMode.HTML}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != TELEGRAM_USER_ID:
            await update.message.reply_text("⛔️ Accès refusé.")
            return
        return await func(update, context)
    return wrapper

@restricted
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = True
    await send_telegram_message("▶️ Bot lancé.")

@restricted
async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = False
    await send_telegram_message("⏸ Bot arrêté.")

@restricted
async def force_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_position, last_order_info
    if active_position:
        try:
            qty = last_order_info.get("amount", 0)
            price = exchange.fetch_ticker(symbol)['last']
            exchange.create_market_sell_order(symbol, qty)
            await send_telegram_message(f"❌ Vente forcée à {price:.4f} pour {qty} ADA")
        except Exception as e:
            await send_telegram_message(f"Erreur force_sell : {e}")
    else:
        await send_telegram_message("Aucune position à clôturer.")

@restricted
async def open_trade_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if active_position:
        current_price = exchange.fetch_ticker(symbol)['last']
        tp = round(entry_price * 1.03, 4)
        sl = round(entry_price * 0.97, 4)
        tendance = "📈 Vers TP" if current_price > entry_price else "📉 Vers SL"
        msg = f"🟠 Position ouverte
Entrée : {entry_price:.4f}
TP : {tp} | SL : {sl}
Prix actuel : {current_price:.4f} {tendance}"
    else:
        msg = "❌ Aucune position ouverte."
    await send_telegram_message(msg)

@restricted
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer le bot", callback_data='startbot'),
         InlineKeyboardButton("⏸ Stopper le bot", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'),
         InlineKeyboardButton("🔍 Trade en cours", callback_data='open_trade')],
        [InlineKeyboardButton("❌ Fermer position", callback_data='close')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Menu de contrôle :", reply_markup=reply_markup)

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "startbot":
        await start_bot(update, context)
    elif data == "stopbot":
        await stop_bot(update, context)
    elif data == "status":
        await status_bot(update, context)
    elif data == "close":
        await force_sell(update, context)
    elif data == "open_trade":
        await open_trade_status(update, context)

@restricted
async def status_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "✅ Bot actif." if bot_running else "⛔ Bot en pause."
    await send_telegram_message(msg)

@restricted
async def daily_report():
    if not os.path.exists(log_file):
        await send_telegram_message("Aucun trade aujourd'hui.")
        return
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    today_trades = df[df['datetime'].dt.date == today]
    if today_trades.empty:
        await send_telegram_message("📉 Aucun trade exécuté aujourd'hui.")
    else:
        win_count = (today_trades['action'] == 'TP').sum()
        loss_count = (today_trades['action'] == 'SL').sum()
        msg = f"📊 Bilan du {today} :
✅ Gagnants : {win_count}
❌ Perdants : {loss_count}
📦 Total : {len(today_trades)}"
        await send_telegram_message(msg)

schedule.every().day.at("23:55").do(lambda: asyncio.run(daily_report()))

# === STRATÉGIE DE TRADING ===
def trading_loop():
    global active_position, entry_price, highest_price, last_order_info, trade_count, trade_wins, trade_losses, last_trade_type
    if not bot_running:
        return

    try:
        df = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(df, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        df['ema20'] = df['close'].ewm(span=20).mean()
        df['ema50'] = df['close'].ewm(span=50).mean()
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['upper_bb'] = df['sma20'] + 2 * df['close'].rolling(20).std()
        df['lower_bb'] = df['sma20'] - 2 * df['close'].rolling(20).std()

        high_price = df['high'].max()
        low_price = df['low'].min()
        df['fibo_618'] = low_price + 0.618 * (high_price - low_price)

        last = df.iloc[-1]
        price = last['close']

        if not active_position:
            if last['ema20'] > last['ema50'] and price > last['fibo_618'] and price > last['lower_bb']:
                balance = exchange.fetch_balance()
                usdt = balance['USDT']['free']
                qty = round(usdt / price, 1)
                exchange.create_market_buy_order(symbol, qty)
                entry_price = price
                highest_price = price
                active_position = True
                last_order_info = {"amount": qty, "entry_price": entry_price}
                tp = round(price * 1.03, 4)
                sl = round(price * 0.97, 4)
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},buy,{price},{qty},{tp},{sl}
")
                asyncio.run(send_telegram_message(f"🟢 Achat ADA à {entry_price:.4f} | TP: {tp} | SL: {sl}"))
        else:
            current_price = price
            highest_price = max(highest_price, current_price)
            tp = entry_price * 1.03
            sl = entry_price * 0.97
            trailing_trigger = entry_price * 1.02
            trailing_sl = highest_price * 0.993
            qty = last_order_info['amount']
            if current_price >= tp:
                exchange.create_market_sell_order(symbol, qty)
                trade_wins += 1
                last_trade_type = "TP"
                asyncio.run(send_telegram_message(f"✅ TP atteint à {current_price:.4f} 💰 Position fermée."))
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},TP,{current_price},{qty},{tp},{sl}
")
                active_position = False
                trade_count += 1
            elif current_price <= sl:
                exchange.create_market_sell_order(symbol, qty)
                trade_losses += 1
                last_trade_type = "SL"
                asyncio.run(send_telegram_message(f"⛔️ SL touché à {current_price:.4f} ❌ Position coupée."))
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},SL,{current_price},{qty},{tp},{sl}
")
                active_position = False
                trade_count += 1
            elif current_price > trailing_trigger and current_price <= trailing_sl:
                exchange.create_market_sell_order(symbol, qty)
                last_trade_type = "Trailing"
                asyncio.run(send_telegram_message(f"🔁 Trailing SL activé à {current_price:.4f} 🛑 Position clôturée."))
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},Trailing,{current_price},{qty},{tp},{sl}
")
                active_position = False
                trade_count += 1

    except Exception as e:
        logging.error(f"Erreur trading_loop : {e}")
        asyncio.run(send_telegram_message(f"Erreur stratégie : {e}"))

schedule.every(20).seconds.do(trading_loop)

threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()
threading.Thread(target=lambda: asyncio.run(launch_telegram())).start()

while True:
    schedule.run_pending()
    time.sleep(1)
