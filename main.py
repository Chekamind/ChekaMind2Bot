import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Твой токен
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Задание на день", "Рефлексия"],
                ["Помощь", "О боте"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "Привет! 👋 Я твой бот для осознанности.\nВыбери действие:",
        reply_markup=reply_markup
    )


# Ответ на текстовые сообщения
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "Задание на день":
        await update.message.reply_text("📝 Вот твое задание на день: сделай паузу и подыши 3 минуты.")
    elif text == "Рефлексия":
        await update.message.reply_text("💭 Как прошел твой день? Напиши пару мыслей.")
    elif text == "Помощь":
        await update.message.reply_text("📌 Доступные команды: /start")
    elif text == "О боте":
        await update.message.reply_text("🤖 Бот помогает развивать осознанность каждый день.")
    else:
        await update.message.reply_text("Я тебя понял 😉 но пока не знаю, что ответить.")


# Основная функция запуска
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
