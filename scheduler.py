#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import logging
import traceback
import telegram
from apscheduler.schedulers.blocking import BlockingScheduler
from pytz import timezone
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re

# Configuração de logging
logger = logging.getLogger()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger.info("Iniciando scheduler de lembretes...")

# Configuração do Telegram
telegram_token = os.environ.get("TELEGRAM_TOKEN")
if not telegram_token:
    raise Exception("A variável de ambiente TELEGRAM_TOKEN não está definida!")

bot = telegram.Bot(token=telegram_token)

contatos = [
    {"nome": "Larissa", "chat_id": int(os.environ.get("LARISSA_CHAT_ID", 0))},
    {"nome": "Thiago",  "chat_id": int(os.environ.get("THIAGO_CHAT_ID", 0))}
]

# Configuração do Google Sheets (para resumo diário)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
json_creds = os.environ.get("GOOGLE_CREDS_JSON")
if not json_creds:
    raise Exception("A variável de ambiente GOOGLE_CREDS_JSON não está definida!")

creds_dict = json.loads(json_creds)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

SHEET_ID = os.environ.get("SHEET_ID", "1vKrmgkMTDwcx5qufF-YRvsXSk99J1Vq9-LwuQINwcl8")
try:
    spreadsheet = gc.open_by_key(SHEET_ID)
    sheet = spreadsheet.sheet1
    logger.info("Conexão com Google Sheets estabelecida com sucesso")
except Exception as e:
    logger.critical(f"Erro ao conectar com a planilha: {e}")
    raise

# Funções auxiliares
def parse_valor(valor_str):
    valor_str = str(valor_str).replace("R$", "").replace(" ", "").strip()
    valor_str = re.sub(r"[^\d\.,]", "", valor_str)
    if "," in valor_str and "." in valor_str:
        valor_str = valor_str.replace(".", "").replace(",", ".")
    elif "," in valor_str:
        valor_str = valor_str.replace(",", ".")
    try:
        return float(valor_str)
    except:
        return 0.0

def formatar_valor(valor):
    return f"R${valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# Função para enviar o lembrete diário
def enviar_lembrete_diario():
    logger.info("Executando função de lembrete diário")

    # Obter data atual em Brasília
    timezone_brasilia = timezone("America/Sao_Paulo")
    hoje = datetime.now(timezone_brasilia)
    data_formatada = hoje.strftime("%d/%m/%Y")

    # Mensagem personalizada com a data
    for contato in contatos:
        mensagem = (
            f"⏰ Olá, {contato['nome']}!\n\n"
            f"Não se esqueça de registrar suas despesas de hoje ({data_formatada}).\n\n"
            f"Para registrar uma despesa, envie no formato:\n"
            f"{contato['nome']}, descrição, valor\n\n"
            f"Exemplo: {contato['nome']}, supermercado, 50,00"
        )

        try:
            bot.send_message(chat_id=contato["chat_id"], text=mensagem)
            logger.info(f"Lembrete diário enviado para {contato['nome']}")
        except Exception as e:
            logger.error(f"Erro ao enviar lembrete para {contato['nome']}: {e}")
            logger.error(traceback.format_exc())

# Função para enviar resumo diário
def enviar_resumo_diario():
    logger.info("Executando função de resumo diário")

    # Obter data atual em Brasília
    timezone_brasilia = timezone("America/Sao_Paulo")
    hoje = datetime.now(timezone_brasilia)
    data_formatada = hoje.strftime("%d/%m/%Y")

    try:
        registros = sheet.get_all_records()
        total_hoje = 0.0
        categorias = {}

        # Filtrar registros de hoje
        for r in registros:
            data_str = r.get("Data da Despesa", "").strip()
            if data_str == data_formatada:
                v = parse_valor(r.get("Valor", "0"))
                total_hoje += v
                cat = r.get("Categoria", "OUTROS")
                categorias[cat] = categorias.get(cat, 0) + v

        # Preparar mensagem de resumo
        if total_hoje > 0:
            resumo = f"📊 Resumo do dia {data_formatada}:\n\nTotal gasto hoje: {formatar_valor(total_hoje)}\n\n"

            # Adicionar detalhes por categoria
            if categorias:
                resumo += "Detalhamento por categoria:\n"
                for cat, val in sorted(categorias.items(), key=lambda x: x[1], reverse=True):
                    percentual = (val / total_hoje) * 100
                    resumo += f"- {cat}: {formatar_valor(val)} ({percentual:.1f}%)\n"

            # Enviar para todos os contatos
            for contato in contatos:
                try:
                    bot.send_message(chat_id=contato["chat_id"], text=resumo)
                    logger.info(f"Resumo diário enviado para {contato['nome']}")
                except Exception as e:
                    logger.error(f"Erro ao enviar resumo para {contato['nome']}: {e}")
                    logger.error(traceback.format_exc())
        else:
            logger.info("Nenhuma despesa registrada hoje. Resumo não enviado.")

    except Exception as e:
        logger.error(f"Erro ao gerar resumo diário: {e}")
        logger.error(traceback.format_exc())

# Configuração do scheduler
timezone_brasilia = timezone("America/Sao_Paulo")
scheduler = BlockingScheduler(timezone=timezone_brasilia)

# Agendar o lembrete diário para as 20:00
scheduler.add_job(enviar_lembrete_diario, 'cron', hour=20, minute=0,
                 name='lembrete_diario')
logger.info("Agendado: Lembrete diário às 20:00")

# Agendar o resumo diário para as 22:00
scheduler.add_job(enviar_resumo_diario, 'cron', hour=22, minute=0,
                 name='resumo_diario')
logger.info("Agendado: Resumo diário às 22:00")

# Iniciar o scheduler
logger.info("Iniciando scheduler...")
scheduler.start()
