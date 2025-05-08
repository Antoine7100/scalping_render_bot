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
from telegram.ext import Updater, CommandHandler

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "1440739670"
TELEGRAM_USER_ID = 1440739670

# Initialiser Bybit en Perp
exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

symbols = ["ADA/USDT:USDT", "DOGE/USDT:USDT"]
leverage = 5

# Réglage du levier pour chaque actif
for symbol in symbols:
    try:
        exchange.set_leverage(leverage, symbol)
    except Exception as e:
        logging.warning(f"⚠️ Levier non modifié pour {symbol} : {e}")

# Fonction sécurisée d'envoi de message

def secure_send_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, data=payload)
    except Exception as e:
        logging.error(f"Erreur Telegram : {e}")

# Gestion des commandes Telegram
updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)

def start(update, context):
    secure_send_message("Bot démarré !")

def stop(update, context):
    secure_send_message("Bot arrêté !")

def status(update, context):
    secure_send_message("✅ Bot actif.")

def bilan(update, context):
    secure_send_message("📊 Bilan du jour : Agressif sur ADA et DOGE")

def trades(update, context):
    if not os.path.exists("trades_log.csv"):
        context.bot.send_message(chat_id=update.effective_chat.id, text="Aucun trade enregistré.")
        return
    df = pd.read_csv("trades_log.csv")
    trades_list = df.tail(5).to_string(index=False)
    context.bot.send_message(chat_id=update.effective_chat.id, text=f"Derniers trades :\n{trades_list}")

updater.dispatcher.add_handler(CommandHandler("start", start))
updater.dispatcher.add_handler(CommandHandler("stop", stop))
updater.dispatcher.add_handler(CommandHandler("status", status))
updater.dispatcher.add_handler(CommandHandler("bilan", bilan))
updater.dispatcher.add_handler(CommandHandler("trades", trades))
updater.start_polling()

# Fonction de trading avec stratégie agressive

def trade_action(action, symbol, price, qty):
    try:
        if action == "buy":
            order = exchange.create_market_buy_order(symbol, qty)
            secure_send_message(f"🟢 Achat {symbol} à {price}")
        elif action == "sell":
            order = exchange.create_market_sell_order(symbol, qty)
            secure_send_message(f"🔴 Vente {symbol} à {price}")
    except Exception as e:
        secure_send_message(f"Erreur lors de l'action de trading : {e}")

# Boucle principale de trading agressif multi-actifs

def run_bot():
    while True:
        for symbol in symbols:
            try:
                df = exchange.fetch_ohlcv(symbol, "1m", limit=100)
                df = pd.DataFrame(df, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                last_price = df['close'].iloc[-1]
                rsi = df['close'].pct_change().mean() * 100
                macd = df['close'].ewm(span=12).mean() - df['close'].ewm(span=26).mean()
                macdsignal = macd.ewm(span=9).mean()
                trend_up = df['close'].ewm(span=20).mean().iloc[-1] > df['close'].ewm(span=50).mean().iloc[-1]
                signal_ok = macd.iloc[-1] > macdsignal.iloc[-1] and 40 < rsi < 70

                # Achat agressif
                if trend_up and signal_ok:
                    trade_action("buy", symbol, last_price, 1)

                # Vente agressive (Take Profit / Stop Loss)
                take_profit = last_price * 1.03
                stop_loss = last_price * 0.97

                if last_price >= take_profit:
                    trade_action("sell", symbol, last_price, 1)
                    secure_send_message(f"🎯 TP atteint pour {symbol} à {last_price}")
                elif last_price <= stop_loss:
                    trade_action("sell", symbol, last_price, 1)
                    secure_send_message(f"⛔️ SL activé pour {symbol} à {last_price}")

                time.sleep(10)
            except Exception as e:
                secure_send_message(f"Erreur pour {symbol} : {e}")
                time.sleep(5)

if __name__ == "__main__":
    try:
        secure_send_message("🚀 Démarrage du bot agressif pour ADA et DOGE...")
        run_bot()
    except Exception as e:
        secure_send_message(f"Erreur critique au démarrage : {e}")


