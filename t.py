import telebot
import requests
import time
import threading
import os
from tradingview_ta import TA_Handler
from telebot import types

print("Bot démarré...")

# --- CONFIGURATION ---
API_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(API_TOKEN)

live_btc_id, live_or_id = None, None
derniere_alerte = {"BTC": 0, "OR": 0}
signaux_actifs = []

def calculer_rsi_binance(symbole, periode=14):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbole}&interval=1m&limit={periode + 1}"
        data = requests.get(url, timeout=10).json()
        closes = [float(c[4]) for c in data]
        hausses = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
        baisses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
        moy_h, moy_b = sum(hausses)/periode, sum(baisses)/periode
        return round(100 - (100 / (1 + (moy_h/moy_b))), 1) if moy_b != 0 else 100.0
    except Exception as e:
        print("Erreur RSI Binance:", e)
        return 0.0

def calculer_prudence(prix, conf, rec):
    marge_pct = 0.003 if conf >= 80 else (0.002 if conf >= 75 else 0.001)
    return round(prix * (1 + marge_pct), 2) if "BUY" in rec else round(prix * (1 - marge_pct), 2)

def calculer_stop_loss(prix, symbole, type_signal):
    pct = 0.01 if symbole == "BTC" else 0.005 
    return round(prix * (1 - pct), 2) if type_signal == "BUY" else round(prix * (1 + pct), 2)

def calculer_confiance(score_buy, score_sell, rsi, rec):
    total = score_buy + score_sell
    if total == 0:
        return 50.0
    force_score = (score_buy / total if "BUY" in rec else score_sell / total) * 100
    force_rsi = (100 - rsi if "BUY" in rec else rsi)
    return round((force_score * 0.7) + (force_rsi * 0.3), 1)

def verifier_signaux(prix_actuel, symbole, chat_id):
    global signaux_actifs
    for s in signaux_actifs[:]:
        if s['symbole'] == symbole:
            est_valide = (s['type'] == "BUY" and prix_actuel >= s['cible']) or \
                         (s['type'] == "SELL" and prix_actuel <= s['cible'])
            est_stoppe = (s['type'] == "BUY" and prix_actuel <= s['sl']) or \
                         (s['type'] == "SELL" and prix_actuel >= s['sl'])
            if est_valide:
                bot.send_message(chat_id, f"✅ SIGNAL {s['confiance']}% VALIDÉ ({symbole})\nCible: {s['cible']} 💰")
                signaux_actifs.remove(s)
            elif est_stoppe:
                bot.send_message(chat_id, f"❌ ÉCHEC DU SIGNAL {s['confiance']}% ({symbole})\nStop Loss touché à: {s['sl']} 📉")
                signaux_actifs.remove(s)

def get_data():
    try:
        res_b = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10).json()
        res_o = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=XAUTUSDT", timeout=10).json()

        b_h = TA_Handler(symbol="BTCUSDT", screener="crypto", exchange="BINANCE", interval="1m")
        o_h = TA_Handler(symbol="XAUTUSDT", screener="crypto", exchange="BINANCE", interval="1m")

        try:
            ba = b_h.get_analysis()
            oa = o_h.get_analysis()
        except Exception as e:
            print("Erreur TradingView:", e)
            return None

        return {
            "btc": {
                "p": float(res_b['price']),
                "r": ba.indicators['RSI'],
                "r_p": calculer_rsi_binance("BTCUSDT"),
                "rec": ba.summary['RECOMMENDATION'],
                "b": ba.summary['BUY'],
                "s": ba.summary['SELL']
            },
            "or": {
                "p": float(res_o['price']),
                "r": oa.indicators['RSI'],
                "r_p": calculer_rsi_binance("XAUTUSDT"),
                "rec": oa.summary['RECOMMENDATION'],
                "b": oa.summary['BUY'],
                "s": oa.summary['SELL']
            }
        }

    except Exception as e:
        print("Erreur get_data:", e)
        return None

# --- CLAVIER ---
def menu_clavier():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        types.KeyboardButton("₿ REVOIR LIVE BITCOIN"),
        types.KeyboardButton("🟡 REVOIR LIVE OR (XAUT)")
    )
    return markup

# --- CLIC ---
@bot.message_handler(func=lambda message: message.text in ["₿ REVOIR LIVE BITCOIN", "🟡 REVOIR LIVE OR (XAUT)"])
def bouton_clavier_appuye(message):
    global live_btc_id, live_or_id

    if "BITCOIN" in message.text:
        try:
            bot.delete_message(message.chat.id, live_btc_id)
        except Exception as e:
            print(e)
        live_btc_id = bot.send_message(message.chat.id, "Réactivation BTC...").message_id
    else:
        try:
            bot.delete_message(message.chat.id, live_or_id)
        except Exception as e:
            print(e)
        live_or_id = bot.send_message(message.chat.id, "Réactivation OR...").message_id

def moteur_principal(chat_id):
    global derniere_alerte, signaux_actifs, live_btc_id, live_or_id

    while True:
        print("Cycle exécuté...")

        d = get_data()

        if d:
            b_conf = calculer_confiance(d["btc"]["b"], d["btc"]["s"], d["btc"]["r"], d["btc"]["rec"])
            o_conf = calculer_confiance(d["or"]["b"], d["or"]["s"], d["or"]["r"], d["or"]["rec"])

            verifier_signaux(d["btc"]["p"], "BTC", chat_id)
            verifier_signaux(d["or"]["p"], "OR", chat_id)

            for key, conf, info in [("BTC", b_conf, d["btc"]), ("OR", o_conf, d["or"])]:
                if conf >= 74 and (time.time() - derniere_alerte[key] > 300):
                    prud = calculer_prudence(info['p'], conf, info['rec'])
                    type_sig = "BUY" if "BUY" in info['rec'] else "SELL"
                    sl_prix = calculer_stop_loss(info['p'], key, type_sig)

                    bot.send_message(
                        chat_id,
                        f"⚠️RADAR {key}⚠️\nConfiance: {conf}%\nDirection: {info['rec']}\n\n💰Entrée: {info['p']}\n🎯Cible: {prud}\n🛑Stop Loss: {sl_prix}",
                        reply_markup=menu_clavier()
                    )

                    signaux_actifs.append({
                        'symbole': key,
                        'cible': prud,
                        'sl': sl_prix,
                        'type': type_sig,
                        'confiance': conf
                    })

                    derniere_alerte[key] = time.time()

            t = time.strftime('%H:%M:%S')

            try:
                txt_btc = f"BTC: {d['btc']['p']}\n📊 RSI TV: {d['btc']['r']:.1f} | 🏠 Moi: {d['btc']['r_p']}\n📈 Mouv: {d['btc']['b']}B | {d['btc']['s']}S\nConf: {b_conf}% | {t}"
                bot.edit_message_text(txt_btc, chat_id, live_btc_id)

                txt_or = f"OR: {d['or']['p']}\n📊 RSI TV: {d['or']['r']:.1f} | 🏠 Moi: {d['or']['r_p']}\n📈 Mouv: {d['or']['b']}B | {d['or']['s']}S\nConf: {o_conf}% | {t}"
                bot.edit_message_text(txt_or, chat_id, live_or_id)

            except Exception as e:
                print("Erreur update message:", e)

        time.sleep(60)

@bot.message_handler(commands=['start'])
def start(message):
    global live_btc_id, live_or_id

    bot.send_message(message.chat.id, "Bot v3.8 (Clavier Fixe) Activé 🚀", reply_markup=menu_clavier())

    live_btc_id = bot.send_message(message.chat.id, "BTC...").message_id
    live_or_id = bot.send_message(message.chat.id, "OR...").message_id

    threading.Thread(
        target=moteur_principal,
        args=(message.chat.id,),
        daemon=True
    ).start()

# --- POLLING SÉCURISÉ ---
while True:
    try:
        bot.polling(none_stop=True, timeout=60)
    except Exception as e:
        print("Erreur polling:", e)
        time.sleep(5)
