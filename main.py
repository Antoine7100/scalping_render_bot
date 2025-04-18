import ccxt
import os
import pandas as pd
import time
import logging

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Clés API
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

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
    return df

# Fonction principale
def run():
    df = get_ohlcv()
    high = df['high'].max()
    low = df['low'].min()
    last_price = df['close'].iloc[-1]
    fibs = fibonacci_levels(high, low)

    logging.info(f"Prix actuel: {last_price:.2f} USDT")
    logging.info(f"Niveaux Fibonacci: {fibs}")

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

            # (Simulation - Print seulement, implémentation complète possible en ordre conditionnel)
            logging.info("🧪 Ordres TP/SL simulés. Implémentation réelle possible avec Bybit API v5 directe.")

        except Exception as e:
            logging.error(f"Erreur lors de l'achat: {e}")
    else:
        logging.info("Aucune condition d'achat remplie pour le moment.")

if __name__ == "__main__":
    run()
    time.sleep(10)
