import logging
import time
import random

# Configuration des logs pour afficher l'heure et les niveaux
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Exemple de simulation de trading
def simulate_trading():
    logging.info("Bot lancé et prêt à trader.")

    for i in range(10):
        prix = round(random.uniform(25000, 30000), 2)
        rsi = round(random.uniform(20, 80), 2)

        logging.info(f"🔍 Analyse #{i+1} — Prix: {prix} | RSI: {rsi}")

        if rsi < 30:
            logging.info("📈 Signal d'achat détecté.")
        elif rsi > 70:
            logging.info("📉 Signal de vente détecté.")
        else:
            logging.info("⏸️ Aucun signal, attente.")

        time.sleep(2)

    logging.info("✅ Simulation terminée.")

if __name__ == "__main__":
    simulate_trading()
