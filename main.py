import sqlite3
from datetime import datetime
import threading
from flask import Flask
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

# ===== База данных =====
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

user_waiting_comment = {}

# ===== /start =====
def start(update: Update, context: CallbackContext):
    keyboard = [
        [KeyboardButton("🧘 Задание на осознанность")],
        [KeyboardButton("📝 Рефлексия"), KeyboardButton("📊 Статистика")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    update.message.reply_text(
        "Привет! Я твой бот для осознанности 👋", 
        reply_markup=reply_markup
    )

# ===== Обработка сообщений =====
def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.lower()
    user_id = update.message.from_user.id
    username = update.message.from_user.username or ""

    if user_waiting_comment.get(user_id):
        comment = update.message.text
        cursor.execute(
            "INSERT INTO stats (user_id, username, comment, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, username, comment, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        user_waiting_comment[user_id] = False
        update.message.reply_text("✅ Комментарий сохранён! Продолжайте практику 🙏")
        return

    if "🧘" in text or "задание" in text:
        update.message.reply_text("Вот тебе задание на сегодня: наблюдай за дыханием 5 минут 🧘")
        user_waiting_comment[user_id] = True

    elif "📝" in text or "рефлексия" in text:
        update.message.reply_text("Как прошёл твой день? Запиши 3 мысли.")
        user_waiting_comment[user_id] = True

    elif "📊" in text or "статистика" in text:
        cursor.execute("SELECT COUNT(*), GROUP_CONCAT(comment, '\n- ') FROM stats WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        total = result[0]
        comments = result[1] if result[1] else "Нет комментариев"
        update.message.reply_text(
            f"📊 Ваша статистика:\n\nКоличество осознанных моментов: {total}\n\nКомментарии:\n- {comments}"
        )
    else:
        update.message.reply_text("Я тебя понял 😉")

# ===== Flask пингер =====
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "✅ Bot is alive!", 200

def run_flask():
    app_flask.run(host="0.0.0.0", port=8080)

# ===== Основной запуск =====
def main():
    threading.Thread(target=run_flask).start()

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    print("Бот запущен...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
