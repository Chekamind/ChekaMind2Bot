import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chekamind-bot")

# Настройки
BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://chekamind2bot.onrender.com{WEBHOOK_PATH}"
PORT = 10000

# Обработчики команд и кнопок
async def start(update: Update, context):
    keyboard = [[InlineKeyboardButton("Привет 👋", callback_data="hello")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Я ваш ChekaMind Бот.", reply_markup=reply_markup)

async def button(update: Update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "hello":
        await query.edit_message_text("Рад вас видеть! 😊")

# Основная функция
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    logger.info("🚀 Запуск webhook...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
