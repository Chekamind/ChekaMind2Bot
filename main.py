import logging
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Команда /start
def start(update: Update, context: CallbackContext):
    keyboard = [
        [KeyboardButton("🧘 Задание на осознанность")],
        [KeyboardButton("📝 Рефлексия")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    update.message.reply_text(
        "Привет! Я твой бот для осознанности 👋", 
        reply_markup=reply_markup
    )

# Обработка текстов
def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.lower()

    if "задание" in text:
        update.message.reply_text("Вот тебе задание на сегодня: наблюдай за дыханием 5 минут 🧘")
    elif "рефлексия" in text:
        update.message.reply_text("Как прошёл твой день? Запиши 3 мысли 📝")
    else:
        update.message.reply_text("Я тебя понял 😉")

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    print("Бот запущен...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
