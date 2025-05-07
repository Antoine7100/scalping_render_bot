import os
import requests
import logging
import subprocess
from telegram import Bot

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Arrêter tous les processus du bot en cours
try:
    result = subprocess.run(['pgrep', '-f', 'python'], capture_output=True, text=True)
    pids = result.stdout.splitlines()
    for pid in pids:
        if pid.isdigit():
            subprocess.run(['kill', '-9', pid])
            logging.info(f"Processus bot arrêté : {pid}")
except Exception as e:
    logging.warning(f"Erreur lors de l'arrêt des processus bot : {e}")

# Supprimer automatiquement le webhook au démarrage
try:
    response = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook")
    if response.status_code == 200:
        logging.info("Webhook supprimé avec succès.")
    else:
        logging.warning(f"Échec de la suppression du webhook : {response.text}")
except Exception as e:
    logging.error(f"Erreur lors de la suppression du webhook : {e}")

try:
    Bot(token=TELEGRAM_BOT_TOKEN).delete_webhook()
except Exception as e:
    logging.warning(f"Impossible de supprimer le webhook via la bibliothèque Telegram : {e}")


import ccxt
import pandas as pd
import time
from datetime import datetime
import numpy as np
from flask import Flask
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncio
import schedule
import shutil

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "1440739670"
TELEGRAM_USER_ID = 1440739670

exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})


symbol = "ADA/USDT:USDT"
leverage = 5
limit = 150
timeframe = '1m'
log_file = "trades_log.csv"
backup_dir = "log_backups"
os.makedirs(backup_dir, exist_ok=True)

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

# === DÉCORATEUR RESTRICTED ===
def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != TELEGRAM_USER_ID:
            await update.message.reply_text("⛔️ Accès refusé.")
            return
        return await func(update, context)
    return wrapper

# === STRATÉGIE DE TRADING ===
def trading_loop():
    global active_position, entry_price, highest_price, last_order_info, trade_count, trade_wins, trade_losses, last_trade_type
    if not bot_running:
        return

    symbols = ["ADA/USDT:USDT", "DOGE/USDT:USDT"]

    try:
        for symbol in symbols:
            logging.info(f"Récupération des données pour {symbol}")
            df = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(df, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

            if df.empty:
                logging.warning(f"Aucune donnée récupérée pour {symbol}")
                continue

            # Indicateurs techniques ultra-agressifs
            df['ema5'] = df['close'].ewm(span=5).mean()
            df['ema20'] = df['close'].ewm(span=20).mean()
            df['rsi'] = 100 - (100 / (1 + (df['close'].diff().gt(0).rolling(window=5).mean() /
                                            df['close'].diff().lt(0).rolling(window=5).mean())))
            df['stoch_rsi'] = (df['rsi'] - df['rsi'].rolling(window=14).min()) / (df['rsi'].rolling(window=14).max() - df['rsi'].rolling(window=14).min())
            df['macd'] = df['close'].ewm(span=6).mean() - df['close'].ewm(span=13).mean()
            df['signal'] = df['macd'].ewm(span=4).mean()
            df['atr'] = df['high'] - df['low']

            last = df.iloc[-1]
            price = last['close']
            sl = entry_price - 1.5 * last['atr']
            tp = entry_price + 1.5 * last['atr']

            logging.info(f"{symbol} | Prix: {price} | RSI: {last['rsi']:.2f} | Stoch RSI: {last['stoch_rsi']:.2f} | MACD: {last['macd']:.2f} | Signal: {last['signal']:.2f}")

            # Trading ultra-agressif : Achat si RSI < 50 ou Stoch RSI < 0.2
            if not active_position or last['rsi'] < 50 or last['stoch_rsi'] < 0.2:
                if last['macd'] > last['signal']:
                    logging.info(f"Condition d'achat remplie pour {symbol}")
                    balance = exchange.fetch_balance()
                    usdt = balance['USDT']['free']
                    position_size = round((usdt * 0.03) / price, 1)
                    exchange.create_market_buy_order(symbol, position_size)
                    entry_price = price
                    highest_price = price
                    active_position = True
                    last_order_info = {"amount": position_size, "entry_price": entry_price}
                    log_trade([datetime.now(), f"buy {symbol}", price, position_size, tp, sl])
                    send_telegram_message_sync(f"🟢 Achat {symbol} à {entry_price:.4f} | TP: {tp} | SL: {sl}")
                else:
                    logging.info(f"Condition d'achat non remplie pour {symbol}")
            elif last['rsi'] > 50 or last['stoch_rsi'] > 0.8:
                if last['macd'] < last['signal']:
                    logging.info(f"Condition de vente remplie pour {symbol}")
                    exchange.create_market_sell_order(symbol, last_order_info['amount'])
                    trade_losses += 1
                    send_telegram_message_sync(f"⛔️ SL touché à {price:.4f} sur {symbol} ❌ Position coupée.")
                    active_position = False
                    trade_count += 1

    except Exception as e:
        logging.error(f"Erreur trading_loop : {e}")
        send_telegram_message_sync(f"Erreur stratégie : {e}")

schedule.every(2).seconds.do(trading_loop)


# === OUTILS ===
import time

def send_telegram_message_sync(msg):
    retries = 3  # Nombre de tentatives
    for attempt in range(retries):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": constants.ParseMode.HTML}
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                return
            else:
                logging.error(f"Erreur lors de l'envoi du message : {response.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Erreur de connexion Telegram (tentative {attempt + 1}/{retries}) : {e}")
        time.sleep(2)
    logging.error("Impossible d'envoyer le message après plusieurs tentatives.")

def log_trade(row_data):
    header = "datetime,action,price,qty,take_profit,stop_loss\n"
    file_exists = os.path.exists(log_file)
    with open(log_file, 'a') as f:
        if not file_exists:
            f.write(header)
        f.write(",".join(map(str, row_data)) + "\n")

# === VÉRIFICATION DES POSITIONS ===
@restricted
async def open_trade_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        positions = exchange.fetch_positions()
        position = next((p for p in positions if p['symbol'] == symbol and p['contracts'] > 0), None)

        if position:
            entry = position['entryPrice']
            qty = position['contracts']
            current_price = exchange.fetch_ticker(symbol)['last']
            tp = round(entry * 1.03, 4)
            sl = round(entry * 0.97, 4)
            tendance = "📈 Vers TP" if current_price > entry else "📉 Vers SL"
            msg = f"📊 Position réelle détectée\nEntrée : {entry:.4f}\nQuantité : {qty}\nTP : {tp} | SL : {sl}\n{tendance}"
        else:
            msg = "❌ Aucune position ouverte sur Bybit."

        await update.callback_query.edit_message_text(text=msg)
    except Exception as e:
        logging.error(f"Erreur open_trade_status : {e}")
        await update.callback_query.edit_message_text(text=f"Erreur lors de la récupération de la position : {e}")

# === COMMANDES TELEGRAM ===
@restricted
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ton ID Telegram est : {update.effective_user.id}")

@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = "\n".join([
        "📋 Commandes disponibles :",
        "/startbot - Lancer le bot",
        "/stopbot - Arrêter le bot",
        "/menu - Afficher le menu de contrôle",
        "/close - Fermer une position manuellement",
        "/bilan - Afficher les statistiques de performance",
        "/myid - Afficher ton ID Telegram",
        "/help - Afficher cette aide"
    ])
    await update.message.reply_text(message)

@restricted
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = True
    await update.callback_query.edit_message_text("▶️ Bot lancé.")

@restricted
async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = False
    await update.callback_query.edit_message_text("⏸ Bot arrêté.")

@restricted
async def force_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_position, last_order_info
    if active_position:
        try:
            qty = last_order_info.get("amount", 0)
            price = exchange.fetch_ticker(symbol)['last']
            exchange.create_market_sell_order(symbol, qty)
            active_position = False
            await update.callback_query.edit_message_text(f"❌ Vente forcée à {price:.4f} pour {qty} ADA")
        except Exception as e:
            await update.callback_query.edit_message_text(f"Erreur force_sell : {e}")
    else:
        await update.callback_query.edit_message_text("Aucune position à clôturer.")

@restricted
async def status_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "✅ Bot actif." if bot_running else "⛔ Bot en pause."
    await update.callback_query.edit_message_text(msg)

@restricted
async def bilan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(log_file):
        await update.callback_query.edit_message_text("Aucun trade enregistré.")
        return
    df = pd.read_csv(log_file)
    tp = (df['action'] == 'TP').sum()
    sl = (df['action'] == 'SL').sum()
    total = len(df)
    await update.callback_query.edit_message_text(
        f"📈 Bilan :\n✅ TP : {tp}\n❌ SL : {sl}\n📦 Total : {total}"
    )

@restricted
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶️ Lancer le bot", callback_data='startbot'),
         InlineKeyboardButton("⏸ Stopper le bot", callback_data='stopbot')],
        [InlineKeyboardButton("📊 Statut", callback_data='status'),
         InlineKeyboardButton("🔍 Trade en cours", callback_data='open_trade')],
        [InlineKeyboardButton("📈 Bilan", callback_data='bilan')],  # Bilan ajouté
        [InlineKeyboardButton("❌ Fermer position", callback_data='close')]  # Fermer position ajouté
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
    elif data == "bilan":
        await bilan(update, context)

async def launch_telegram():
    app_telegram = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app_telegram.add_handler(CommandHandler("startbot", start_bot))
    app_telegram.add_handler(CommandHandler("stopbot", stop_bot))
    app_telegram.add_handler(CommandHandler("menu", menu))
    app_telegram.add_handler(CommandHandler("close", force_sell))
    app_telegram.add_handler(CommandHandler("bilan", bilan))
    app_telegram.add_handler(CommandHandler("myid", myid))
    app_telegram.add_handler(CommandHandler("help", help_command))
    app_telegram.add_handler(CallbackQueryHandler(handle_button))
    print("✅ Telegram bot en ligne. En attente de commandes...")
    await app_telegram.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()
    asyncio.get_event_loop().run_until_complete(launch_telegram())


