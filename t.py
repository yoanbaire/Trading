import telebot
import requests
import time
import threading
import os
from tradingview_ta import TA_Handler
from telebot import types
from flask import Flask
from threading import Thread

# --- CONFIGURATION POUR RENDER (KEEP-ALIVE) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Gold Predictor is Running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- CONFIGURATION DU BOT ---
# Utilisation du Token en direct et activation du multi-threading natif
API_TOKEN = '8606494026:AAHUTkTJonxPkXnveSJfTvAmMDNYpXjeJw0'
bot = telebot.TeleBot(API_TOKEN, threaded=True, num_threads=4)

# Variables globales
live_btc_id = None
live_or_id = None
derniere_alerte = {"BTC": 0, "OR": 0}
signaux_actifs = []

# --- FONCTIONS DE CALCUL ---
def calculer_rsi_binance(symbole, periode=14):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbole}&interval=1m&limit={periode + 1}"
        data = requests.get(url, timeout=5).json()
        closes = [float(c[4]) for c in data]
        hausses = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
        baisses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
        moy_h, moy_b = sum(hausses)/periode, sum(baisses)/periode
        return round(100 - (100 / (1 + (moy_h/moy_b))), 1) if moy_b != 0 else 100.0
    except:
        return 50.0

def calculer_prudence(prix, conf, rec):
    marge_pct = 0.003 if conf >= 80 else (0.002 if conf >= 75 else 0.001)
    return round(prix * (1 + marge_pct), 2) if "BUY" in rec else round(prix * (1 - marge_pct), 2)

def calculer_stop_loss(prix, symbole, type_signal):
    pct = 0.01 if symbole == "BTC" else 0.005 
    return round(prix * (1 - pct), 2) if type_signal == "BUY" else round(prix * (1 + pct), 2)

def calculer_confiance(score_buy, score_sell, rsi, rec):
    total = score_buy + score_sell
    if total == 0: return 50.0
    force_score = (score_buy / total if "BUY" in rec else score_sell / total) * 100
    force_rsi = (100 - rsi if "BUY" in rec else rsi)
    return round((force_score * 0.7) + (force_rsi * 0.3), 1)

def get_data():
    try:
        # Récupération Prix Binance
        res_b = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10).json()
        res_o = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=XAUTUSDT", timeout=10).json()
        
        # Analyse TradingView
        b_h = TA_Handler(symbol="BTCUSDT", screener="crypto", exchange="BINANCE", interval="1m")
        o_h = TA_Handler(symbol="XAUTUSDT", screener="crypto", exchange="BINANCE", interval="1m")
        ba, oa = b_h.get_analysis(), o_h.get_analysis()
        
        return {
            "btc": {"p": float(res_b['price']), "r": ba.indicators['RSI'], "r_p": calculer_rsi_binance("BTCUSDT"), "rec": ba.summary['RECOMMENDATION'], "b": ba.summary['BUY'], "s": ba.summary['SELL']},
            "or": {"p": float(res_o['price']), "r": oa.indicators['RSI'], "r_p": calculer_rsi_binance("XAUTUSDT"), "rec": oa.summary['RECOMMENDATION'], "b": oa.summary['BUY'], "s": oa.summary['SELL']}
        }
    except Exception as e:
        print(f"Erreur API (Binance/TV): {e}")
        return None

# --- GESTION DU CLAVIER ---
def menu_clavier():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("₿ REVOIR LIVE BITCOIN"), types.KeyboardButton("🟡 REVOIR LIVE OR (XAUT)"))
    return markup

# --- MOTEUR PRINCIPAL ---
def moteur_principal(chat_id):
    global live_btc_id, live_or_id, derniere_alerte
    print(f"Moteur d'analyse lancé pour le chat: {chat_id}")
    
    while True:
        try:
            d = get_data()
            if d and live_btc_id and live_or_id:
                b_conf = calculer_confiance(d["btc"]["b"], d["btc"]["s"], d["btc"]["r"], d["btc"]["rec"])
                o_conf = calculer_confiance(d["or"]["b"], d["or"]["s"], d["or"]["r"], d["or"]["rec"])
                t = time.strftime('%H:%M:%S')

                # Mise à jour BTC Live
                try:
                    txt_btc = f"₿ BTC: {d['btc']['p']}\n📊 RSI TV: {d['btc']['r']:.1f}\n📈 Mouv: {d['btc']['b']}B | {d['btc']['s']}S\nConf: {b_conf}% | {t}"
                    bot.edit_message_text(txt_btc, chat_id, live_btc_id)
                except: pass

                # Mise à jour OR Live
                try:
                    txt_or = f"🟡 OR: {d['or']['p']}\n📊 RSI TV: {d['or']['r']:.1f}\n📈 Mouv: {d['or']['b']}B | {d['or']['s']}S\nConf: {o_conf}% | {t}"
                    bot.edit_message_text(txt_or, chat_id, live_or_id)
                except: pass

                # Alertes RADAR
                for key, conf, info in [("BTC", b_conf, d["btc"]), ("OR", o_conf, d["or"])]:
                    if conf >= 74 and (time.time() - derniere_alerte[key] > 300):
                        prud = calculer_prudence(info['p'], conf, info['rec'])
                        msg = f"⚠️ RADAR {key} ({conf}%) ⚠️\nDirection: {info['rec']}\n💰 Entrée: {info['p']}\n🎯 Cible: {prud}"
                        bot.send_message(chat_id, msg)
                        derniere_alerte[key] = time.time()
            else:
                print("En attente de données valides...")
        except Exception as e:
            print(f"Erreur boucle moteur: {e}")
            
        time.sleep(60)

# --- COMMANDES ---
@bot.message_handler(commands=['start'])
def start(message):
    global live_btc_id, live_or_id
    bot.send_message(message.chat.id, "Bot v3.8 Stable Activé 🚀", reply_markup=menu_clavier())
    
    live_btc_id = bot.send_message(message.chat.id, "Initialisation BTC...").message_id
    live_or_id = bot.send_message(message.chat.id, "Initialisation OR...").message_id
    
    # Lancement du moteur dans un thread séparé
    t = threading.Thread(target=moteur_principal, args=(message.chat.id,), daemon=True)
    t.start()

# --- LANCEMENT ---
if __name__ == "__main__":
    keep_alive()
    bot.remove_webhook()
    print("Log: Système prêt. Polling en cours...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)

