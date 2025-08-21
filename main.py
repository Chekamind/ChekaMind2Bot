import logging
import sqlite3
import threading
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,  # <--- поправлено
)
from flask import Flask

TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

conn = sqlite3.connect("mindfulness.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    comment TEXT,
    timestamp TEXT
)
""")
conn.commit()

MINDFULNESS_TIPS = [
    "Сделайте глубокий вдох и выдох, почувствуйте, как воздух наполняет лёгкие.",
    "Остановитесь на секунду и почувствуйте опору под ногами.",
    "Сосредоточьтесь на том, что видите прямо сейчас, без оценок.",
    "Обратите внимание на дыхание — просто наблюдайте за вдохом и выдохом.",
    "Сделайте паузу. Скажите себе: 'Я здесь. Я живу этим моментом'."
]

def start(update: Update, context):
    keyboard = [
        [InlineKeyboardButton("🧘 Осознанность", callback_data="mindfulness")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Привет! Выберите действие:", reply_markup=reply_markup)

def button_handler(update: Update, context):
    query = update.callback_query
    query.answer()
    if query.data == "mindfulness":
        import random
        tip = random.choice(MINDFULNESS_TIPS)
        context.user_data["awaiting_comment"] = True
        query.message.reply_text(
            f"🧘 Совет: {tip}\n\nНапишите свой комментарий (что почувствовали, заметили):"
        )
    elif query.data == "stats":
        cursor.execute(
            "SELECT COUNT(*), GROUP_CONCAT(comment, '\n- ') FROM stats WHERE user_id = ?",
            (query.from_user.id,)
        )
        result = cursor.fetchone()
        total = result[0]
        comments = result[1] if result[1] else "Нет комментариев"
        query.message.reply_text(
            f"📊 Ваша статистика:\n\nКоличество осознанных моментов: {total}\n\nКомментарии:\n- {comments}"
        )

def handle_message(update: Update, context):
    if context.user_data.get("awaiting_comment"):
        user = update.message.from_user
        comment = update.message.text
        cursor.execute(
            "INSERT INTO stats (user_id, username, comment, timestamp) VALUES (?, ?, ?, ?)",
            (user.id, user.username, comment, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        context.user_data["awaiting_comment"] = False
        update.message.reply_text("✅ Комментарий сохранён! Продолжайте практику 🙏")

app_flask = Flask(__name__)
@app_flask.route("/")
def home():
    return "✅ Bot is alive!", 200

def run_flask():
    app_flask.run(host="0.0.0.0", port=8080)

def main():
    threading.Thread(target=run_flask, daemon=True).start()

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))  # <--- поправлено

    logging.info("🤖 Бот запущен!")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
