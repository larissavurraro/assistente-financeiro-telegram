from flask import Flask, request
import telegram
import os, logging

app = Flask(__name__)
telegram_token = os.environ.get("TELEGRAM_TOKEN")
bot = telegram.Bot(token=telegram_token)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

@app.route(f"/{telegram_token}", methods=["POST"])
def webhook():
    data = request.json
    logger.info("Webhook ativado, dados recebidos!")
    logger.info("Conteúdo recebido: " + str(data))

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        texto_recebido = data["message"]["text"]

        logger.info(f"Chat ID: {chat_id}, Texto recebido: {texto_recebido}")
        
        try:
            resposta = "Mensagem recebida com sucesso no servidor! 💬"
            bot.send_message(chat_id=chat_id, text=resposta)
            logger.info(f"Mensagem enviada para o chat ID {chat_id}: {resposta}")
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
    
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
