# main.py
# Lógica robusta: alerta ≠ entrada

import os
import time
import hmac
import hashlib
import requests
import numpy as np
import pytz

from datetime import datetime
from urllib.parse import urlencode
from security import send_telegram

# =========================
# CONFIGURAÇÕES
# =========================
SYMBOL_SPOT = "ARPAUSDT"
SYMBOL_FUTURES = "ARPA_USDT"

INTERVAL = "1m"

BOLL_PERIOD = 8
BOLL_STD = 2
ENTRY_PCT = 0.2

LOOP_SLEEP = 2

trade_active = False
last_alert = None

LEVERAGE = 10
QTY = 1000

MEXC_API_KEY = os.getenv("MEXC_API_KEY")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET")

BASE_URL = "https://contract.mexc.com"



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
def get_klines(SYMBOL_SPOT, INTERVAL, limit=200):
    r = requests.get(
        "https://api.mexc.com/api/v3/klines",
        params={"symbol": SYMBOL_SPOT, "interval": INTERVAL, "limit": limit},
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
# Assinatura MEXC Futures
# =========================
def mexc_sign(params: dict):
    query = urlencode(params)
    signature = hmac.new(
        MEXC_API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature

# =========================
# Verificar se já existe posição aberta (HEDGE)
# =========================
def has_open_position(symbol):
    endpoint = "/api/v1/private/position/list"
    ts = int(time.time() * 1000)

    params = {
        "timestamp": ts
    }

    params["signature"] = mexc_sign(params)

    headers = {
        "ApiKey": MEXC_API_KEY
    }

    r = requests.get(BASE_URL + endpoint, params=params, headers=headers, timeout=10)
    r.raise_for_status()

    data = r.json()["data"]

    for pos in data:
        if pos["symbol"] == symbol and float(pos["positionSize"]) != 0:
            return True

    return False
# =========================
# Criar ordem de entrada (LONG ou SHORT)
# =========================
def open_position(symbol, side):
    if has_open_position(symbol):
        return None

    ts = int(time.time() * 1000)

    params = {
        "symbol": symbol,
        "price": 0,
        "vol": QTY,
        "side": 1 if side == "LONG" else 3,  # 1=OPEN_LONG | 3=OPEN_SHORT
        "type": 1,  # Market
        "openType": 2,  # Cross
        "positionType": 2,  # Hedge
        "timestamp": ts
    }

    params["signature"] = mexc_sign(params)

    r = requests.post(
        "https://contract.mexc.com/api/v1/private/order/submit",
        params=params,
        headers={"ApiKey": MEXC_API_KEY},
        timeout=10
    )

    r.raise_for_status()
    data = r.json()["data"]
    return float(data["price"])  # preço médio

# =========================
# Função para criar SL + TP
# =========================
def create_sl_tp(symbol, side, entry_price, qty):
    """
    side: LONG ou SHORT
    """

    if side == "LONG":
        sl_price = entry_price * 0.99   # -1%
        tp_price = entry_price * 1.02   # +2%
        close_side = 4  # CLOSE_LONG
    else:
        sl_price = entry_price * 1.01   # +1%
        tp_price = entry_price * 0.98   # -2%
        close_side = 2  # CLOSE_SHORT

    ts = int(time.time() * 1000)

    headers = {"ApiKey": MEXC_API_KEY}

    # ---------- STOP LOSS ----------
    sl_params = {
        "symbol": symbol,
        "vol": qty,
        "side": close_side,
        "type": 5,  # STOP_MARKET
        "triggerPrice": round(sl_price, 6),
        "positionType": 2,  # Hedge
        "openType": 2,      # Cross
        "timestamp": ts
    }
    sl_params["signature"] = mexc_sign(sl_params)

    requests.post(
        BASE_URL + "/api/v1/private/order/submit",
        params=sl_params,
        headers=headers,
        timeout=10
    )

    # ---------- TAKE PROFIT ----------
    tp_params = {
        "symbol": symbol,
        "vol": qty,
        "side": close_side,
        "type": 6,  # TAKE_PROFIT_MARKET
        "triggerPrice": round(tp_price, 6),
        "positionType": 2,
        "openType": 2,
        "timestamp": ts
    }
    tp_params["signature"] = mexc_sign(tp_params)

    requests.post(
        BASE_URL + "/api/v1/private/order/submit",
        params=tp_params,
        headers=headers,
        timeout=10
    )


# =========================
# INIT
# =========================
print("🚀 Bot Bollinger iniciado (alerta + entrada)")
send_telegram("🚀 Bot Bollinger iniciado")


# =========================
# LOOP PRINCIPAL
# =========================
while True:
    try:
        # =========================
        # BUSCA DE KLINES E PREÇOS
        # =========================
        klines = get_klines(SYMBOL_SPOT, INTERVAL)
        closes = [float(k[4]) for k in klines]
        price = closes[-1]

        upper, lower = bollinger(closes)

        # =========================
        # MONITORA POSIÇÃO ATIVA
        # =========================
        if trade_active and not has_open_position(SYMBOL_FUTURES):
            trade_active = False
            send_telegram(f"{ts_str()} 🔁 Trade encerrado, bot liberado")

        # =========================
        # ROMPIMENTO SUPERIOR → SHORT
        # =========================
        if price > upper:
            pct = (price - upper) / upper * 100

            # ALERTA INFORMATIVO
            if last_alert != "SHORT":
                send_telegram(f"{ts_str()} ⚠️ ALERTA SHORT\nPreço: {price:.8f}\nRuptura: {pct:.2f}%")
                last_alert = "SHORT"

            # ENTRADA REAL
            if pct >= ENTRY_PCT and not trade_active:
                entry_price = open_position(SYMBOL_FUTURES, "SHORT")
                if entry_price:
                    create_sl_tp(SYMBOL_FUTURES, "SHORT", entry_price, QTY)
                    send_telegram(
                        f"{ts_str()} 🔴 SHORT EXECUTADO\n"
                        f"Entrada: {entry_price:.8f}\n"
                        f"SL/TP definidos"
                    )
                    trade_active = True

        # =========================
        # ROMPIMENTO INFERIOR → LONG
        # =========================
        elif price < lower:
            pct = (lower - price) / lower * 100

            if last_alert != "LONG":
                send_telegram(f"{ts_str()} ⚠️ ALERTA LONG\nPreço: {price:.8f}\nRuptura: {pct:.2f}%")
                last_alert = "LONG"

            if pct >= ENTRY_PCT and not trade_active:
                entry_price = open_position(SYMBOL_FUTURES, "LONG")
                if entry_price:
                    create_sl_tp(SYMBOL_FUTURES, "LONG", entry_price, QTY)
                    send_telegram(
                        f"{ts_str()} 🟢 LONG EXECUTADO\n"
                        f"Entrada: {entry_price:.8f}\n"
                        f"SL/TP definidos"
                    )
                    trade_active = True

        else:
            last_alert = None  # reseta quando volta para dentro da banda

        # =========================
        # PAUSA ENTRE CADA LOOP
        # =========================
        time.sleep(LOOP_SLEEP)

    except Exception as e:
        # ERRO GERAL → NÃO PARA O BOT
        print("[ERRO]", e)
        time.sleep(5)

