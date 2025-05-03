import logging
import os
import datetime
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, Voice
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# --- Configuração ---
# Use variáveis de ambiente para segurança
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "SEU_TOKEN_AQUI")
GOOGLE_SHEETS_CREDENTIALS_PATH = os.environ.get(
    "GOOGLE_SHEETS_CREDENTIALS_PATH", "caminho/para/seu/credentials.json"
)
GOOGLE_SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "SEU_ID_DA_PLANILHA_AQUI")
GOOGLE_SHEETS_NAME = os.environ.get("GOOGLE_SHEETS_NAME", "NomeDaAba")

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("finance_bot.log"), # Salva logs em arquivo
        logging.StreamHandler() # Mostra logs no console
    ]
)
logger = logging.getLogger(__name__)

# Escopos necessários para Google Sheets API
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# --- Funções Auxiliares ---

def authenticate_google_sheets():
    """Autentica com a API do Google Sheets e retorna o objeto da planilha."""
    try:
        creds = Credentials.from_service_account_file(
            GOOGLE_SHEETS_CREDENTIALS_PATH, scopes=SCOPES
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(GOOGLE_SHEETS_ID)
        worksheet = spreadsheet.worksheet(GOOGLE_SHEETS_NAME)
        logger.info("Autenticação com Google Sheets bem-sucedida.")
        return worksheet
    except FileNotFoundError:
        logger.error(f"Arquivo de credenciais não encontrado em: {GOOGLE_SHEETS_CREDENTIALS_PATH}")
        return None
    except gspread.exceptions.APIError as e:
        logger.error(f"Erro na API do Google Sheets durante autenticação/abertura: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado durante autenticação com Google Sheets: {e}", exc_info=True)
        return None

def parse_expense_message(text: str) -> dict | None:
    """
    Analisa a mensagem do usuário para extrair os detalhes da despesa.
    Formato esperado: "Responsável, [Data (DD/MM/YYYY ou 'hoje')], Descrição, Valor"
    Retorna um dicionário com os dados ou None se o formato for inválido.
    """
    parts = [p.strip() for p in text.split(",")]

    if len(parts) < 3 or len(parts) > 4:
        logger.warning(f"Formato de mensagem inválido (partes={len(parts)}): {text}")
        return None

    try:
        responsavel = parts[0]
        valor_str = parts[-1]
        # Tenta converter o valor para float, removendo 'R$' se presente
        valor = float(valor_str.replace("R$", "").replace(",", ".").strip())

        data_str = "hoje"
        descricao = ""

        if len(parts) == 4:
            data_str = parts[1]
            descricao = parts[2]
        elif len(parts) == 3:
            # Verifica se a segunda parte parece uma data ou é 'hoje'
            # Se não for, assume que é a descrição e a data é 'hoje'
            try:
                # Tenta fazer parse como data ou verifica se é 'hoje'
                if data_str.lower() != "hoje":
                    datetime.datetime.strptime(parts[1], "%d/%m/%Y")
                data_str = parts[1] # É uma data válida ou 'hoje'
                descricao = "" # Sem descrição explícita neste formato simplificado? Ajustar se necessário
                # NOTA: Este formato (Responsável, Data, Valor) é ambíguo sem descrição.
                # Recomenda-se exigir 4 partes ou um delimitador mais claro.
                # Por ora, vamos assumir que a parte do meio é a descrição se não for data
                # (Esta lógica pode precisar de ajuste dependendo do uso real)

                # Lógica Revisitada: Se tem 3 partes, a do meio é a descrição e a data é hoje.
                descricao = parts[1]
                data_str = "hoje"

            except ValueError:
                 # Se não for data válida, assume que é a descrição
                descricao = parts[1]
                data_str = "hoje"


        if not responsavel or not descricao or valor <= 0:
             logger.warning(f"Dados inválidos extraídos: R={responsavel}, Desc={descricao}, V={valor}")
             return None # Garante que campos essenciais não estão vazios

        # Processa a data
        if data_str.lower() == "hoje":
            data = datetime.date.today()
        else:
            try:
                data = datetime.datetime.strptime(data_str, "%d/%m/%Y").date()
            except ValueError:
                logger.warning(f"Formato de data inválido: {data_str}")
                return None # Data em formato incorreto

        return {
            "responsavel": responsavel,
            "data": data.strftime("%d/%m/%Y"), # Formata para string
            "descricao": descricao,
            "valor": valor,
        }
    except ValueError:
        logger.warning(f"Erro ao converter valor para número: {valor_str}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao parsear mensagem '{text}': {e}", exc_info=True)
        return None


def classify_category(description: str) -> str:
    """
    Classifica a despesa em uma categoria com base na descrição.
    (Implementação simples - pode ser expandida)
    """
    description_lower = description.lower()
    if "mercado" in description_lower or "supermercado" in description_lower or "hortifruti" in description_lower:
        return "Alimentação (Mercado)"
    if "restaurante" in description_lower or "ifood" in description_lower or "lanche" in description_lower:
        return "Alimentação (Restaurantes)"
    if "uber" in description_lower or "99" in description_lower or "transporte" in description_lower or "gasolina" in description_lower or "combustível" in description_lower:
        return "Transporte"
    if "farmácia" in description_lower or "drogaria" in description_lower or "remédio" in description_lower:
         return "Saúde"
    if "luz" in description_lower or "água" in description_lower or "gás" in description_lower or "internet" in description_lower or "aluguel" in description_lower or "condomínio" in description_lower:
        return "Contas Fixas"
    if "lazer" in description_lower or "cinema" in description_lower or "show" in description_lower or "bar" in description_lower:
        return "Lazer"
    # Adicione mais categorias conforme necessário
    return "Outros" # Categoria padrão


def record_expense_in_sheets(worksheet, expense_data: dict) -> bool:
    """Registra a despesa na planilha Google Sheets."""
    try:
        # Adiciona a categoria ao dicionário antes de gravar
        expense_data["categoria"] = classify_category(expense_data["descricao"])

        # Define a ordem das colunas conforme sua planilha
        # IMPORTANTE: Ajuste esta ordem para corresponder às colunas da sua planilha!
        row_to_insert = [
            expense_data["data"],
            expense_data["responsavel"],
            expense_data["categoria"],
            expense_data["descricao"],
            expense_data["valor"],
            # Adicione mais campos se necessário (ex: timestamp de registro)
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") # Timestamp Registro
        ]
        worksheet.append_row(row_to_insert, value_input_option="USER_ENTERED")
        logger.info(f"Despesa registrada com sucesso no Google Sheets: {expense_data}")
        return True
    except gspread.exceptions.APIError as e:
        logger.error(f"Erro na API do Google Sheets ao tentar registrar despesa: {e} - Dados: {expense_data}")
        return False
    except Exception as e:
        logger.error(f"Erro inesperado ao registrar despesa no Google Sheets: {e} - Dados: {expense_data}", exc_info=True)
        return False


# --- Handlers do Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia uma mensagem de boas-vindas quando o comando /start é emitido."""
    user = update.effective_user
    logger.info(f"Usuário {user.username or user.id} iniciou o bot.")
    await update.message.reply_html(
        f"Olá, {user.mention_html()}!\n\n"
        "Eu sou seu assistente financeiro. Para registrar uma despesa, envie uma mensagem de texto ou áudio no formato:\n\n"
        "<code>Responsável, [Data (DD/MM/YYYY ou 'hoje')], Descrição, Valor</code>\n\n"
        "<b>Exemplos:</b>\n"
        "<code>Maria, hoje, Almoço executivo, 35.50</code>\n"
        "<code>João, 25/12/2024, Presente de Natal, 120</code>\n"
        "<code>Ana, Supermercado da semana, 250.75</code> (Data será 'hoje')\n\n"
        "Enviarei uma confirmação após registrar na planilha!"
    )

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa mensagens de texto para registro de despesas."""
    message_text = update.message.text
    chat_id = update.message.chat_id
    user = update.effective_user
    logger.info(f"Mensagem de texto recebida de {user.username or user.id}: '{message_text}'")

    expense_data = parse_expense_message(message_text)

    if not expense_data:
        logger.warning(f"Falha no parsing da mensagem de {user.username or user.id}: '{message_text}'")
        await context.bot.send_message(
            chat_id=chat_id,
            text="😕 Formato inválido. Use:\n"
                 "<code>Responsável, [Data (DD/MM/YYYY ou 'hoje')], Descrição, Valor</code>\n\n"
                 "<b>Exemplos:</b>\n"
                 "<code>Maria, hoje, Almoço executivo, 35.50</code>\n"
                 "<code>João, 25/12/2024, Presente de Natal, 120</code>\n"
                 "<code>Ana, Supermercado da semana, 250.75</code>",
            parse_mode=ParseMode.HTML
        )
        return

    # Tenta autenticar e obter a planilha a cada tentativa de registro
    # Isso garante que a conexão esteja ativa, mas pode adicionar latência.
    # Alternativa: manter o objeto 'worksheet' global ou em context.bot_data
    # e re-autenticar apenas se ocorrer um erro de API.
    worksheet = authenticate_google_sheets()
    if not worksheet:
        logger.error("Falha ao autenticar/obter planilha do Google Sheets.")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Erro ao conectar com a planilha Google Sheets. Tente novamente mais tarde ou verifique as configurações."
        )
        return

    # Tenta registrar na planilha
    success = record_expense_in_sheets(worksheet, expense_data)

    if success:
        # Registro bem-sucedido, envia confirmação
        logger.info(f"Enviando confirmação para {user.username or user.id} para despesa: {expense_data}")
        confirmation_message = (
            f"✅ <b>Despesa registrada!</b>\n\n"
            f"📅 <b>Data:</b> {expense_data['data']}\n"
            f"📂 <b>Categoria:</b> {expense_data['categoria']}\n" # Categoria adicionada na função de registro
            f"📝 <b>Descrição:</b> {expense_data['descricao']}\n"
            f"👤 <b>Responsável:</b> {expense_data['responsavel']}\n"
            f"💰 <b>Valor:</b> R$ {expense_data['valor']:.2f}".replace('.',',') # Formata para duas casas decimais
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=confirmation_message,
            parse_mode=ParseMode.HTML
        )
        # TODO: Implementar envio de confirmação por áudio (TTS - Text-to-Speech)
        # Exemplo usando uma biblioteca como gTTS ou uma API de nuvem:
        # try:
        #     tts = gTTS(text=f"Despesa registrada: {expense_data['descricao']}, valor {expense_data['valor']:.2f} reais", lang='pt-br')
        #     tts.save("confirmacao.mp3")
        #     await context.bot.send_voice(chat_id=chat_id, voice=open("confirmacao.mp3", "rb"))
        #     os.remove("confirmacao.mp3")
        #     logger.info("Confirmação por áudio enviada.")
        # except Exception as e:
        #     logger.error(f"Erro ao gerar/enviar áudio de confirmação: {e}")
    else:
        # Falha ao registrar na planilha
        logger.error(f"Falha ao registrar despesa no Google Sheets para {user.username or user.id}. Dados: {expense_data}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Erro ao registrar a despesa na planilha Google Sheets. Tente novamente mais tarde."
        )


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa mensagens de voz para registro de despesas."""
    chat_id = update.message.chat_id
    user = update.effective_user
    voice: Voice = update.message.voice
    logger.info(f"Mensagem de voz recebida de {user.username or user.id}. Duração: {voice.duration}s")

    try:
        voice_file = await voice.get_file()
        file_path = f"{voice.file_id}.ogg" # Nome temporário para o arquivo
        await voice_file.download_to_drive(file_path)
        logger.info(f"Áudio baixado para: {file_path}")

        # --- Ponto de Integração para Transcrição de Áudio ---
        transcribed_text = ""
        # TODO: Implementar a lógica de transcrição de áudio aqui.
        #      Use bibliotecas como SpeechRecognition (com engines como Sphinx, Google Cloud Speech, etc.)
        #      ou APIs de serviços de nuvem (AWS Transcribe, Azure Speech to Text).
        # Exemplo conceitual (substitua pela sua implementação real):
        # try:
        #     import speech_recognition as sr
        #     r = sr.Recognizer()
        #     with sr.AudioFile(file_path) as source:
        #         audio_data = r.record(source)
        #         # Substitua 'recognize_google' pela API/engine desejada e configure credenciais/chaves se necessário
        #         transcribed_text = r.recognize_google(audio_data, language='pt-BR')
        #         logger.info(f"Texto transcrito: '{transcribed_text}'")
        # except ImportError:
        #      logger.error("Biblioteca 'SpeechRecognition' não instalada. Transcrição de áudio pulada.")
        #      await context.bot.send_message(chat_id=chat_id, text="⚠️ A função de transcrição de áudio não está configurada neste bot.")
        #      return # Ou defina transcribed_text = "ERRO_TRANSCRICAO" para tratar abaixo
        # except sr.UnknownValueError:
        #     logger.warning("Não foi possível entender o áudio.")
        #     await context.bot.send_message(chat_id=chat_id, text="😕 Não consegui entender o áudio. Tente falar mais claramente.")
        #     return
        # except sr.RequestError as e:
        #     logger.error(f"Erro no serviço de reconhecimento de fala; {e}")
        #     await context.bot.send_message(chat_id=chat_id, text="⚠️ O serviço de reconhecimento de fala está indisponível no momento.")
        #     return
        # except Exception as e:
        #      logger.error(f"Erro inesperado na transcrição: {e}", exc_info=True)
        #      await context.bot.send_message(chat_id=chat_id, text="❌ Ocorreu um erro ao processar seu áudio.")
        #      return


        # --- Simulação de Transcrição (REMOVER EM PRODUÇÃO) ---
        # Esta linha é apenas para teste. Remova-a quando integrar a transcrição real.
        # Assuma que a transcrição ocorreu e coloque o texto aqui para testar o fluxo
        # transcribed_text = "Larissa, hoje, supermercado, 150" # Exemplo
        # logger.warning("Usando texto de simulação para áudio. Implementar transcrição real.")
        # --------------------------------------------------------

        if not transcribed_text:
             logger.warning("Transcrição de áudio falhou ou não foi implementada.")
             # Envie uma mensagem se a transcrição não foi implementada ou falhou
             await context.bot.send_message(chat_id=chat_id, text="⚠️ A transcrição de áudio ainda não foi implementada ou falhou.")
             # Limpa o arquivo baixado
             if os.path.exists(file_path):
                 os.remove(file_path)
             return

        # Processa o texto transcrito da mesma forma que a mensagem de texto
        expense_data = parse_expense_message(transcribed_text)

        if not expense_data:
            logger.warning(f"Falha no parsing do texto transcrito de {user.username or user.id}: '{transcribed_text}'")
            await context.bot.send_message(
                chat_id=chat_id,
                text="😕 Não entendi o formato no áudio. Diga algo como:\n"
                     "<code>Responsável, [Data], Descrição, Valor</code>\n\n"
                     "<b>Exemplo:</b>\n"
                     "<i>'Maria, hoje, Almoço, 35 e 50'</i>",
                parse_mode=ParseMode.HTML
            )
            return

        # Tenta autenticar e obter a planilha
        worksheet = authenticate_google_sheets()
        if not worksheet:
            logger.error("Falha ao autenticar/obter planilha do Google Sheets (áudio).")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Erro ao conectar com a planilha Google Sheets. Tente novamente mais tarde."
            )
            return

        # Tenta registrar na planilha
        success = record_expense_in_sheets(worksheet, expense_data)

        if success:
            logger.info(f"Enviando confirmação (áudio) para {user.username or user.id} para despesa: {expense_data}")
            confirmation_message = (
                f"✅ <b>Despesa registrada (via áudio)!</b>\n\n"
                f"📅 <b>Data:</b> {expense_data['data']}\n"
                f"📂 <b>Categoria:</b> {expense_data['categoria']}\n"
                f"📝 <b>Descrição:</b> {expense_data['descricao']}\n"
                f"👤 <b>Responsável:</b> {expense_data['responsavel']}\n"
                f"💰 <b>Valor:</b> R$ {expense_data['valor']:.2f}".replace('.',',')
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=confirmation_message,
                parse_mode=ParseMode.HTML
            )
            # TODO: Implementar envio de confirmação por áudio (TTS) aqui também, se desejado.
        else:
            logger.error(f"Falha ao registrar despesa (áudio) no Google Sheets para {user.username or user.id}. Dados: {expense_data}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Erro ao registrar a despesa do áudio na planilha Google Sheets. Tente novamente mais tarde."
            )

    except Exception as e:
        logger.error(f"Erro ao processar mensagem de voz de {user.username or user.id}: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Ocorreu um erro inesperado ao processar sua mensagem de voz."
        )
    finally:
        # Garante que o arquivo de áudio temporário seja removido
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Arquivo de áudio temporário removido: {file_path}")
            except OSError as e:
                logger.error(f"Erro ao remover arquivo de áudio temporário {file_path}: {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Loga os erros causados por Updates."""
    logger.error(f"Exceção ao processar um update: {context.error}", exc_info=context.error)
    # Opcionalmente, notificar o desenvolvedor ou um chat de admin sobre erros críticos
    # if isinstance(context.error, telegram.error.NetworkError):
    #     # handle network error


# --- Função Principal ---

def main() -> None:
    """Inicia o bot."""
    # Validações Iniciais Essenciais
    if TELEGRAM_BOT_TOKEN == "SEU_TOKEN_AQUI":
        logger.critical("Token do Telegram não configurado! Defina a variável de ambiente TELEGRAM_BOT_TOKEN.")
        return
    if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS_PATH):
         logger.critical(f"Arquivo de credenciais do Google Sheets não encontrado em: {GOOGLE_SHEETS_CREDENTIALS_PATH}. Defina GOOGLE_SHEETS_CREDENTIALS_PATH.")
         return
    if GOOGLE_SHEETS_ID == "SEU_ID_DA_PLANILHA_AQUI":
         logger.critical("ID da Planilha Google não configurado! Defina a variável de ambiente GOOGLE_SHEETS_ID.")
         return

    logger.info("Iniciando o bot...")

    # Cria a Application e passa o token do bot.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Registra os handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    # Registra o handler de erro (importante!)
    application.add_error_handler(error_handler)

    # Inicia o Bot (Polling)
    logger.info("Bot iniciado e aguardando mensagens...")
    application.run_polling()


if __name__ == "__main__":
    main()
