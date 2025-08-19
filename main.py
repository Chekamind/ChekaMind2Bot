import os
import logging
from datetime import time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Включаем логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("📌 Задание на сегодня")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
 "👋 Привет! Я твой бот для осознанности. Сегодня я буду присылать тебе задания утром и вопросы для размышлений вечером!"
        "Используй кнопки ниже для управления.",
        reply_markup=reply_markup
    )

# Ответ на кнопку
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📌 Задание на сегодня":
        await update.message.reply_text("Сегодняшнее задание: замедлись и обрати внимание на дыхание в течение 2 минут.")
    else:
        await update.message.reply_text("Я тебя понял 👍")

# Утреннее задание
async def morning_task(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(job.chat_id, "🌅 Доброе утро! Сегодняшнее задание: 5 минут наблюдай за своими мыслями, не оценивая их.")

# Вечернее напоминание
async def evening_reflection(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(job.chat_id, "🌙 Время рефлексии: вспомни три момента за день, когда ты был максимально осознанным.")

# Команда /set_schedule
async def set_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    # Утро 08:30
    context.job_queue.run_daily(morning_task, time(hour=8, minute=30), chat_id=chat_id)
    # Вечер 23:00
    context.job_queue.run_daily(evening_reflection, time(hour=23, minute=0), chat_id=chat_id)

    await update.message.reply_text("✅ Расписание установлено: задания в 08:30 и 23:00.")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_schedule", set_schedule))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()

