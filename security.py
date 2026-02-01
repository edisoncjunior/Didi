# -*- coding: utf-8 -*-
"""
security.py
Responsável por:
- Carregar variáveis de ambiente (local = .env ou Railway = variáveis de serviço)
- Validar credenciais do Telegram
- Enviar mensagens com tratamento de erro
"""

import os
import requests

# ======================================================
# CARREGAMENTO OPCIONAL DO .env (APENAS LOCAL)
# ======================================================
try:
    from dotenv import load_dotenv

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ENV_PATH = os.path.join(BASE_DIR, ".env")

    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        print("[ENV] .env carregado com sucesso (local)")
    else:
        print("[ENV] .env não encontrado (provável Railway)")

except Exception as e:
    print("[ENV] dotenv não disponível:", e)


# ======================================================
# OBTENÇÃO DAS VARIÁVEIS
# ======================================================
def _get_env():
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[AVISO] Telegram desabilitado (env não disponível ainda)")
        return None, None

    return token, chat_id


# ======================================================
# TELEGRAM
# ======================================================
def send_telegram(msg: str):
    print("[DEBUG] Preparando envio Telegram")

    token, chat_id = _get_env()

    if not token or not chat_id:
        print("[AVISO TELEGRAM] Variáveis ausentes no runtime")
        print(f"TELEGRAM_TOKEN={token}")
        print(f"TELEGRAM_CHAT_ID={chat_id}")
        return  # NÃO quebra o app

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg
        }

        r = requests.post(url, json=payload, timeout=10)
        print(f"[TELEGRAM] {r.status_code} | {r.text}")

    except Exception as e:
        print("[ERRO TELEGRAM EXCEPTION]", e)
