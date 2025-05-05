#!/usr/bin/env python
# -*- coding: utf-8 -*-

from flask import Flask, request
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import os, json, uuid, logging, traceback
import numpy as np
import telegram
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ========== CONFIG ==========
app = Flask(__name__)
app.secret_key = os.environ.get('APP_SECRET_KEY', 'sua_chave_secreta_aqui')

STATIC_DIR = "static"
BASE_URL = os.environ.get("BASE_URL", "https://assistente-financeiro.onrender.com")
os.makedirs(STATIC_DIR, exist_ok=True)

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)

# ========== GOOGLE SHEETS ==========
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
except Exception as e:
    logger.critical(f"Erro ao conectar com a planilha: {e}")
    raise

# ========== TELEGRAM ==========
telegram_token = os.environ.get("TELEGRAM_TOKEN")
if not telegram_token:
    raise Exception("A variável de ambiente TELEGRAM_TOKEN não está definida!")
bot = telegram.Bot(token=telegram_token)

contatos = [
    {"nome": "Larissa", "chat_id": int(os.environ.get("LARISSA_CHAT_ID", 0))},
    {"nome": "Thiago",  "chat_id": int(os.environ.get("THIAGO_CHAT_ID", 0))}
]

# ========== AGENDAMENTO ==========
timezone_brasilia = timezone("America/Sao_Paulo")
scheduler = BackgroundScheduler(timezone=timezone_brasilia)

def enviar_lembrete():
    for contato in contatos:
        nome = contato["nome"]
        chat_id = contato["chat_id"]
        mensagem = f"🔔 Oi {nome}! Já cadastrou suas despesas de hoje? 💰"
        try:
            if chat_id != 0:
                bot.send_message(chat_id=chat_id, text=mensagem)
                logger.info(f"Lembrete enviado para {nome} ({chat_id})")
            else:
                logger.warning(f"Chat ID do {nome} não configurado!")
        except Exception as e:
            logger.error(f"Erro ao enviar lembrete para {nome}: {e}")
            logger.error(traceback.format_exc())

scheduler.add_job(enviar_lembrete, 'cron', hour=20, minute=0)
scheduler.start()

# ========== FUNÇÕES AUXILIARES ==========
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

palavras_categoria = {
    "ALIMENTAÇÃO": ["mercado", "supermercado", "pão", "leite", "feira", "comida"],
    "TRANSPORTE": ["uber", "99", "ônibus", "metro", "trem", "corrida", "combustível", "gasolina"],
    "LAZER": ["cinema", "netflix", "bar", "show", "festa", "lazer"],
    "MORADIA": ["aluguel", "condominio", "energia", "água", "internet", "luz"],
    "SAÚDE": ["farmácia", "higiene", "produto de limpeza", "remédio"]
}

def classificar_categoria(descricao):
    desc = descricao.lower()
    for categoria, palavras in palavras_categoria.items():
        if any(p in desc for p in palavras):
            return categoria
    return "OUTROS"

def gerar_grafico(tipo, titulo, dados, categorias=None):
    plt.figure(figsize=(10, 6))
    plt.title(titulo)
    plt.rcParams.update({'font.size': 14})
    if tipo == 'barra':
        plt.bar(categorias, dados)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
    elif tipo == 'pizza':
        if categorias and len(categorias) > 6:
            top_indices = np.argsort(dados)[-5:]
            top_categorias = [categorias[i] for i in top_indices]
            top_dados = [dados[i] for i in top_indices]
            outros_valor = sum(d for i, d in enumerate(dados) if i not in top_indices)
            top_categorias.append('Outros')
            top_dados.append(outros_valor)
            categorias = top_categorias
            dados = top_dados
        plt.pie(dados, labels=categorias, autopct='%1.1f%%', startangle=90, shadow=True)
        plt.axis('equal')
    elif tipo == 'linha':
        plt.plot(categorias, dados, marker='o', linestyle='-')
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
    nome_arquivo = f"grafico_{uuid.uuid4().hex}.png"
    caminho_arquivo = os.path.join(STATIC_DIR, nome_arquivo)
    plt.savefig(caminho_arquivo, dpi=100, bbox_inches='tight')
    plt.close()
    return caminho_arquivo

# ========== FUNÇÕES DE RESUMO ==========
def gerar_resumo_geral(chat_id):
    try:
        registros = sheet.get_all_records()
        total = 0.0
        categorias = {}
        for r in registros:
            valor = parse_valor(r.get("Valor", "0"))
            total += valor
            cat = r.get("Categoria", "OUTROS")
            categorias[cat] = categorias.get(cat, 0) + valor
        resumo = f"📊 Resumo Geral:\n\nTotal registrado: {formatar_valor(total)}"
        labels = list(categorias.keys())
        valores = list(categorias.values())
        grafico_path = gerar_grafico('pizza', 'Distribuição de Despesas', valores, labels)
        bot.send_message(chat_id=chat_id, text=resumo)
        bot.send_photo(chat_id=chat_id, photo=open(grafico_path, 'rb'))
    except Exception as e:
        logger.error(f"Erro no resumo geral: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id=chat_id, text="❌ Erro no resumo geral.")

def gerar_resumo_hoje(chat_id):
    try:
        hoje = datetime.now().strftime("%d/%m/%Y")
        registros = sheet.get_all_records()
        total = 0.0
        categorias = {}
        for r in registros:
            data_str = r.get("Data da Despesa", "").strip()  # AJUSTADO
            if data_str == hoje:
                v = parse_valor(r.get("Valor", "0"))
                total += v
                cat = r.get("Categoria", "OUTROS")
                categorias[cat] = categorias.get(cat, 0) + v
        resumo = f"📅 Resumo de Hoje ({hoje}):\n\nTotal registrado: {formatar_valor(total)}"
        if categorias:
            labels = list(categorias.keys())
            valores = list(categorias.values())
            grafico_path = gerar_grafico('pizza', f'Despesas de Hoje ({hoje})', valores, labels)
            bot.send_message(chat_id=chat_id, text=resumo)
            bot.send_photo(chat_id=chat_id, photo=open(grafico_path, 'rb'))
        else:
            bot.send_message(chat_id=chat_id, text=resumo + "\n\nNão há despesas registradas para hoje.")
    except Exception as e:
        logger.error(f"Erro no resumo de hoje: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id=chat_id, text="❌ Erro no resumo de hoje.")

def gerar_resumo_mensal(chat_id):
    try:
        registros = sheet.get_all_records()
        hoje = datetime.now()
        dias = {}
        for r in registros:
            data_str = r.get("Data da Despesa", "").strip()
            if not data_str:
                continue
            try:
                data = datetime.strptime(data_str, "%d/%m/%Y")
            except:
                continue
            if data.month == hoje.month and data.year == hoje.year:
                dia = data.day
                v = parse_valor(r.get("Valor", "0"))
                dias[dia] = dias.get(dia, 0) + v
        labels = [f"{dia}/{hoje.month}" for dia in sorted(dias)]
        valores = [dias[dia] for dia in sorted(dias)]
        total = sum(valores)
        resumo = f"📅 Resumo do mês de {hoje.strftime('%B/%Y')}:\n\nTotal: {formatar_valor(total)}\nDias com despesas: {len(dias)}"
        if dias:
            dia_maior = max(dias, key=dias.get)
            resumo += f"\nDia com maior gasto: {dia_maior}/{hoje.month} - {formatar_valor(dias[dia_maior])}"
        grafico_path = gerar_grafico('linha', f"Despesas diárias - {hoje.strftime('%B/%Y')}", valores, labels)  # <--- Corrigido aqui
        bot.send_message(chat_id=chat_id, text=resumo)
        bot.send_photo(chat_id=chat_id, photo=open(grafico_path, 'rb'))
    except Exception as e:
        logger.error(f"Erro no resumo mensal: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id=chat_id, text="❌ Erro no resumo mensal.")

def gerar_resumo_categoria(chat_id):
    try:
        registros = sheet.get_all_records()
        categorias = {}
        total = 0.0
        for r in registros:
            v = parse_valor(r.get("Valor", "0"))
            cat = r.get("Categoria", "OUTROS")
            categorias[cat] = categorias.get(cat, 0) + v
            total += v
        resumo = "📂 Resumo por Categoria:\n\n"
        for cat, val in sorted(categorias.items(), key=lambda x: x[1], reverse=True):
            percentual = (val / total) * 100 if total > 0 else 0
            resumo += f"{cat}: {formatar_valor(val)} ({percentual:.1f}%)\n"
        resumo += f"\nTotal Geral: {formatar_valor(total)}"
        labels = list(categorias.keys())
        valores = list(categorias.values())
        grafico_path = gerar_grafico('pizza', 'Despesas por Categoria', valores, labels)
        bot.send_message(chat_id=chat_id, text=resumo)
        bot.send_photo(chat_id=chat_id, photo=open(grafico_path, 'rb'))
    except Exception as e:
        logger.error(f"Erro no resumo por categoria: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id=chat_id, text="❌ Erro no resumo por categoria.")

def gerar_resumo(chat_id, responsavel, dias, titulo):
    try:
        registros = sheet.get_all_records()
        limite = datetime.now() - timedelta(days=dias)
        total = 0.0
        categorias = {}
        registros_cont = 0
        for r in registros:
            data_str = r.get("Data", "").strip()  # AJUSTADO
            if not data_str:
                continue
            try:
                try:
                    data = datetime.strptime(data_str, "%d/%m/%Y")
                except ValueError:
                    data = datetime.strptime(data_str, "%Y-%m-%d")
            except Exception as err:
                logger.warning(f"Data inválida: {data_str} | Erro: {err}")
                continue
            resp = r.get("Responsável", "").upper()
            if data >= limite and (responsavel.upper() == "TODOS" or resp == responsavel.upper()):
                v = parse_valor(r.get("Valor", "0"))
                total += v
                cat = r.get("Categoria", "OUTROS")
                categorias[cat] = categorias.get(cat, 0) + v
                registros_cont += 1
        resumo = f"📋 {titulo} ({responsavel.title()}):\n\nTotal: {formatar_valor(total)}\nRegistros: {registros_cont}"
        if categorias:
            labels = list(categorias.keys())
            valores = list(categorias.values())
            grafico_path = gerar_grafico('pizza', f'{titulo} - {responsavel.title()}', valores, labels)
            bot.send_message(chat_id=chat_id, text=resumo)
            bot.send_photo(chat_id=chat_id, photo=open(grafico_path, 'rb'))
        else:
            bot.send_message(chat_id=chat_id, text=resumo)
    except Exception as e:
        logger.error(f"Erro ao gerar {titulo}: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id=chat_id, text=f"❌ Erro ao gerar {titulo.lower()}.")

# ========== ROTA TELEGRAM ==========
@app.route(f"/{telegram_token}", methods=["POST"])
def receber_telegram():
    try:
        data = request.json
        logger.info("POST do Telegram recebido: " + str(data))
        if "message" not in data:
            return "ok"
        mensagem = data["message"]
        chat_id = mensagem["chat"]["id"]
        texto = mensagem.get("text", "")
        texto_lower = texto.lower()

        # Comando de ajuda
        if "ajuda" in texto_lower:
            ajuda_msg = (
                "🤖 *Assistente Financeiro - Comandos disponíveis:*\n\n"
                "📌 Registrar despesa:\n"
                "_Formato:_ <Responsável>, <Descrição>, <Valor>\n"
                "_Exemplo:_ Larissa, supermercado, 37,90\n\n"
                "📊 *Ver resumos:*\n"
                "- resumo geral\n- resumo hoje\n- resumo do mês\n- resumo da semana\n- resumo por categoria\n- resumo da Larissa\n- resumo do Thiago\n"
            )
            bot.send_message(chat_id=chat_id, text=ajuda_msg, parse_mode="Markdown")
            return "ok"

        # Comandos de resumo
        if "resumo geral" in texto_lower:
            gerar_resumo_geral(chat_id)
        elif "resumo hoje" in texto_lower:
            gerar_resumo_hoje(chat_id)
        elif "resumo por categoria" in texto_lower:
            gerar_resumo_categoria(chat_id)
        elif "resumo do mês" in texto_lower:
            gerar_resumo_mensal(chat_id)
        elif "resumo da semana" in texto_lower:
            gerar_resumo(chat_id, "TODOS", 7, "Resumo da Semana")
        elif "resumo da larissa" in texto_lower:
            gerar_resumo(chat_id, "LARISSA", 30, "Resumo do Mês")
        elif "resumo do thiago" in texto_lower:
            gerar_resumo(chat_id, "THIAGO", 30, "Resumo do Mês")
        # Cadastro da despesa
        elif "," in texto:
            partes = [p.strip() for p in texto.split(",")]
            if len(partes) != 3:
                bot.send_message(chat_id=chat_id, text="❌ Formato inválido. Envie: Responsável, Descrição, Valor\nExemplo: Larissa, supermercado, 37,90")
                return "ok"
            responsavel, descricao, valor = partes
            data_formatada = datetime.now(timezone_brasilia).strftime("%d/%m/%Y")
            categoria = classificar_categoria(descricao)
            valor_float = parse_valor(valor)
            valor_formatado = formatar_valor(valor_float)
            try:
                sheet.append_row([data_formatada, categoria, descricao.upper(), responsavel.upper(), valor_formatado])
                resposta = (
                    f"✅ Despesa registrada!\n"
                    f"📅 Data: {data_formatada}\n"
                    f"📂 Categoria: {categoria}\n"
                    f"📝 Descrição: {descricao.upper()}\n"
                    f"👤 Responsável: {responsavel.upper()}\n"
                    f"💰 Valor: {valor_formatado}"
                )
                bot.send_message(chat_id=chat_id, text=resposta)
            except Exception as e:
                logger.error(f"Erro ao registrar despesa: {e}")
                logger.error(traceback.format_exc())
                bot.send_message(chat_id=chat_id, text="❌ Erro ao registrar a despesa na planilha!")
        else:
            bot.send_message(chat_id=chat_id, text="Comando não reconhecido. Envie 'ajuda' para ver os comandos disponíveis.")
    except Exception as e:
        logger.error(f"Erro ao processar mensagem: {e}")
        logger.error(traceback.format_exc())
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
