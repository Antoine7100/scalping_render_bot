# === BOT TRADING PRO BYBIT x TELEGRAM x FLASK ===
# Fonctionnalités : Trading automatique + Telegram + Flask + Résumé + Contrôle complet

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

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# === CONFIG ===
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = "7962738343:AAEAsom6NSDKo5DyhVkQ1cCCV8ls_iGoUZo"
TELEGRAM_CHAT_ID = "1440739670"
TELEGRAM_USER_ID = 1440739670
symbol = "ADA/USDT:USDT"
leverage = 10

# === BYBIT ===
exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

try:
    exchange.set_leverage(leverage, symbol)
except Exception as e:
    logging.warning(f"⚠️ Levier non appliqué : {e}")

# === VARIABLES ===
timeframe = '1m'
limit = 100
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

# === FLASK ===
app = Flask(__name__)

@app.route("/")
def index():
    return "<h2>Bot actif - Voir /status et /trades</h2>"

@app.route("/status")
def status():
    try:
        current_price = exchange.fetch_ticker(symbol)['last']
    except:
        current_price = 0

    if active_position:
        tp = round(entry_price * 1.03, 4)
        sl = round(entry_price * 0.97, 4)
        direction = "TP ✅" if abs(current_price - tp) < abs(current_price - sl) else "SL ❌"
        html = f"<ul><li>✅ Position ouverte</li><li>💰 Entrée : {entry_price:.4f}</li><li>📉 Prix actuel : {current_price:.4f}</li><li>🎯 TP : {tp:.4f} | ⛔ SL : {sl:.4f}</li><li>📍 Tendance : vers {direction}</li></ul>"
    else:
        html = "<ul><li>❌ Aucune position ouverte</li></ul>"

    stats = f"<ul><li>Total trades : {trade_count}</li><li>✅ Gagnants : {trade_wins}</li><li>❌ Perdants : {trade_losses}</li><li>Dernier trade : {last_trade_type}</li></ul>"
    return f"<h2>Statut du Bot</h2>{html}<h3>Performance</h3>{stats}"

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

# === TELEGRAM ===
async def send_telegram_message(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": constants.ParseMode.HTML}
    try:
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
async def status_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        current_price = exchange.fetch_ticker(symbol)['last']
    except:
        current_price = 0

    if active_position:
        tp = round(entry_price * 1.03, 4)
        sl = round(entry_price * 0.97, 4)
        direction = "TP ✅" if abs(current_price - tp) < abs(current_price - sl) else "SL ❌"
        msg = f"📊 <b>Statut de la position :</b>\n✅ <b>Position ouverte sur {symbol}</b>\n💰 Entrée : {entry_price:.4f} USDT\n📉 Prix actuel : {current_price:.4f} USDT\n🎯 TP : {tp:.4f} | ⛔ SL : {sl:.4f}\n📍 Tendance : vers {direction}"
    else:
        msg = "📊 Aucune position ouverte actuellement."
    await send_telegram_message(msg)

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
async def trades_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(log_file):
        await update.message.reply_text("Aucun trade enregistré.")
        return
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    df_today = df[df['datetime'].dt.date == today]
    if df_today.empty:
        await update.message.reply_text("Aucun trade exécuté aujourd'hui.")
        return
    msg = "📋 <b>Trades du jour :</b>\n"
    for _, row in df_today.iterrows():
        msg += f"\n📅 {row['datetime']}\nAction : {row['action']} | Prix : {row['price']} | Qté : {row['qty']}\nTP : {row['take_profit']} | SL : {row['stop_loss']}\n"
    await send_telegram_message(msg)

@restricted
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer", callback_data='startbot'), InlineKeyboardButton("⏸ Stopper", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'), InlineKeyboardButton("❌ Fermer position", callback_data='close')],
        [InlineKeyboardButton("📋 Voir les trades", callback_data='trades')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Menu de contrôle :", reply_markup=reply_markup)

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == 'startbot': await start_bot(update, context)
    elif command == 'stopbot': await stop_bot(update, context)
    elif command == 'status': await status_bot(update, context)
    elif command == 'close': await force_sell(update, context)
    elif command == 'trades': await trades_telegram(update, context)

async def start_telegram():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    await app.bot.delete_webhook(drop_pending_updates=True)
    app.add_handler(CommandHandler("startbot", start_bot))
    app.add_handler(CommandHandler("stopbot", stop_bot))
    app.add_handler(CommandHandler("status", status_bot))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("close", force_sell))
    app.add_handler(CommandHandler("trades", trades_telegram))
    app.add_handler(CallbackQueryHandler(handle_button))
    await app.run_polling()

# === THREADS ===
def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

def trading_loop():
    try:
        df = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(df, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['ema8'] = df['close'].ewm(span=8).mean()
        df['ema21'] = df['close'].ewm(span=21).mean()
        df['macd'] = df['close'].ewm(span=12).mean() - df['close'].ewm(span=26).mean()
        df['macdsignal'] = df['macd'].ewm(span=9).mean()
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss))
        last = df.iloc[-1]
        price = last['close']

        global active_position, entry_price, highest_price, last_order_info, trade_count, trade_wins, trade_losses, last_trade_type

        if not active_position:
            if last['ema8'] > last['ema21'] and last['macd'] > last['macdsignal'] and 35 < last['rsi'] < 75:
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
                    f.write(f"{datetime.now()},buy,{price},{qty},{tp},{sl}\n")
                asyncio.run(send_telegram_message(f"🟢 Achat ADA à {entry_price:.4f} | TP: {tp} | SL: {sl}"))
        else:
            current_price = price
            highest_price = max(highest_price, current_price)
            tp = entry_price * 1.03
            sl = entry_price * 0.97
            trailing_trigger = entry_price * 1.02
            trailing_sl = highest_price * 0.99
            qty = last_order_info['amount']
            if current_price >= tp:
                exchange.create_market_sell_order(symbol, qty)
                trade_wins += 1
                last_trade_type = "TP"
                asyncio.run(send_telegram_message(f"✅ TP atteint à {current_price:.4f} 💰 Position fermée."))
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},TP,{current_price},{qty},{tp},{sl}\n")
                active_position = False
                trade_count += 1
            elif current_price <= sl:
                exchange.create_market_sell_order(symbol, qty)
                trade_losses += 1
                last_trade_type = "SL"
                asyncio.run(send_telegram_message(f"⛔️ SL touché à {current_price:.4f} ❌ Position coupée."))
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},SL,{current_price},{qty},{tp},{sl}\n")
                active_position = False
                trade_count += 1
            elif current_price > trailing_trigger and current_price <= trailing_sl:
                exchange.create_market_sell_order(symbol, qty)
                last_trade_type = "Trailing"
                asyncio.run(send_telegram_message(f"🔁 Trailing SL activé à {current_price:.4f} 🛑 Position clôturée."))
                with open(log_file, 'a') as f:
                    f.write(f"{datetime.now()},Trailing,{current_price},{qty},{tp},{sl}\n")
                active_position = False
                trade_count += 1
    except Exception as e:
        logging.error(f"💥 Erreur loop: {e}")
        asyncio.run(send_telegram_message(f"Erreur boucle : {e}"))

def daily_summary():
    if not os.path.exists(log_file):
        return
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.now().date()
    df_today = df[df['datetime'].dt.date == today]
    if df_today.empty:
        return
    win = len(df_today[df_today['action'] == 'TP'])
    loss = len(df_today[df_today['action'] == 'SL'])
    total = len(df_today)
    summary = f"📊 Résumé du {today} :\nTotal : {total} trades\n✅ Gagnants : {win}\n❌ Perdants : {loss}"
    asyncio.run(send_telegram_message(summary))

# === SCHEDULING ===
schedule.every(20).seconds.do(lambda: bot_running and trading_loop())
schedule.every().day.at("22:00").do(daily_summary)

if __name__ == "__main__":
    threading.Thread(target=run_schedule).start()
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()
    threading.Thread(target=start_telegram).start()
