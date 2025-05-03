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
    # Adicione este log
    logger.info("Webhook ativado, dados recebidos!")
    logger.info("Conteúdo recebido: " + str(request.json))  # Log detalhado
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
