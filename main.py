# main.py
# Lógica robusta: alerta ≠ entrada
import time
import requests
import numpy as np
from datetime import datetime
import pytz

from security import send_telegram

# =========================
# CONFIGURAÇÕES
# =========================
SYMBOL = "ARPAUSDT"
INTERVAL = "1m"

BOLL_PERIOD = 8
BOLL_STD = 2
ENTRY_PCT = 0.2

LOOP_SLEEP = 2

trade_active = False
last_alert = None


# =========================
# TEMPO SP
# =========================
def agora_sp():
    return datetime.now(pytz.timezone("America/Sao_Paulo"))

def ts_str():
    return agora_sp().strftime("%Y-%m-%d %H:%M:%S")

# =========================
# MEXC
# =========================
def get_klines(symbol, interval, limit=200):
    r = requests.get(
        "https://api.mexc.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    r.raise_for_status()
    return r.json()


def bollinger(closes):
    arr = np.array(closes)
    sma = arr[-BOLL_PERIOD:].mean()
    std = arr[-BOLL_PERIOD:].std()
    return sma + BOLL_STD * std, sma - BOLL_STD * std


# =========================
# INIT
# =========================
print("🚀 Bot Bollinger iniciado (alerta + entrada)")
send_telegram("🚀 Bot Bollinger iniciado")


# =========================
# LOOP
# =========================
while True:
    try:
        klines = get_klines(SYMBOL, INTERVAL)
        closes = [float(k[4]) for k in klines]
        price = closes[-1]

        upper, lower = bollinger(closes)

        # ===== ROMPIMENTO SUPERIOR =====
        if price > upper:
            pct = (price - upper) / upper * 100

            # ALERTA INFORMATIVO
            if last_alert != "SHORT":
                send_telegram(
                    f"⚠️ ALERTA SHORT\nPreço: {price:.8f}\nRuptura: {pct:.2f}%"
                )
                last_alert = "SHORT"

            # ENTRADA REAL
            if pct >= ENTRY_PCT and not trade_active:
                send_telegram(
                    f"🔴 ENTRADA SHORT\nPreço: {price:.8f}\nRuptura: {pct:.2f}%"
                )
                trade_active = True

        # ===== ROMPIMENTO INFERIOR =====
        elif price < lower:
            pct = (lower - price) / lower * 100

            if last_alert != "LONG":
                send_telegram(
                    f"⚠️ ALERTA LONG\nPreço: {price:.8f}\nRuptura: {pct:.2f}%"
                )
                last_alert = "LONG"

            if pct >= ENTRY_PCT and not trade_active:
                send_telegram(
                    f"🟢 ENTRADA LONG\nPreço: {price:.8f}\nRuptura: {pct:.2f}%"
                )
                trade_active = True

        else:
            last_alert = None  # reseta quando volta para dentro da banda

    except Exception as e:
        print("[ERRO]", e)

    time.sleep(LOOP_SLEEP)
