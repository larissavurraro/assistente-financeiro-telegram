"""
Bot Assistente Financeiro - Exemplo de webhook Flask e Telegram
Esse bot responde cada mensagem recebida, útil para testar integração do Telegram-Bot via webhook.
"""

from flask import Flask, request
import telegram
import os
import logging

# Configurando log
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -- Variáveis e objetos principais --
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise Exception("A variável de ambiente TELEGRAM_TOKEN não está definida!")

bot = telegram.Bot(token=TELEGRAM_TOKEN)

# -- Flask App --
app = Flask(__name__)

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.json
    logger.info("Webhook ativado, dados recebidos!")
    logger.info("Conteúdo recebido: %s", str(data))

    # Verifica se veio uma mensagem válida
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        texto_recebido = data["message"].get("text", "(sem texto)")

        logger.info(f"Chat ID: {chat_id}, Texto recebido: {texto_recebido}")

        try:
            resposta = "Mensagem recebida com sucesso no servidor! 💬"
            bot.send_message(chat_id=chat_id, text=resposta)
            logger.info(f"Mensagem enviada para o chat ID {chat_id}: {resposta}")
        except telegram.error.TelegramError as e:
            logger.error(f"Erro no envio para o Telegram: {e}")
    else:
        logger.warning("POST recebido sem campo 'message'.")

    return "ok"

@app.route("/", methods=["GET"])
def home():
    return "Assistente Financeiro ONLINE e aguardando mensagens do Telegram! 🚀", 200

# -- Executa localmente --
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render define PORT, padrão é 5000
    app.run(host="0.0.0.0", port=port)
