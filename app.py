#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import uuid
import requests
import logging
import subprocess
import traceback
from datetime import datetime, timedelta

import gspread
# Use google-auth em vez de oauth2client
from google.oauth2.service_account import Credentials
from flask import Flask, request
from pydub import AudioSegment
from gtts import gTTS
import whisper
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg') # Use Agg backend para evitar problemas de GUI em servidores
import numpy as np
import telegram
from apscheduler.schedulers.background import BackgroundScheduler

# ========== CONFIGURAÇÃO ==========
app = Flask(__name__)
# É recomendado usar uma variável de ambiente para a secret key
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "uma-chave-secreta-padrao-forte")

STATIC_DIR = "static"
# Tenta obter BASE_URL do ambiente, útil para webhooks se necessário
BASE_URL = os.environ.get("BASE_URL")
os.makedirs(STATIC_DIR, exist_ok=True)

# Configuração de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(), # Log para o console
        # logging.FileHandler("bot.log") # Descomente para logar em arquivo
    ]
)
logger = logging.getLogger(__name__)

# ========== GOOGLE SHEETS ==========
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
json_creds_str = os.environ.get("GOOGLE_CREDS_JSON")
if not json_creds_str:
    logger.critical("Variável de ambiente GOOGLE_CREDS_JSON não definida!")
    raise ValueError("Credenciais do Google Sheets não configuradas.")

try:
    creds_dict = json.loads(json_creds_str)
    # Autenticação usando google.oauth2.service_account
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    gc = gspread.authorize(creds)
    logger.info("Autenticação com Google API bem-sucedida.")
except json.JSONDecodeError:
    logger.critical("Conteúdo de GOOGLE_CREDS_JSON não é um JSON válido.")
    raise
except Exception as e:
    logger.critical(f"Erro ao processar credenciais do Google ou autorizar gspread: {e}")
    logger.critical(traceback.format_exc())
    raise

SHEET_ID = "1vKrmgkMTDwcx5qufF-YRvsXSk99J1Vq9-LwuQINwcl8" # Mantenha ou use variável de ambiente
SHEET_NAME = "Página1" # Ou o nome correto da sua aba, considere usar variável de ambiente

try:
    spreadsheet = gc.open_by_key(SHEET_ID)
    # Tenta acessar a aba pelo nome. Se não existir, pode tentar sheet1 ou index 0
    try:
        sheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        logger.warning(f"Aba '{SHEET_NAME}' não encontrada. Tentando 'Sheet1'.")
        try:
            sheet = spreadsheet.worksheet("Sheet1") # Nome comum padrão
        except gspread.WorksheetNotFound:
            logger.warning("Aba 'Sheet1' não encontrada. Tentando primeira aba (index 0).")
            sheet = spreadsheet.get_worksheet(0) # Pega a primeira aba

    logger.info(f"Acesso à planilha '{spreadsheet.title}' e aba '{sheet.title}' bem-sucedido.")

    # Teste de escrita (opcional, mas útil)
    try:
        # Certifique-se que as colunas coincidem com sua planilha
        # Exemplo: Data, Categoria, Descrição, Responsável, Valor
        test_row = [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), 'TESTE', 'CONEXÃO BOT', 'BOT', 'R$0,00']
        sheet.append_row(test_row)
        # Você pode querer deletar a linha de teste depois
        logger.info(f"Teste de escrita na aba '{sheet.title}' OK.")
    except Exception as e:
        logger.error(f"Erro no teste de escrita ao Google Sheets na aba '{sheet.title}': {e}")
        logger.error(traceback.format_exc())
        # Não lançar exceção aqui necessariamente, o bot pode funcionar parcialmente

except gspread.exceptions.APIError as e:
     logger.critical(f"Erro na API do Google Sheets ao abrir planilha ID {SHEET_ID}: {e}")
     logger.critical(traceback.format_exc())
     raise
except Exception as e:
    logger.critical(f"Erro inesperado na conexão com a planilha ID {SHEET_ID}: {e}")
    logger.critical(traceback.format_exc())
    raise

# ========== TELEGRAM ==========
telegram_token = os.environ.get("TELEGRAM_TOKEN")
if not telegram_token:
    logger.critical("Variável de ambiente TELEGRAM_TOKEN não definida!")
    raise ValueError("Token do Telegram não configurado.")

try:
    bot = telegram.Bot(token=telegram_token)
    bot_info = bot.get_me()
    logger.info(f"Conectado ao Telegram como: {bot_info.username} (ID: {bot_info.id})")
except Exception as e:
    logger.critical(f"Erro ao inicializar o bot do Telegram: {e}")
    logger.critical(traceback.format_exc())
    raise

# IDs dos Chats - Obtenha via @userinfobot ou similar no Telegram
# É crucial que esses IDs estejam corretos.
LARISSA_CHAT_ID = os.environ.get("LARISSA_CHAT_ID")
THIAGO_CHAT_ID = os.environ.get("THIAGO_CHAT_ID")

contatos = []
if LARISSA_CHAT_ID:
    try:
        contatos.append({"nome": "Larissa", "chat_id": int(LARISSA_CHAT_ID)})
    except ValueError:
        logger.error("LARISSA_CHAT_ID não é um número inteiro válido.")
else:
    logger.warning("LARISSA_CHAT_ID não definido no ambiente.")

if THIAGO_CHAT_ID:
    try:
        contatos.append({"nome": "Thiago", "chat_id": int(THIAGO_CHAT_ID)})
    except ValueError:
        logger.error("THIAGO_CHAT_ID não é um número inteiro válido.")
else:
    logger.warning("THIAGO_CHAT_ID não definido no ambiente.")

if not contatos:
    logger.warning("Nenhum CHAT_ID válido foi configurado. Lembretes e talvez outras funções não funcionarão.")


# ========== AGENDAMENTO ==========
def enviar_lembrete():
    """Envia mensagem de lembrete para os contatos configurados."""
    if not contatos:
        logger.info("Agendador: Nenhum contato configurado para enviar lembrete.")
        return

    logger.info("Agendador: Executando envio de lembretes.")
    for contato in contatos:
        nome = contato["nome"]
        chat_id = contato["chat_id"]
        mensagem = f"🔔 Oi {nome}! Já cadastrou suas despesas de hoje? 💰\n\nUse o comando /ajuda para ver como registrar."
        try:
            bot.send_message(chat_id=chat_id, text=mensagem)
            logger.info(f"Lembrete enviado para {nome} (Chat ID: {chat_id})")
        except telegram.error.BadRequest:
             logger.error(f"Erro ao enviar lembrete para {nome} (Chat ID: {chat_id}): Chat não encontrado ou bot bloqueado?")
        except Exception as e:
            logger.error(f"Erro inesperado ao enviar lembrete para {nome}: {e}")
            logger.error(traceback.format_exc())

# Configura o scheduler para rodar em background
scheduler = BackgroundScheduler(daemon=True) # daemon=True permite sair da app principal
# Executa todo dia às 20:00
scheduler.add_job(enviar_lembrete, 'cron', hour=20, minute=0)
scheduler.start()
logger.info("Agendador de lembretes iniciado para rodar às 20:00.")

# ========== FUNÇÕES AUXILIARES ==========
def parse_valor(valor_str):
    """Converte string de valor (possivelmente com R$, . ou ,) para float."""
    try:
        # Remove R$, espaços, troca . por nada (milhar), e , por . (decimal)
        valor_limpo = str(valor_str).replace("R$", "").strip().replace(".", "").replace(",", ".")
        return float(valor_limpo)
    except (ValueError, TypeError):
        logger.warning(f"Não foi possível converter '{valor_str}' para float. Retornando 0.0")
        return 0.0

def formatar_valor(valor):
    """Formata um valor float para o padrão R$ X.XXX,XX."""
    try:
        return f"R${valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        logger.warning(f"Não foi possível formatar o valor '{valor}'. Retornando 'R$ 0,00'")
        return "R$ 0,00"

# Mapeamento de palavras-chave para categorias (flexível)
palavras_categoria = {
    # Chave: Nome da Categoria (será usado em maiúsculas)
    # Valor: Lista de palavras-chave (em minúsculas)
    "ALIMENTAÇÃO": ["mercado", "supermercado", "pão", "leite", "feira", "comida", "restaurante", "lanche", "ifood", "rappi", "padaria"],
    "TRANSPORTE": ["uber", "99", "ônibus", "metro", "trem", "corrida", "combustível", "gasolina", "estacionamento", "pedagio", "passagem"],
    "LAZER": ["cinema", "netflix", "bar", "show", "festa", "lazer", "streaming", "jogo", "viagem", "passeio"],
    "MORADIA": ["aluguel", "condominio", "energia", "água", "internet", "luz", "gás", "iptu"],
    "SAÚDE": ["farmácia", "higiene", "produto de limpeza", "remédio", "médico", "consulta", "plano"],
    "VESTUÁRIO": ["roupa", "calçado", "sapato", "acessorio"],
    "EDUCAÇÃO": ["curso", "livro", "material escolar", "faculdade"],
    "PETS": ["petshop", "ração", "veterinário"],
}

def classificar_categoria(descricao):
    """Classifica a descrição em uma categoria baseada em palavras-chave."""
    if not isinstance(descricao, str):
        return "OUTROS"
    desc = descricao.lower()
    for categoria, palavras in palavras_categoria.items():
        if any(palavra in desc for palavra in palavras):
            return categoria # Retorna o nome da categoria como definido nas chaves
    return "OUTROS" # Categoria padrão se nenhuma palavra-chave for encontrada

def gerar_audio_confirmacao(texto):
    """Gera um arquivo de áudio MP3 a partir do texto usando gTTS."""
    try:
        audio_id = uuid.uuid4().hex
        mp3_path = os.path.join(STATIC_DIR, f"confirmacao_{audio_id}.mp3")
        tts = gTTS(text=texto, lang='pt-br') # Usar pt-br para melhor pronúncia
        tts.save(mp3_path)
        logger.info(f"Áudio de confirmação gerado: {mp3_path}")
        return mp3_path
    except Exception as e:
        logger.error(f"Erro ao gerar áudio de confirmação com gTTS: {e}")
        logger.error(traceback.format_exc())
        return None

def convert_to_wav_ffmpeg(input_path, output_path):
    """Converte áudio para WAV 16kHz mono usando ffmpeg."""
    try:
        # -y: sobrescrever arquivo de saída se existir
        # -i: arquivo de entrada
        # -ar 16000: sample rate 16kHz (comum para ASR)
        # -ac 1: mono channel
        # -hide_banner -loglevel error: para reduzir output do ffmpeg
        result = subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1",
             "-hide_banner", "-loglevel", "error", # Menos verbosidade
            output_path
        ], capture_output=True, text=True, check=True) # check=True lança exceção se ffmpeg falhar
        logger.info(f"FFmpeg converteu {input_path} para {output_path} com sucesso.")
        return True
    except FileNotFoundError:
        logger.error("Comando 'ffmpeg' não encontrado. Certifique-se que está instalado e no PATH.")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro na execução do ffmpeg: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Falha inesperada ao executar ffmpeg: {e}")
        logger.error(traceback.format_exc())
        return False

def convert_to_wav_pydub(input_path, output_path):
    """Converte áudio para WAV usando pydub como fallback."""
    try:
        audio = AudioSegment.from_file(input_path)
        # Exporta como WAV, 16kHz, mono
        audio.set_frame_rate(16000).set_channels(1).export(output_path, format="wav")
        logger.info(f"Pydub converteu {input_path} para {output_path}.")
        return True
    except Exception as e:
        logger.error(f"Erro ao converter áudio com pydub: {e}")
        logger.error(traceback.format_exc())
        return False

def processar_audio(file_id, chat_id):
    """Baixa, converte e transcreve um arquivo de áudio do Telegram."""
    ogg_path = os.path.join(STATIC_DIR, f"audio_{file_id}.ogg")
    wav_path = ogg_path.replace(".ogg", ".wav")
    texto_transcrito = None

    try:
        logger.info(f"Processando áudio file_id: {file_id}")
        bot.send_chat_action(chat_id=chat_id, action=telegram.constants.ChatAction.TYPING)
        file_info = bot.get_file(file_id)
        file_info.download(ogg_path)
        logger.info(f"Áudio OGG baixado para: {ogg_path}")

        # Tenta converter com ffmpeg primeiro (mais robusto)
        success_conversion = convert_to_wav_ffmpeg(ogg_path, wav_path)
        if not success_conversion:
            logger.warning("Conversão com ffmpeg falhou. Tentando com pydub.")
            success_conversion = convert_to_wav_pydub(ogg_path, wav_path)

        if not success_conversion:
            logger.error("Falha ao converter áudio para WAV com ffmpeg e pydub.")
            bot.send_message(chat_id=chat_id, text="❌ Desculpe, tive um problema ao converter seu áudio.")
            return None

        # Transcrição com Whisper
        logger.info(f"Iniciando transcrição do arquivo WAV: {wav_path}")
        # Escolha o modelo: tiny, base, small, medium, large
        # Modelos maiores são mais precisos, mas exigem mais recursos/tempo
        model_size = "base" # "tiny" é mais rápido, "base" tem bom equilíbrio
        model = whisper.load_model(model_size)
        result = model.transcribe(wav_path, language="pt", fp16=False) # fp16=False pode ser mais estável em CPU
        texto_transcrito = result["text"].strip()
        logger.info(f"Transcrição (modelo {model_size}): '{texto_transcrito}'")

        if not texto_transcrito:
             logger.warning("Whisper retornou uma transcrição vazia.")
             bot.send_message(chat_id=chat_id, text="😕 Não consegui extrair texto do áudio.")

        return texto_transcrito

    except telegram.error.TelegramError as e:
         logger.error(f"Erro do Telegram ao baixar/processar áudio {file_id}: {e}")
         bot.send_message(chat_id=chat_id, text="❌ Erro ao baixar seu arquivo de áudio do Telegram.")
         return None
    except Exception as e:
        logger.error(f"Erro inesperado ao processar áudio {file_id}: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id=chat_id, text="❌ Ocorreu um erro interno ao processar seu áudio.")
        return None
    finally:
        # Limpeza dos arquivos temporários
        for f_path in [ogg_path, wav_path]:
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                    logger.info(f"Arquivo temporário removido: {f_path}")
                except OSError as e:
                    logger.error(f"Erro ao remover arquivo temporário {f_path}: {e}")


def gerar_grafico(tipo, titulo, dados, categorias=None):
    """Gera um gráfico PNG usando Matplotlib e salva em STATIC_DIR."""
    grafico_path = None
    try:
        fig, ax = plt.subplots(figsize=(10, 6)) # Usar fig, ax é a abordagem moderna
        ax.set_title(titulo, fontsize=16)
        plt.rcParams.update({'font.size': 12}) # Ajuste o tamanho da fonte geral

        if not dados: # Se não há dados, não gera gráfico
             logger.warning(f"Não há dados para gerar o gráfico: {titulo}")
             return None

        if tipo == 'barra':
            if not categorias or len(categorias) != len(dados):
                 logger.error("Erro no gráfico de barra: categorias e dados incompatíveis.")
                 return None
            ax.bar(categorias, dados)
            plt.xticks(rotation=45, ha='right') # Rotação para melhor visualização
            ax.yaxis.set_major_formatter('R${x:,.2f}') # Formata eixo Y como moeda
            plt.tight_layout() # Ajusta layout para não cortar labels

        elif tipo == 'pizza':
            if not categorias: categorias = [f'Item {i+1}' for i in range(len(dados))] # Labels genéricos

            # Agrupar categorias pequenas em "Outros" se houver muitas
            if len(categorias) > 7: # Limite arbitrário
                threshold = sum(dados) * 0.03 # Agrupa itens < 3% do total
                dados_filtrados = []
                labels_filtrados = []
                outros_valor = 0.0
                for label, valor in zip(categorias, dados):
                    if valor < threshold:
                        outros_valor += valor
                    else:
                        dados_filtrados.append(valor)
                        labels_filtrados.append(label)
                if outros_valor > 0:
                    dados_filtrados.append(outros_valor)
                    labels_filtrados.append('Outros')
                dados = dados_filtrados
                categorias = labels_filtrados

            # Garante que dados e categorias ainda correspondam
            if not categorias or len(categorias) != len(dados):
                 logger.error("Erro no gráfico de pizza após filtro 'Outros': categorias e dados incompatíveis.")
                 return None

            wedges, texts, autotexts = ax.pie(
                dados, labels=categorias, autopct='%1.1f%%',
                startangle=90, shadow=False, pctdistance=0.85 # pctdistance para dentro
            )
            plt.setp(autotexts, size=10, weight="bold", color="white") # Formata percentuais
            ax.axis('equal') # Assegura que a pizza seja um círculo

        elif tipo == 'linha':
            if not categorias or len(categorias) != len(dados):
                 logger.error("Erro no gráfico de linha: categorias e dados incompatíveis.")
                 return None
            ax.plot(categorias, dados, marker='o', linestyle='-')
            plt.xticks(rotation=45, ha='right')
            ax.yaxis.set_major_formatter('R${x:,.2f}') # Formata eixo Y como moeda
            plt.grid(True, axis='y', linestyle='--', alpha=0.7) # Adiciona grade horizontal
            plt.tight_layout()

        else:
             logger.error(f"Tipo de gráfico desconhecido: {tipo}")
             return None

        nome_arquivo = f"grafico_{uuid.uuid4().hex}.png"
        grafico_path = os.path.join(STATIC_DIR, nome_arquivo)
        plt.savefig(grafico_path, dpi=100, bbox_inches='tight')
        logger.info(f"Gráfico gerado com sucesso: {grafico_path}")
        return grafico_path

    except Exception as e:
        logger.error(f"Erro ao gerar gráfico '{titulo}': {e}")
        logger.error(traceback.format_exc())
        return None
    finally:
        plt.close(fig) # Fecha a figura para liberar memória, importante!


# ========== FUNÇÕES DE RESUMO ==========

def fetch_records():
    """Busca todos os registros da planilha, com tratamento de erro."""
    try:
        return sheet.get_all_records()
    except gspread.exceptions.APIError as e:
        logger.error(f"Erro na API do Google ao buscar registros: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar registros da planilha: {e}")
        logger.error(traceback.format_exc())
        return None

def send_summary_to_user(chat_id, text_summary, chart_path=None):
    """Envia o resumo em texto e o gráfico (se houver) para o usuário."""
    try:
        bot.send_message(chat_id=chat_id, text=text_summary)
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, 'rb') as photo_file:
                bot.send_photo(chat_id=chat_id, photo=photo_file)
            # Limpar o arquivo do gráfico após o envio
            try:
                os.remove(chart_path)
                logger.info(f"Arquivo de gráfico removido: {chart_path}")
            except OSError as e:
                logger.error(f"Erro ao remover arquivo de gráfico {chart_path}: {e}")
        elif chart_path:
             logger.warning(f"Caminho do gráfico fornecido, mas arquivo não encontrado: {chart_path}")

    except telegram.error.TelegramError as e:
        logger.error(f"Erro do Telegram ao enviar resumo/gráfico para chat {chat_id}: {e}")
    except Exception as e:
        logger.error(f"Erro inesperado ao enviar resumo/gráfico para chat {chat_id}: {e}")
        logger.error(traceback.format_exc())


def gerar_resumo_geral(chat_id):
    """Gera e envia o resumo geral de despesas."""
    logger.info(f"Gerando resumo geral para chat {chat_id}.")
    registros = fetch_records()
    if registros is None:
        bot.send_message(chat_id=chat_id, text="❌ Desculpe, não consegui buscar os dados da planilha para o resumo geral.")
        return

    total = 0.0
    categorias = {}
    for r in registros:
        valor = parse_valor(r.get("Valor", "0")) # Usar get com default
        total += valor
        cat = r.get("Categoria", "OUTROS").upper() # Normalizar categoria
        if not cat: cat = "OUTROS" # Garantir que não seja vazia
        categorias[cat] = categorias.get(cat, 0) + valor

    resumo_txt = f"📊 *Resumo Geral de Despesas*\n\n"
    resumo_txt += f"💰 *Total Geral Gasto:* {formatar_valor(total)}\n\n"
    resumo_txt += " Breakdown por Categoria:\n"

    # Ordena categorias por valor para o texto
    for cat, val in sorted(categorias.items(), key=lambda item: item[1], reverse=True):
         percentual = (val / total) * 100 if total > 0 else 0
         resumo_txt += f"- {cat}: {formatar_valor(val)} ({percentual:.1f}%)\n"

    # Gera o gráfico de pizza
    labels = list(categorias.keys())
    valores = list(categorias.values())
    grafico_path = gerar_grafico('pizza', 'Distribuição Geral de Despesas', valores, labels)

    send_summary_to_user(chat_id, resumo_txt, grafico_path)


def gerar_resumo_periodo(chat_id, dias, titulo, responsavel_filtro=None):
    """Gera resumo para um período (dias) e opcionalmente por responsável."""
    logger.info(f"Gerando {titulo} para chat {chat_id} (Responsável: {responsavel_filtro or 'Todos'}).")
    registros = fetch_records()
    if registros is None:
        bot.send_message(chat_id=chat_id, text=f"❌ Não consegui buscar dados para o {titulo.lower()}.")
        return

    limite_data = datetime.now() - timedelta(days=dias)
    total = 0.0
    categorias = {}
    registros_cont = 0
    registros_filtrados = []

    for r in registros:
        data_str = r.get("Data", "")
        if not data_str: continue

        try:
            # Tenta múltiplos formatos de data
            data_despesa = None
            for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
                try:
                    data_despesa = datetime.strptime(data_str, fmt)
                    break # Sai do loop se um formato funcionar
                except ValueError:
                    continue # Tenta o próximo formato
            if data_despesa is None:
                 logger.warning(f"Formato de data não reconhecido ou inválido: '{data_str}' no registro {r}")
                 continue
        except Exception as e: # Captura outros erros inesperados
            logger.error(f"Erro ao processar data '{data_str}': {e}")
            continue

        # Aplica filtro de data
        if data_despesa < limite_data:
            continue

        # Aplica filtro de responsável (se fornecido)
        resp_registro = str(r.get("Responsável", "")).strip().upper()
        if responsavel_filtro and resp_registro != responsavel_filtro.upper():
            continue

        # Se passou pelos filtros, processa o registro
        valor = parse_valor(r.get("Valor", "0"))
        total += valor
        cat = r.get("Categoria", "OUTROS").upper()
        if not cat: cat = "OUTROS"
        categorias[cat] = categorias.get(cat, 0) + valor
        registros_cont += 1
        registros_filtrados.append(r) # Guarda para possível detalhamento futuro

    resp_title = responsavel_filtro.title() if responsavel_filtro else "Todos"
    resumo_txt = f"📋 *{titulo} ({resp_title})*\n"
    periodo_str = f"Últimos {dias} dias" if dias != 1 else "Hoje"
    if dias == (datetime.now() - datetime.now().replace(day=1)).days + 1 : periodo_str = f"Mês de {datetime.now().strftime('%B')}" # Aproximação para mês atual
    resumo_txt += f"🗓️ Período: {periodo_str}\n"
    resumo_txt += f"📌 Registros encontrados: {registros_cont}\n"
    resumo_txt += f"💰 *Total Gasto:* {formatar_valor(total)}\n\n"

    if categorias:
        resumo_txt += " Breakdown por Categoria:\n"
        for cat, val in sorted(categorias.items(), key=lambda item: item[1], reverse=True):
            percentual = (val / total) * 100 if total > 0 else 0
            resumo_txt += f"- {cat}: {formatar_valor(val)} ({percentual:.1f}%)\n"

        # Gera gráfico de pizza para o período/responsável
        labels = list(categorias.keys())
        valores = list(categorias.values())
        grafico_titulo = f'{titulo} - {resp_title}'
        grafico_path = gerar_grafico('pizza', grafico_titulo, valores, labels)
        send_summary_to_user(chat_id, resumo_txt, grafico_path)
    else:
        resumo_txt += "\nNenhuma despesa encontrada para este período/responsável."
        send_summary_to_user(chat_id, resumo_txt) # Envia só o texto


def gerar_resumo_categoria_detalhado(chat_id):
    """Gera e envia o resumo detalhado por categoria."""
    logger.info(f"Gerando resumo por categoria para chat {chat_id}.")
    registros = fetch_records()
    if registros is None:
        bot.send_message(chat_id=chat_id, text="❌ Desculpe, não consegui buscar os dados da planilha para o resumo por categoria.")
        return

    total_geral = 0.0
    categorias = {}
    for r in registros:
        valor = parse_valor(r.get("Valor", "0"))
        total_geral += valor
        cat = r.get("Categoria", "OUTROS").upper()
        if not cat: cat = "OUTROS"
        categorias[cat] = categorias.get(cat, 0) + valor

    resumo_txt = "📂 *Resumo por Categoria*\n\n"
    if not categorias:
        resumo_txt += "Nenhuma despesa registrada encontrada."
        send_summary_to_user(chat_id, resumo_txt)
        return

    # Ordena categorias por valor
    for cat, val in sorted(categorias.items(), key=lambda item: item[1], reverse=True):
        percentual = (val / total_geral) * 100 if total_geral > 0 else 0
        resumo_txt += f"*{cat}:* {formatar_valor(val)} ({percentual:.1f}%)\n"

    resumo_txt += f"\n💰 *Total Geral:* {formatar_valor(total_geral)}"

    # Gera gráfico de pizza
    labels = list(categorias.keys())
    valores = list(categorias.values())
    grafico_path = gerar_grafico('pizza', 'Despesas por Categoria (Total)', valores, labels)

    send_summary_to_user(chat_id, resumo_txt, grafico_path)


def gerar_resumo_mensal_linha(chat_id):
    """Gera resumo do mês atual com gráfico de linha por dia."""
    logger.info(f"Gerando resumo mensal (linha) para chat {chat_id}.")
    registros = fetch_records()
    if registros is None:
        bot.send_message(chat_id=chat_id, text="❌ Não consegui buscar dados para o resumo mensal.")
        return

    hoje = datetime.now()
    mes_atual = hoje.month
    ano_atual = hoje.year
    gastos_por_dia = {}
    total_mes = 0.0

    for r in registros:
        data_str = r.get("Data", "")
        if not data_str: continue
        try:
             data_despesa = None
             for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
                 try:
                     data_despesa = datetime.strptime(data_str, fmt)
                     break
                 except ValueError: continue
             if data_despesa is None: continue

             # Filtra pelo mês e ano atuais
             if data_despesa.month == mes_atual and data_despesa.year == ano_atual:
                 dia = data_despesa.day
                 valor = parse_valor(r.get("Valor", "0"))
                 gastos_por_dia[dia] = gastos_por_dia.get(dia, 0) + valor
                 total_mes += valor
        except Exception as e:
            logger.warning(f"Erro ao processar data '{data_str}' para resumo mensal: {e}")
            continue

    mes_nome = hoje.strftime('%B')
    resumo_txt = f"📅 *Resumo do Mês de {mes_nome}/{ano_atual}*\n\n"
    resumo_txt += f"💰 *Total Gasto no Mês:* {formatar_valor(total_mes)}\n"

    if gastos_por_dia:
        # Prepara dados para o gráfico de linha (dias ordenados)
        dias_ordenados = sorted(gastos_por_dia.keys())
        valores_dias = [gastos_por_dia[dia] for dia in dias_ordenados]
        labels_dias = [f"{dia:02d}/{mes_atual:02d}" for dia in dias_ordenados] # Formato DD/MM

        dia_maior_gasto = max(gastos_por_dia, key=gastos_por_dia.get)
        valor_maior_gasto = gastos_por_dia[dia_maior_gasto]
        resumo_txt += f"📈 Dia com maior gasto: {dia_maior_gasto:02d}/{mes_atual:02d} ({formatar_valor(valor_maior_gasto)})\n"
        resumo_txt += f"📉 Número de dias com registros: {len(gastos_por_dia)}\n"

        grafico_titulo = f'Gastos Diários - {mes_nome}/{ano_atual}'
        grafico_path = gerar_grafico('linha', grafico_titulo, valores_dias, labels_dias)
        send_summary_to_user(chat_id, resumo_txt, grafico_path)
    else:
        resumo_txt += "\nNenhuma despesa encontrada para este mês."
        send_summary_to_user(chat_id, resumo_txt)


# ========== ROTA PRINCIPAL TELEGRAM (WEBHOOK) ==========
@app.route(f"/{telegram_token}", methods=["POST"])
def webhook_handler():
    """Recebe atualizações do Telegram via Webhook."""
    try:
        update_data = request.get_json(force=True)
        logger.info("Recebido POST do Telegram: %s", json.dumps(update_data, indent=2))

        if "message" not in update_data:
            logger.info("Update sem 'message', ignorando.")
            return "ok", 200

        message = update_data["message"]
        chat_id = message["chat"]["id"]
        user = message.get("from", {})
        user_id = user.get("id")
        user_name = user.get("first_name", f"User_{user_id}")

        texto = message.get("text", "")
        file_id = None
        file_type = None

        # Processa mensagens de voz ou áudio
        if "voice" in message:
            file_id = message["voice"]["file_id"]
            file_type = "voice"
            logger.info(f"Recebido áudio (voice) de {user_name} (Chat: {chat_id})")
        elif "audio" in message:
            file_id = message["audio"]["file_id"]
            file_type = "audio"
            logger.info(f"Recebido áudio (audio) de {user_name} (Chat: {chat_id})")

        if file_id:
            # Processa o áudio e obtém o texto transcrito
            texto_transcrito = processar_audio(file_id, chat_id)
            if texto_transcrito:
                texto = texto_transcrito # Substitui o texto vazio pelo transcrito
                logger.info(f"Áudio processado. Texto para análise: '{texto}'")
            else:
                # processar_audio já envia mensagem de erro
                logger.warning("Processamento de áudio falhou ou retornou vazio.")
                return "ok", 200 # Encerra o processamento para esta mensagem

        # Se não houver texto (nem original nem transcrito), ignora
        if not texto:
            logger.info("Mensagem sem texto e sem áudio válido. Ignorando.")
            # Poderia enviar uma mensagem de ajuda aqui se quisesse
            # bot.send_message(chat_id=chat_id, text="Olá! Envie uma despesa ou 'ajuda'.")
            return "ok", 200

        texto_lower = texto.lower().strip()

        # --- Roteamento de Comandos ---
        if texto_lower == "/start" or texto_lower == "ajuda" or texto_lower == "/ajuda":
             ajuda_msg = (
                "🤖 *Assistente Financeiro Pessoal*\n\n"
                "Olá! Sou seu ajudante para registrar e consultar despesas.\n\n"
                "📌 *Como Registrar uma Despesa:*\n"
                "Envie uma mensagem de texto ou áudio no formato:\n"
                "`Responsável, Data, Descrição, Valor`\n\n"
                "*Exemplos:*\n"
                "`Larissa, hoje, Mercado da semana, 155.70`\n"
                "`Thiago, 25/12, Presente, 80`\n"
                "`Larissa, ontem, Uber, 22,50`\n\n"
                "*Datas aceitas:* `hoje`, `ontem`, `DD/MM` (ano atual), `DD/MM/YYYY`\n\n"
                "📊 *Comandos de Resumo:*\n"
                "- `resumo geral`\n"
                "- `resumo hoje`\n"
                "- `resumo ontem`\n"
                "- `resumo semana`\n"
                "- `resumo mes` (mês atual)\n"
                "- `resumo categoria`\n"
                "- `resumo larissa` (últimos 30 dias)\n"
                "- `resumo thiago` (últimos 30 dias)\n\n"
                "Qualquer dúvida, só chamar! 😉"
            )
             try:
                bot.send_message(chat_id=chat_id, text=ajuda_msg, parse_mode=telegram.constants.ParseMode.MARKDOWN)
             except Exception as e: logger.error(f"Erro ao enviar ajuda: {e}")

        elif texto_lower == "resumo geral":
            gerar_resumo_geral(chat_id)
        elif texto_lower == "resumo hoje":
            gerar_resumo_periodo(chat_id, 1, "Resumo de Hoje")
        elif texto_lower == "resumo ontem":
            gerar_resumo_periodo(chat_id, 2, "Resumo de Ontem") # Inclui ontem e hoje, filtra na func
            # Ou ajustar gerar_resumo_periodo para aceitar data específica
        elif texto_lower == "resumo semana":
            gerar_resumo_periodo(chat_id, 7, "Resumo da Semana")
        elif texto_lower == "resumo mes":
             # Calcula dias desde o início do mês atual
             dias_no_mes = (datetime.now() - datetime.now().replace(day=1)).days + 1
             gerar_resumo_periodo(chat_id, dias_no_mes, f"Resumo do Mês ({datetime.now().strftime('%B')})")
             #gerar_resumo_mensal_linha(chat_id) # Alternativa com gráfico de linha
        elif texto_lower == "resumo categoria":
            gerar_resumo_categoria_detalhado(chat_id)
        elif texto_lower == "resumo larissa":
            # Assumindo que "Larissa" é o nome a ser filtrado na coluna "Responsável"
            gerar_resumo_periodo(chat_id, 30, "Resumo Mensal", responsavel_filtro="LARISSA")
        elif texto_lower == "resumo thiago":
            gerar_resumo_periodo(chat_id, 30, "Resumo Mensal", responsavel_filtro="THIAGO")

        # --- Registro de Despesa ---
        # Verifica se contém vírgula, indicando potencial registro
        elif "," in texto:
            partes = [p.strip() for p in texto.split(",")]

            if len(partes) != 4:
                logger.warning(f"Formato de registro inválido recebido de {user_name}: '{texto}'")
                bot.send_message(
                    chat_id=chat_id,
                    text="❌ Formato inválido. Use: `Responsável, Data, Descrição, Valor`\n"
                         "Ex: `Larissa, hoje, Almoço, 35.50`\nEnvie `ajuda` para mais detalhes.",
                    parse_mode=telegram.constants.ParseMode.MARKDOWN
                )
                return "ok", 200

            responsavel_raw, data_raw, descricao_raw, valor_raw = partes

            # Validações básicas (não podem ser vazios)
            if not responsavel_raw or not data_raw or not descricao_raw or not valor_raw:
                 bot.send_message(chat_id=chat_id, text="❌ Todos os campos (Responsável, Data, Descrição, Valor) são obrigatórios.")
                 return "ok", 200

            # Processamento da Data
            data_formatada = ""
            data_lower = data_raw.lower()
            hoje = datetime.now()
            ontem = hoje - timedelta(days=1)

            if data_lower == "hoje":
                data_formatada = hoje.strftime("%d/%m/%Y")
            elif data_lower == "ontem":
                data_formatada = ontem.strftime("%d/%m/%Y")
            else:
                try:
                    # Tenta formato DD/MM (assume ano atual)
                    dt_obj = datetime.strptime(data_raw, "%d/%m").replace(year=hoje.year)
                    data_formatada = dt_obj.strftime("%d/%m/%Y")
                except ValueError:
                    try:
                        # Tenta formato DD/MM/YYYY
                        dt_obj = datetime.strptime(data_raw, "%d/%m/%Y")
                        data_formatada = dt_obj.strftime("%d/%m/%Y")
                    except ValueError:
                         logger.warning(f"Formato de data não reconhecido: '{data_raw}'")
                         bot.send_message(chat_id=chat_id, text=f"❌ Formato de data inválido: '{data_raw}'. Use 'hoje', 'ontem', 'DD/MM' ou 'DD/MM/YYYY'.")
                         return "ok", 200

            # Processamento dos outros campos
            responsavel = responsavel_raw.strip().upper()
            descricao = descricao_raw.strip().upper()
            categoria = classificar_categoria(descricao_raw) # Classifica antes de upppercase
            valor_float = parse_valor(valor_raw)

            if valor_float <= 0:
                 bot.send_message(chat_id=chat_id, text=f"❌ O valor da despesa ({valor_raw}) parece inválido ou é zero.")
                 return "ok", 200

            valor_formatado = formatar_valor(valor_float) # Formata para exibição e planilha

            # --- Tentativa de Registro no Google Sheets ---
            logger.info(f"Tentando registrar despesa: Data={data_formatada}, Cat={categoria}, Desc={descricao}, Resp={responsavel}, Valor={valor_formatado}")
            try:
                # IMPORTANTE: A ordem aqui DEVE corresponder às colunas na sua planilha
                linha_para_inserir = [
                    data_formatada,
                    categoria,
                    descricao,
                    responsavel,
                    valor_formatado,
                    # Pode adicionar um timestamp de registro se quiser
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ]
                sheet.append_row(linha_para_inserir, value_input_option='USER_ENTERED')
                logger.info(f"Despesa registrada com SUCESSO na planilha para {user_name}.")

                # --- Confirmação para o Usuário (APÓS SUCESSO no Sheets) ---
                resposta_confirmacao = (
                    f"✅ *Despesa Registrada!*\n\n"
                    f"📅 *Data:* {data_formatada}\n"
                    f"📂 *Categoria:* {categoria}\n"
                    f"📝 *Descrição:* {descricao_raw.strip()} \n" # Usar descrição original para clareza
                    f"👤 *Responsável:* {responsavel_raw.strip().title()}\n" # Usar original com TitleCase
                    f"💰 *Valor:* {valor_formatado}"
                )
                try:
                    bot.send_message(chat_id=chat_id, text=resposta_confirmacao, parse_mode=telegram.constants.ParseMode.MARKDOWN)

                    # Tenta gerar e enviar áudio de confirmação
                    audio_path = gerar_audio_confirmacao(
                        f"Despesa registrada: {descricao_raw.strip()}, valor {valor_formatado}, responsável {responsavel_raw.strip()}." # Texto para áudio
                    )
                    if audio_path and os.path.exists(audio_path):
                        try:
                            with open(audio_path, 'rb') as audio_file:
                                bot.send_voice(chat_id=chat_id, voice=audio_file) # Enviar como voice fica melhor
                            # Limpar o arquivo de áudio
                            os.remove(audio_path)
                            logger.info(f"Áudio de confirmação enviado e removido: {audio_path}")
                        except telegram.error.TelegramError as audio_err:
                             logger.error(f"Erro ao enviar áudio de confirmação para chat {chat_id}: {audio_err}")
                        except OSError as e:
                            logger.error(f"Erro ao remover arquivo de áudio {audio_path}: {e}")
                    elif audio_path:
                         logger.warning(f"Arquivo de áudio de confirmação gerado mas não encontrado: {audio_path}")

                except telegram.error.TelegramError as send_err:
                    logger.error(f"Erro ao enviar mensagem/áudio de confirmação para {user_name} (Chat: {chat_id}): {send_err}")
                    # O registro na planilha ocorreu, mas a confirmação falhou. Log é importante.

            except gspread.exceptions.APIError as sheet_api_err:
                logger.error(f"ERRO de API ao tentar registrar despesa na planilha: {sheet_api_err}")
                logger.error(traceback.format_exc())
                bot.send_message(chat_id=chat_id, text="❌ Falha ao registrar na planilha (Erro de API do Google). Tente novamente mais tarde.")
            except Exception as sheet_err:
                logger.error(f"ERRO inesperado ao tentar registrar despesa na planilha: {sheet_err}")
                logger.error(traceback.format_exc())
                bot.send_message(chat_id=chat_id, text="❌ Falha ao registrar na planilha (Erro inesperado). Verifique os logs ou contate o administrador.")

        # --- Comando não reconhecido ---
        else:
            logger.info(f"Comando não reconhecido recebido de {user_name}: '{texto}'")
            bot.send_message(chat_id=chat_id, text="😕 Comando não reconhecido. Envie `ajuda` para ver a lista de comandos disponíveis.", parse_mode=telegram.constants.ParseMode.MARKDOWN)

    except Exception as e:
        # Erro geral no processamento do webhook
        logger.error(f"Erro fatal no processamento do webhook: {e}")
        logger.error(traceback.format_exc())
        # Evitar enviar mensagem de erro genérica para o usuário aqui,
        # pois pode ser um problema interno não relacionado à mensagem dele.
        # Apenas retornar 'ok' para o Telegram não tentar reenviar.

    return "ok", 200 # Sempre retornar OK para o Telegram


# ========== ROTA DE STATUS (Opcional) ==========
@app.route("/")
def index():
    """Rota básica para verificar se o serviço está online."""
    logger.info("Rota '/' acessada.")
    # Verifica conexão com Sheets e Telegram rapidamente
    sheets_ok = False
    telegram_ok = False
    try:
        _ = spreadsheet.title # Tenta acessar um atributo simples
        sheets_ok = True
    except Exception: pass
    try:
        _ = bot.get_me()
        telegram_ok = True
    except Exception: pass

    status_msg = f"<h1>Assistente Financeiro Bot</h1>"
    status_msg += f"<p>Status Flask: Online</p>"
    status_msg += f"<p>Status Google Sheets: {'Conectado' if sheets_ok else 'ERRO'}</p>"
    status_msg += f"<p>Status Telegram Bot: {'Conectado' if telegram_ok else 'ERRO'}</p>"
    status_msg += f"<p>Lembretes agendados: {'Sim' if scheduler.running else 'Não'}</p>"
    return status_msg, 200


if __name__ == "__main__":
    # Define a porta - Render e outros serviços usam a variável PORT
    port = int(os.environ.get("PORT", 5000)) # Default para 5000 se não definida

    # Configuração do Webhook (Opcional, mas recomendado para produção)
    # Se BASE_URL e TELEGRAM_TOKEN estiverem definidos, tenta configurar o webhook
    # if BASE_URL and telegram_token:
    #     webhook_url = f"{BASE_URL}/{telegram_token}"
    #     try:
    #         logger.info(f"Tentando configurar webhook para: {webhook_url}")
    #         set_webhook_ok = bot.set_webhook(url=webhook_url)
    #         if set_webhook_ok:
    #             logger.info("Webhook configurado com sucesso!")
    #         else:
    #             logger.error("Falha ao configurar webhook (API retornou False).")
    #     except Exception as e:
    #         logger.error(f"Erro ao configurar webhook: {e}")
    #         logger.error(traceback.format_exc())
    # else:
    #      logger.warning("BASE_URL não definido. Webhook não será configurado automaticamente. O bot dependerá de polling ou configuração manual.")


    logger.info(f"Iniciando servidor Flask na porta {port}...")
    # Use debug=False em produção!
    # host='0.0.0.0' permite conexões externas (necessário para Render/Docker)
    app.run(host="0.0.0.0", port=port, debug=False)
