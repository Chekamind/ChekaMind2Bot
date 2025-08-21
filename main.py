import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "ТВОЙ_ТОКЕН"

# Старт
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Задание", "Рефлексия"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Привет! Я твой бот 🤖", reply_markup=reply_markup)

# Обработка текста
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Задание":
        await update.message.reply_text("Вот твоё задание ✍️")
    elif text == "Рефлексия":
        await update.message.reply_text("Время подвести итоги 📘")
    else:
        await update.message.reply_text(f"Ты написал: {text}")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 🚀 запускаем бот на поллинге
    app.run_polling()

if __name__ == "__main__":
    main()
