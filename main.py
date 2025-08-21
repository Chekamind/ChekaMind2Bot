from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask
import threading
import asyncio

TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

# ================== Бот ==================
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

# ================== Flask-заглушка ==================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!"

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    threading.Thread(target=lambda: asyncio.run(run_bot())).start()
    # Запускаем Flask на порту 10000
    flask_app.run(host="0.0.0.0", port=10000)
