#!/usr/bin/env python3
"""
Binance Futures 15m scanner -> Telegram alerts
Compatível com execução LOCAL e RAILWAY
"""

import os
import sys
import time
import signal
import logging
import requests
import numpy as np
import pandas as pd
from datetime import datetime
import pytz

# =====================================================
# CONFIGURAÇÕES GERAIS
# =====================================================

POLL_SECONDS = int(os.getenv("POLL_SECONDS", 120))
KLINES_LIMIT = int(os.getenv("KLINES_LIMIT", 200))

BOLLINGER_PERIOD = 8
BOLLINGER_STD = 2
ADX_PERIOD = 8

BOLLINGER_WIDTH_MIN_PCT = float(os.getenv("BOLLINGER_WIDTH_MIN_PCT", 0.015))
ADX_MIN = float(os.getenv("ADX_MIN", 15))
ADX_ACCEL_THRESHOLD = float(os.getenv("ADX_ACCEL_THRESHOLD", 0.05))

BINANCE_FAPI = "https://fapi.binance.com"
TZ_SP = pytz.timezone("America/Sao_Paulo")

FIXED_SYMBOLS = [
    "BCHUSDT", "BNBUSDT", "CHZUSDT", "DOGEUSDT", "ENAUSDT",
    "ETHUSDT", "JASMYUSDT", "SOLUSDT", "UNIUSDT",
    "XMRUSDT", "XRPUSDT"
]

# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
LOGGER = logging.getLogger("scanner")

# =====================================================
# TELEGRAM (CENTRALIZADO)
# =====================================================

def telegram_env():
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    return token, chat_id

def send_telegram(msg: str):
    token, chat_id = telegram_env()
    if not token or not chat_id:
        LOGGER.warning("Telegram desabilitado (env ausente)")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        r.raise_for_status()
    except Exception:
        LOGGER.exception("Erro ao enviar mensagem Telegram")

def send_telegram_document(file_path, caption=None):
    token, chat_id = telegram_env()
    if not token or not chat_id:
        return
    if not os.path.isfile(file_path):
        LOGGER.warning("Arquivo não encontrado: %s", file_path)
        return
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption or ""},
                files={"document": f},
                timeout=30
            )
            r.raise_for_status()
    except Exception:
        LOGGER.exception("Erro ao enviar documento Telegram")

# =====================================================
# UTILITÁRIOS
# =====================================================

def now_sp():
    return datetime.now(TZ_SP)

def now_sp_str():
    return now_sp().strftime("%Y-%m-%d %H:%M:%S %Z")

def today_str():
    return now_sp().strftime("%Y-%m-%d")

# =====================================================
# BINANCE
# =====================================================
def fetch_klines(symbol, interval="15m", limit=KLINES_LIMIT):
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            },
            timeout=10
        )

        if r.status_code != 200:
            LOGGER.warning(
                "Binance HTTP %s para %s | body=%s",
                r.status_code, symbol, r.text[:200]
            )
            return None

        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            LOGGER.warning("Resposta inválida Binance (%s): %s", symbol, data)
            return None

        cols = [
            "open_time","open","high","low","close","volume",
            "close_time","qav","trades","tb_base","tb_quote","ignore"
        ]

        df = pd.DataFrame(data, columns=cols)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")

        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df = df.set_index("open_time").iloc[:-1]

        if df.empty:
            LOGGER.warning("DF vazio após parse (%s)", symbol)
            return None

        return df

    except requests.exceptions.RequestException as e:
        LOGGER.warning("Erro HTTP Binance (%s): %s", symbol, e)
        return None

    except Exception as e:
        LOGGER.exception("Erro inesperado fetch_klines (%s)", symbol)
        return None


# =====================================================
def analyze_symbol(symbol):
    df = fetch_klines(symbol)

    if df is None or df.empty or len(df) < 50:
        return None

# =====================================================
# INDICADORES
# =====================================================

def sma(s, p): return s.rolling(p, 1).mean()

def true_range(df):
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)

def atr(df, p=14):
    return true_range(df).rolling(p, p).mean()

def bollinger_bands(s):
    ma = s.rolling(BOLLINGER_PERIOD, 1).mean()
    std = s.rolling(BOLLINGER_PERIOD, 1).std()
    upper = ma + BOLLINGER_STD * std
    lower = ma - BOLLINGER_STD * std
    width = (upper - lower) / ma.replace(0, np.nan)
    return upper, lower, width

def adx(df):
    tr = true_range(df)
    atrv = tr.rolling(ADX_PERIOD, ADX_PERIOD).mean()
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = up.where((up > dn) & (up > 0), 0)
    minus = dn.where((dn > up) & (dn > 0), 0)
    plus_di = 100 * plus.rolling(ADX_PERIOD).sum() / atrv
    minus_di = 100 * minus.rolling(ADX_PERIOD).sum() / atrv
    dx = (100 * (plus_di - minus_di).abs() /
          (plus_di + minus_di).replace(0, np.nan))
    return dx.rolling(ADX_PERIOD).mean().fillna(0)

# =====================================================
# SINAL
# =====================================================

def triple_sma_cross(df):
    s3, s8, s20 = sma(df["close"],3), sma(df["close"],8), sma(df["close"],20)
    if len(df) < 3: return None
    if s3.iloc[-1] > s8.iloc[-1] > s20.iloc[-1] and not (s3.iloc[-2] > s8.iloc[-2] > s20.iloc[-2]):
        return "LONG"
    if s3.iloc[-1] < s8.iloc[-1] < s20.iloc[-1] and not (s3.iloc[-2] < s8.iloc[-2] < s20.iloc[-2]):
        return "SHORT"
    return None

def analyze_symbol(symbol):
    df = fetch_klines(symbol)
    _, _, bb_width = bollinger_bands(df["close"])
    last_width = bb_width.iloc[-1]
    baseline = bb_width.rolling(20,1).mean().iloc[-1]
    if last_width < max(baseline, BOLLINGER_WIDTH_MIN_PCT): return None

    adx_series = adx(df)
    if adx_series.iloc[-1] < ADX_MIN: return None

    side = triple_sma_cross(df)
    if not side: return None

    price = df["close"].iloc[-1]
    atrv = atr(df).iloc[-1]
    tps = (
        [price + m*atrv for m in (0.5,1,2)]
        if side == "LONG"
        else [price - m*atrv for m in (0.5,1,2)]
    )

    return {
        "symbol": symbol,
        "side": side,
        "price": price,
        "adx": float(adx_series.iloc[-1]),
        "atr": float(atrv),
        "bb_width": float(last_width),
        "tps": tps
    }

# =====================================================
# LOG DIÁRIO (LOCAL)
# =====================================================

def log_signal(res):
    fn = f"signals_{today_str()}.tsv"
    header = not os.path.isfile(fn)
    with open(fn, "a", encoding="utf-8") as f:
        if header:
            f.write("symbol\tside\tprice\tadx\tatr\ttp1\ttp2\ttp3\n")
        f.write(
            f"{res['symbol']}\t{res['side']}\t{res['price']:.8f}\t"
            f"{res['adx']:.2f}\t{res['atr']:.8f}\t"
            f"{res['tps'][0]:.8f}\t{res['tps'][1]:.8f}\t{res['tps'][2]:.8f}\n"
        )

# =====================================================
# LOOP PRINCIPAL
# =====================================================

SHUTDOWN = False

def shutdown_handler(sig, frame):
    global SHUTDOWN
    SHUTDOWN = True
    send_telegram(f"🛑 Scanner encerrado em {now_sp_str()}")

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

def main():
    send_telegram(f"🤖 Scanner iniciado em {now_sp_str()}")
    LOGGER.info("Scanner iniciado")

    while not SHUTDOWN:
        for sym in FIXED_SYMBOLS:
            try:
                res = analyze_symbol(sym)
                if res:
                    msg = (
                        f"🚨 <b>SINAL 15m</b>\n"
                        f"Par: <b>{res['symbol']}</b>\n"
                        f"Lado: <b>{res['side']}</b>\n"
                        f"Preço: {res['price']:.8f}\n"
                        f"ADX: {res['adx']:.2f}\n"
                        f"ATR: {res['atr']:.8f}\n"
                        f"TPs:\n"
                        f"{res['tps'][0]:.8f}\n{res['tps'][1]:.8f}\n{res['tps'][2]:.8f}"
                    )
                    log_signal(res)
                    send_telegram(msg)
            except Exception:
                LOGGER.exception("Erro analisando %s", sym)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
