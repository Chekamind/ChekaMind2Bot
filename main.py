import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("chekamind-bot")

BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://chekamind2bot.onrender.com{WEBHOOK_PATH}"
PORT = 10000

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

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    await app.bot.set_webhook(WEBHOOK_URL)
    logger.info("Webhook установлен: %s", WEBHOOK_URL)
    await app.start_webhook(listen="0.0.0.0", port=PORT, webhook_path=WEBHOOK_PATH)
    await app.idle()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
