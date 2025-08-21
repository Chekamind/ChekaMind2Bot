import os
import asyncio
import threading
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

# Flask для проверки, что сервис жив
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "Bot is running ✅"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("🧘 Задание на осознанность")],
        [KeyboardButton("📝 Рефлексия")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! Я твой бот для осознанности 👋",
        reply_markup=reply_markup
    )

# Обработка текстов
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()

    if "задание" in text:
        await update.message.reply_text("Вот тебе задание на сегодня: наблюдай за дыханием 5 минут 🧘")
    elif "рефлексия" in text:
        await update.message.reply_text("Как прошёл твой день? Запиши 3 мысли 📝")
    else:
        await update.message.reply_text("Я тебя понял 😉")

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    await app.run_polling()

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    threading.Thread(target=lambda: asyncio.run(run_bot())).start()
    
    # Запускаем Flask на порту, который видит Render
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
