from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Задание на осознанность")],
                [KeyboardButton("Рефлексия")]]
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
        await update.message.reply_text("Как прошёл твой день? Запиши 3 мысли.")
    else:
        await update.message.reply_text("Я тебя понял 😉")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
