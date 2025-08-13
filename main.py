import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

# -----------------------------
# Логирование
# -----------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("chekamind-bot")

# -----------------------------
# Настройки бота
# -----------------------------
BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://chekamind2bot.onrender.com{WEBHOOK_PATH}"
PORT = 10000  # Render проксирует HTTPS на этот порт

# -----------------------------
# Обработчики команд и кнопок
# -----------------------------
async def start(update: Update, context):
    keyboard = [
        [InlineKeyboardButton("Привет 👋", callback_data="hello")],
        [InlineKeyboardButton("Помощь ❓", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Я ваш ChekaMind Бот.", reply_markup=reply_markup)

async def button(update: Update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "hello":
        await query.edit_message_text(text="Рад вас видеть! 😊")
    elif query.data == "help":
        await query.edit_message_text(text="Я могу отправлять задания на осознанность и вести вас по шагам.")

# -----------------------------
# Основная функция
# -----------------------------
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    # Устанавливаем webhook
    await app.bot.set_webhook(WEBHOOK_URL)
    logger.info("🚀 Webhook установлен: %s", WEBHOOK_URL)

    # Запуск веб-сервера на Render
    await app.start_webhook(listen="0.0.0.0", port=PORT, webhook_path=WEBHOOK_PATH)
    logger.info("🌐 Web-сервер запущен на порту %d с webhook_path %s", PORT, WEBHOOK_PATH)

    # Ожидание, чтобы бот не завершался
    await app.idle()

# -----------------------------
# Запуск
# -----------------------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
