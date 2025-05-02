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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncio
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
is_processing = False

app = Flask(__name__)

@app.route("/status")
def status():
    return "<h3>✅ Bot opérationnel</h3>", 200

async def send_telegram_message(app, msg):
    try:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=constants.ParseMode.HTML)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

async def send_daily_summary(app):
    if not os.path.exists(log_file):
        return
    df = pd.read_csv(log_file)
    df['datetime'] = pd.to_datetime(df['datetime'])
    today = datetime.utcnow().date()
    today_trades = df[df['datetime'].dt.date == today]
    total = len(today_trades)
    wins = len(today_trades[today_trades['action'].str.contains('TP')])
    losses = len(today_trades[today_trades['action'].str.contains('SL')])
    trailing = len(today_trades[today_trades['action'].str.contains('Trailing')])
    if total == 0:
        summary = "📊 Résumé du jour : Aucun trade exécuté."
    else:
        summary = (
            f"📊 Résumé du jour :\n"
            f"• Total : {total}\n"
            f"• ✅ Gagnants : {wins}\n"
            f"• ❌ Perdants : {losses}\n"
            f"• 🔁 Trailing SL : {trailing}"
        )
    await send_telegram_message(app, summary)

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
    await send_telegram_message(context.application, "▶️ Bot lancé.")

@restricted
async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = False
    await send_telegram_message(context.application, "⏸ Bot arrêté.")

@restricted
async def status_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if bot_running:
        await send_telegram_message(context.application, "✅ Bot actif.")
    else:
        await send_telegram_message(context.application, "⛔ Bot en pause.")

@restricted
async def force_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_position, last_order_info
    if active_position:
        try:
            qty = last_order_info.get("amount", 0)
            price = exchange.fetch_ticker(symbol)['last']
            exchange.create_market_sell_order(symbol, qty)
            await send_telegram_message(context.application, f"❌ Vente forcée à {price:.4f} pour {qty} ADA")
        except Exception as e:
            await send_telegram_message(context.application, f"Erreur force_sell : {e}")
    else:
        await send_telegram_message(context.application, "Aucune position à clôturer.")

@restricted
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer le bot", callback_data='startbot'),
         InlineKeyboardButton("⏸ Stopper le bot", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'),
         InlineKeyboardButton("❌ Fermer position", callback_data='close')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("Menu de contrôle :", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text("Menu de contrôle :", reply_markup=reply_markup)

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    command = query.data
    if command == 'startbot':
        await start_bot(update, context)
    elif command == 'stopbot':
        await stop_bot(update, context)
    elif command == 'status':
        await status_bot(update, context)
    elif command == 'close':
        await force_sell(update, context)

async def launch_telegram_bot():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)
    app.add_handler(CommandHandler("startbot", start_bot))
    app.add_handler(CommandHandler("stopbot", stop_bot))
    app.add_handler(CommandHandler("status", status_bot))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("close", force_sell))
    app.add_handler(CommandHandler("start", menu))
    app.add_handler(CallbackQueryHandler(handle_button))

    async def trading_loop():
        global is_processing, bot_running, active_position, entry_price, highest_price, last_order_info
        while True:
            if bot_running and not is_processing:
                is_processing = True
                try:
                    df = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                    df = pd.DataFrame(df, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df['ema20'] = df['close'].ewm(span=20).mean()
                    df['ema50'] = df['close'].ewm(span=50).mean()
                    df['macd'] = df['close'].ewm(span=12).mean() - df['close'].ewm(span=26).mean()
                    df['macdsignal'] = df['macd'].ewm(span=9).mean()
                    df['rsi'] = 100 - (100 / (1 + df['close'].diff().where(lambda x: x > 0, 0).rolling(14).mean() /
                                                df['close'].diff().where(lambda x: x < 0, 0).abs().rolling(14).mean()))
                    last = df.iloc[-1]
                    price = last['close']
                    if not active_position:
                        if last['ema20'] > last['ema50'] and last['macd'] > last['macdsignal'] and 45 < last['rsi'] < 70:
                            balance = exchange.fetch_balance()
                            usdt = balance['total']['USDT']
                            if usdt < 5:
                                await send_telegram_message(app, f"⚠️ Solde insuffisant : {usdt:.2f} USDT. Achat annulé.")
                                is_processing = False
                                continue
                            qty = round(usdt / price, 1)
                            try:
                            exchange.create_market_buy_order(symbol, qty)
                        except Exception as e:
                            await send_telegram_message(app, f"❌ Erreur Bybit : {e}
Montant calculé : {qty} ADA à {price:.4f} USDT")
                            is_processing = False
                            continue
                            entry_price = price
                            highest_price = price
                            active_position = True
                            last_order_info = {"amount": qty, "entry_price": entry_price}
                            tp = round(price * 1.02, 4)
                            sl = round(price * 0.985, 4)
                            await send_telegram_message(app, f"🟢 Achat ADA à {entry_price:.4f} | TP: {tp} | SL: {sl}")
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
                            await send_telegram_message(app, f"✅ TP atteint à {current_price:.4f} 💰 Position fermée.")
                            active_position = False
                        elif current_price <= sl:
                            exchange.create_market_sell_order(symbol, qty)
                            await send_telegram_message(app, f"⛔️ SL touché à {current_price:.4f} ❌ Position coupée.")
                            active_position = False
                        elif current_price > trailing_trigger and current_price <= trailing_sl:
                            exchange.create_market_sell_order(symbol, qty)
                            await send_telegram_message(app, f"🔁 Trailing SL activé à {current_price:.4f} 🛑 Position clôturée.")
                            active_position = False
                except Exception as e:
                    logging.error(f"💥 Erreur loop: {e}")
                    await send_telegram_message(app, f"Erreur boucle : {e}")
                finally:
                    is_processing = False
            await asyncio.sleep(30 if bot_running else 5)

    async def daily_report_loop():
        while True:
            now = datetime.utcnow()
            if now.hour == 20 and now.minute == 0:
                await send_daily_summary(app)
                await asyncio.sleep(60)
            await asyncio.sleep(30)

    asyncio.create_task(trading_loop())
    asyncio.create_task(daily_report_loop())
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()
    asyncio.run(launch_telegram_bot())






