#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SCANNER DIDI PROFISSIONAL – Binance Futures

Melhorias:
• Retry automático HTTP
• Reconexão inteligente
• Detecção de perda de internet
• Log de latência da Binance
• Watchdog de execução
• Session persistente
• Tratamento robusto de exceções
• Anti-duplicidade
• Candle fechado apenas
"""

# ===============================
# IMPORTS
# ===============================
import os
import time
import signal
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np

# ===============================
# CONFIGURAÇÕES
# ===============================
BINANCE_FAPI = "https://fapi.binance.com"
KLINES_LIMIT = 200
POST_CLOSE_DELAY_15 = 15

ADX_MIN = 15
BB_WIDTH_MIN = 0.015

TZ_SP = ZoneInfo("America/Sao_Paulo")

HTTP_TIMEOUT = 20
HTTP_RETRIES = 3
INTERNET_TEST_URL = "https://api.binance.com/api/v3/time"

# ===============================
# LOG
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
LOGGER = logging.getLogger("scanner")

# ===============================
# SESSION PERSISTENTE
# ===============================
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(max_retries=2)
session.mount("https://", adapter)

# ===============================
# TIME
# ===============================
def now_sp():
    return datetime.now(TZ_SP)

# ===============================
# SHUTDOWN CONTROL
# ===============================
SHUTDOWN = False
LAST_EXECUTION = time.time()

def handle_shutdown(sig, frame):
    global SHUTDOWN
    SHUTDOWN = True
    LOGGER.warning("Shutdown recebido (%s)", sig)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# ===============================
# INTERNET CHECK
# ===============================
def internet_ok():
    try:
        start = time.time()
        r = session.get(INTERNET_TEST_URL, timeout=5)
        latency = (time.time() - start) * 1000
        if r.status_code == 200:
            LOGGER.info("Internet OK | Latência Binance: %.0f ms", latency)
            return True
        return False
    except Exception:
        LOGGER.warning("Sem conexão com a internet...")
        return False

# ===============================
# ENV
# ===============================
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("TELEGRAM_TOKEN e TELEGRAM_CHAT_ID não definidos")

# ===============================
# ATIVOS
# ===============================
raw_symbols = os.getenv("BA", "")
SYMBOLS = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]

if not SYMBOLS:
    raise RuntimeError("Nenhum símbolo definido na variável BA do .env")

# ===============================
# TELEGRAM
# ===============================
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }

    try:
        r = session.post(url, data=payload, timeout=10)
        r.raise_for_status()

        # log opcional de sucesso
        LOGGER.info("Mensagem enviada ao Telegram")

    except requests.exceptions.HTTPError as e:
        LOGGER.error("Erro HTTP Telegram: %s | resposta=%s", e, r.text)

    except Exception as e:
        LOGGER.error("Erro Telegram: %s", e)


# ===============================
# FETCH KLINES COM RETRY
# ===============================
def fetch_klines(symbol: str, interval="15m") -> pd.DataFrame:

    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": KLINES_LIMIT}

    for attempt in range(HTTP_RETRIES):
        try:
            start = time.time()
            r = session.get(url, params=params, timeout=HTTP_TIMEOUT)
            latency = (time.time() - start) * 1000

            r.raise_for_status()

            LOGGER.debug("[%s] Latência API: %.0f ms", symbol, latency)

            df = pd.DataFrame(r.json(), columns=[
                "open_time","open","high","low","close","volume",
                "close_time","qav","trades","tb_base","tb_quote","ignore"
            ])

            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            df.set_index("open_time", inplace=True)

            for c in ["open","high","low","close"]:
                df[c] = df[c].astype(float)

            return df.iloc[:-1]

        except requests.exceptions.RequestException as e:
            LOGGER.warning("[%s] Retry %d/%d erro: %s",
                           symbol, attempt+1, HTTP_RETRIES, e)
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Falha ao obter klines para {symbol}")

# ===============================
# INDICADORES
# ===============================
def sma(series, p):
    return series.rolling(p).mean()

def adx(df, period=8):
    high, low, close = df["high"], df["low"], df["close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()

    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)

    plus_di = 100 * plus_dm.rolling(period).sum() / atr
    minus_di = 100 * minus_dm.rolling(period).sum() / atr

    den = (plus_di + minus_di).replace(0, np.nan)
    dx = abs(plus_di - minus_di) / den * 100

    return dx.rolling(period).mean()

def bollinger_width(series, period=8, std=2):
    ma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    return (2 * std * sd) / ma.replace(0, np.nan)

# ===============================
# CHECK SIGNAL (mantido igual)
# ===============================
def check_signal(df):

    df = df.dropna()
    if len(df) < 50:
        return None

    close = df["close"]

    s3 = sma(close, 3)
    s8 = sma(close, 8)
    s20 = sma(close, 20)

    s3_3, s3_2, s3_1 = s3.iloc[-3], s3.iloc[-2], s3.iloc[-1]
    s8_3, s8_2, s8_1 = s8.iloc[-3], s8.iloc[-2], s8.iloc[-1]
    s20_3, s20_2, s20_1 = s20.iloc[-3], s20.iloc[-2], s20.iloc[-1]

    adx_series = adx(df)
    if len(adx_series) < 3:
        return None

    adx_now = adx_series.iloc[-1]
    adx_prev = adx_series.iloc[-2]

    if pd.isna(adx_now) or adx_now < ADX_MIN:
        return None

    if adx_now < adx_prev:
        return None

    bbw_series = bollinger_width(close)
    bbw_now = bbw_series.iloc[-1]
    bbw_prev = bbw_series.iloc[-2]

    if pd.isna(bbw_now) or bbw_now < BB_WIDTH_MIN:
        return None

    if bbw_now < bbw_prev:
        return None

    slope3 = s3_1 - s3_2
    slope8 = s8_1 - s8_2
    min_slope = close.iloc[-1] * 0.0005
    price = close.iloc[-1]

    cruzou_long = (
        (s3_3 < s8_3 and s3_2 > s8_2) or
        (s3_2 < s8_2 and s3_1 > s8_1)
    )

    alinhamento_long = (s3_1 > s8_1 > s20_1)
    inclinacao_long = (slope3 > min_slope and slope8 > 0)
    separacao_ok = (abs(s3_1 - s8_1) / price) > 0.0005
    rompimento = price > df["high"].iloc[-2]

    if cruzou_long and alinhamento_long and inclinacao_long and separacao_ok and rompimento:
        return "LONG"

    cruzou_short = (
        (s3_3 > s8_3 and s3_2 < s8_2) or
        (s3_2 > s8_2 and s3_1 < s8_1)
    )

    alinhamento_short = (s3_1 < s8_1 < s20_1)
    inclinacao_short = (-slope3 > min_slope and slope8 < 0)
    separacao_ok = (abs(s3_1 - s8_1) / price) > 0.0005
    rompimento = price < df["low"].iloc[-2]

    if cruzou_short and alinhamento_short and inclinacao_short and separacao_ok and rompimento:
        return "SHORT"

    return None

# ===============================
# ALERTA
# ===============================
def send_alert(symbol, price, signal):
    msg = (
        f"🚨 <b>ALERTA DIDI 15m</b>\n\n"
        f"Par: <b>{symbol}</b>\n"
        f"Sinal: <b>{signal}</b>\n"
        f"Preço: <b>{price:.4f}</b>\n"
        f"Horário SP: {now_sp().strftime('%d/%m/%Y %H:%M:%S')}"
    )
    send_telegram(msg)

# ===============================
# WATCHDOG
# ===============================
def watchdog():
    global LAST_EXECUTION
    while not SHUTDOWN:
        if time.time() - LAST_EXECUTION > 600:
            LOGGER.error("Watchdog detectou travamento do scanner!")
        time.sleep(60)

# ===============================
# LOOP PRINCIPAL
# ===============================
LAST_SIGNAL = {}

def scanner_loop():
    global LAST_EXECUTION

    LOGGER.info("Scanner iniciado. Monitorando %d ativos.", len(SYMBOLS))

    while not SHUTDOWN:

        if not internet_ok():
            time.sleep(30)
            continue

        for symbol in SYMBOLS:
            try:
                df = fetch_klines(symbol)

                if df is None or df.empty:
                    continue

                sig = check_signal(df)
                if not sig:
                    continue

                last = LAST_SIGNAL.get(symbol)
                if last == sig:
                    continue

                price = df["close"].iloc[-1]
                send_alert(symbol, price, sig)

                LAST_SIGNAL[symbol] = sig
                LOGGER.info("[%s] Novo sinal: %s", symbol, sig)

            except Exception as e:
                LOGGER.error("[%s] Erro: %s", symbol, e)

            time.sleep(0.1)

        LAST_EXECUTION = time.time()
        time.sleep(30)

# ===============================
# MAIN
# ===============================
def main():
    threading.Thread(target=watchdog, daemon=True).start()
    send_telegram("🤖 Scanner Didi resiliente iniciado")
    scanner_loop()

if __name__ == "__main__":
    main()

