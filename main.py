import ccxt
import os
import pandas as pd
import time
import logging
import matplotlib.pyplot as plt
from datetime import datetime
import requests

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Clés API
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# Clés Telegram (ajoutées manuellement ici pour test)
TELEGRAM_BOT_TOKEN = "7558300482:AAGu9LaSH0YlfvfxI5uWbC19bgz0XJx6oCQ"
TELEGRAM_CHAT_ID = "123456789"  # Remplace par ton vrai chat_id

# Initialiser Bybit
exchange = ccxt.bybit({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
})

symbol = "BTC/USDT"
timeframe = '1h'
limit = 100
risk_percent = 0.02
profit_target = 0.04
stop_loss_percent = 0.02
log_file = "trades_log.csv"

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

# Enregistrer un trade dans le log CSV
def log_trade(action, price, qty, tp, sl):
    df = pd.DataFrame([[datetime.now(), action, price, qty, tp, sl]], columns=["datetime", "action", "price", "qty", "take_profit", "stop_loss"])
    if os.path.exists(log_file):
        df.to_csv(log_file, mode='a', header=False, index=False)
    else:
        df.to_csv(log_file, mode='w', header=True, index=False)

# Afficher les niveaux Fibonacci sur un graphique
def show_chart(df, fibs):
    plt.figure(figsize=(12, 6))
    plt.plot(df['timestamp'], df['close'], label='Prix de clôture')
    for level, price in fibs.items():
        plt.axhline(y=price, color='gray', linestyle='--', label=f'Fibo {level}')
    plt.title("BTC/USDT avec niveaux de retracement Fibonacci")
    plt.xlabel("Temps")
    plt.ylabel("Prix (USDT)")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig("fibonacci_chart.png")
    logging.info("📊 Graphique sauvegardé : fibonacci_chart.png")

# Fonction principale
def run():
    df = get_ohlcv()
    high = df['high'].max()
    low = df['low'].min()
    last_price = df['close'].iloc[-1]
    fibs = fibonacci_levels(high, low)

    logging.info(f"Prix actuel: {last_price:.2f} USDT")
    logging.info(f"Niveaux Fibonacci: {fibs}")

    # Afficher graphique
    show_chart(df, fibs)

    # Taille de position à 20 USDT par défaut
    amount_usdt = 20
    amount_qty = round(amount_usdt / last_price, 5)

    # Si le prix touche ou descend sous 0.618 -> on achète
    if last_price <= fibs['0.618']:
        logging.info(f"\n🚀 Achat détecté sous niveau 0.618 ({fibs['0.618']:.2f})")
        try:
            order = exchange.create_market_buy_order(symbol, amount_qty)
            logging.info(f"\n💵 Ordre d'achat exécuté: {order['amount']} {symbol} au prix de {last_price:.2f}")

            # Définir Take Profit et Stop Loss
            take_profit_price = round(last_price * (1 + profit_target), 2)
            stop_loss_price = round(last_price * (1 - stop_loss_percent), 2)

            logging.info(f"🎯 Take Profit fixé à {take_profit_price} | 🛑 Stop Loss fixé à {stop_loss_price}")
            send_telegram_message(f"✅ Achat: {amount_qty} BTC à {last_price} USDT\n🎯 TP: {take_profit_price} | 🛑 SL: {stop_loss_price}")

            # Enregistrer dans fichier CSV
            log_trade("BUY", last_price, amount_qty, take_profit_price, stop_loss_price)

        except Exception as e:
            logging.error(f"Erreur lors de l'achat: {e}")
            send_telegram_message(f"❌ Erreur lors de l'achat: {e}")
    else:
        logging.info("Aucune condition d'achat remplie pour le moment.")
        send_telegram_message(f"🔍 Prix actuel {last_price} USDT > niveau 0.618 : pas d'achat.")

if __name__ == "__main__":
    run()
    time.sleep(10)

