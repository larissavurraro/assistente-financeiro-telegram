
from flask import Flask, request, Response
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import os, json, uuid, requests, logging
from pydub import AudioSegment
from gtts import gTTS
import whisper
import matplotlib.pyplot as plt
import matplotlib
import telegram

matplotlib.use('Agg')
import numpy as np
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

STATIC_DIR = "static"
BASE_URL = os.environ.get("BASE_URL", "https://assistente-financeiro.onrender.com")
os.makedirs(STATIC_DIR, exist_ok=True)

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
json_creds = os.environ.get("GOOGLE_CREDS_JSON")
creds_dict = json.loads(json_creds)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key("1vKrmgkMTDwcx5qufF-YRvsXSk99J1Vq9-LwuQINwcl8")
sheet = spreadsheet.sheet1

telegram_token = os.environ.get("TELEGRAM_TOKEN")
bot = telegram.Bot(token=telegram_token)

contatos = [
    {"nome": "Larissa", "chat_id": int(os.environ.get("LARISSA_CHAT_ID", "0"))},
    {"nome": "Thiago", "chat_id": int(os.environ.get("THIAGO_CHAT_ID", "0"))}
]

def enviar_lembrete():
    for contato in contatos:
        nome = contato["nome"]
        chat_id = contato["chat_id"]
        mensagem = f"🔔 Oi {nome}! Já cadastrou suas despesas de hoje? 💰"
        try:
            bot.send_message(chat_id=chat_id, text=mensagem)
            logger.info(f"Lembrete enviado para {nome} ({chat_id})")
        except Exception as e:
            logger.error(f"Erro ao enviar lembrete para {nome}: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(enviar_lembrete, 'cron', hour=20, minute=0)
scheduler.start()

def gerar_audio(texto):
    audio_id = uuid.uuid4().hex
    mp3_path = os.path.join(STATIC_DIR, f"audio_{audio_id}.mp3")
    tts = gTTS(text=texto, lang='pt')
    tts.save(mp3_path)
    return mp3_path

def processar_audio(file_id):
    file = bot.get_file(file_id)
    ogg_path = os.path.join(STATIC_DIR, f"audio_{file_id}.ogg")
    wav_path = ogg_path.replace(".ogg", ".wav")
    file.download(ogg_path)
    AudioSegment.from_file(ogg_path).export(wav_path, format="wav")
    model = whisper.load_model("tiny")
    result = model.transcribe(wav_path, language="pt")
    texto = result["text"]
    os.remove(ogg_path)
    os.remove(wav_path)
    return texto

def parse_valor(valor_str):
    try:
        return float(str(valor_str).replace("R$", "").replace(".", "").replace(",", ".").strip())
    except:
        return 0.0

def formatar_valor(valor):
    return f"R${valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def classificar_categoria(descricao):
    palavras_categoria = {
        "alimentação": ["mercado", "supermercado", "pão", "leite", "feira", "comida"],
        "transporte": ["uber", "99", "ônibus", "metro", "trem", "corrida", "combustível", "gasolina"],
        "lazer": ["cinema", "netflix", "bar", "show", "festa", "lazer"],
        "moradia": ["aluguel", "condominio", "energia", "água", "internet", "luz"],
        "saúde": ["farmácia", "higiene", "produto de limpeza", "remédio"]
    }
    desc = descricao.lower()
    for categoria, palavras in palavras_categoria.items():
        if any(p in desc for p in palavras):
            return categoria.upper()
    return "OUTROS"

def gerar_grafico(tipo, titulo, dados, categorias=None):
    plt.figure(figsize=(10, 6))
    plt.title(titulo)
    plt.rcParams.update({'font.size': 14})
    if tipo == 'pizza':
        plt.pie(dados, labels=categorias, autopct='%1.1f%%', startangle=90, shadow=True)
        plt.axis('equal')
    elif tipo == 'barra':
        plt.bar(categorias, dados)
        plt.xticks(rotation=45, ha='right')
    nome_arquivo = f"grafico_{uuid.uuid4().hex}.png"
    caminho_arquivo = os.path.join(STATIC_DIR, nome_arquivo)
    plt.savefig(caminho_arquivo)
    plt.close()
    return caminho_arquivo

@app.route(f"/{telegram_token}", methods=["POST"])
def receber_telegram():
    data = request.json
    if "message" not in data:
        return "ok"
    mensagem = data["message"]
    chat_id = mensagem["chat"]["id"]
    texto = mensagem.get("text", "")
    file_id = None
    if "voice" in mensagem:
        file_id = mensagem["voice"]["file_id"]
    elif "audio" in mensagem:
        file_id = mensagem["audio"]["file_id"]

    if file_id:
        texto = processar_audio(file_id)

    if "resumo geral" in texto.lower():
        registros = sheet.get_all_records()
        total = sum(parse_valor(r.get("Valor", "0")) for r in registros)
        categorias = {}
        for r in registros:
            categoria = r.get("Categoria", "OUTROS")
            valor = parse_valor(r.get("Valor", "0"))
            categorias[categoria] = categorias.get(categoria, 0) + valor
        resumo = f"📊 Resumo Geral:\nTotal: {formatar_valor(total)}"
        labels = list(categorias.keys())
        valores = list(categorias.values())
        grafico = gerar_grafico('pizza', 'Resumo Geral', valores, labels)
        bot.send_message(chat_id=chat_id, text=resumo)
        bot.send_photo(chat_id=chat_id, photo=open(grafico, 'rb'))
        audio = gerar_audio(resumo)
        bot.send_audio(chat_id=chat_id, audio=open(audio, 'rb'))
    elif "," in texto:
        partes = [p.strip() for p in texto.split(",")]
        if len(partes) == 5:
            responsavel, data, _, descricao, valor = partes
            if data.lower() == "hoje":
                data_formatada = datetime.today().strftime("%d/%m/%Y")
            else:
                try:
                    data_formatada = datetime.strptime(data, "%d/%m").replace(year=datetime.today().year).strftime("%d/%m/%Y")
                except:
                    data_formatada = datetime.today().strftime("%d/%m/%Y")
            categoria = classificar_categoria(descricao)
            descricao = descricao.upper()
            responsavel = responsavel.upper()
            valor_float = parse_valor(valor)
            valor_formatado = formatar_valor(valor_float)
            sheet.append_row([data_formatada, categoria, descricao, responsavel, valor_formatado])
            resposta = f"✅ Despesa registrada!\n📅 Data: {data_formatada}\n📂 Categoria: {categoria}\n📝 Descrição: {descricao}\n👤 Responsável: {responsavel}\n💰 Valor: {valor_formatado}"
            bot.send_message(chat_id=chat_id, text=resposta)
            audio = gerar_audio(resposta)
            bot.send_audio(chat_id=chat_id, audio=open(audio, 'rb'))
    else:
        ajuda = (
            "🤖 *Assistente Financeiro - Comandos disponíveis:*\n\n"
            "📌 *Registrar despesas:*\n"
            "`Larissa, 28/04, mercado, compras, 150`\n"
            "(formato: responsável, data, local, descrição, valor)\n\n"
            "📊 *Ver resumos:*\n"
            "- resumo geral\n"
            "- resumo da Larissa\n"
            "- resumo do Thiago\n\n"
            "🔉 *Também aceitamos mensagens de áudio!*"
        )
        bot.send_message(chat_id=chat_id, text=ajuda, parse_mode="Markdown")
    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
