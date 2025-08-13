import os
import logging
import random
import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+
from aiohttp import web, ClientSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ----------------- Настройка -----------------
DATA_FILE = "data.json"
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("ERROR: Set BOT_TOKEN environment variable")

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

SAVE_INTERVAL_SECONDS = 60           # автосохранение данных на диск
SELF_PING_INTERVAL_SECONDS = 240     # самопинг локального веб-сервера (4 минуты)
AUTO_FINISH_CHECK_SECONDS = 300      # проверка авто-завершения (5 минут)
AUTO_FINISH_HOURS = 3                # авто-завершение после 3 часов
RESTART_DELAY_SECONDS = 5            # задержка перед перезапуском после ошибки

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- Хранилища -----------------
mindfulness_sessions = {}   # user_id -> list of {'time': iso_str, 'note': str}
fitness_sessions = {}       # user_id -> list of {'time': iso_str, 'note': str, 'duration_seconds': int}
active_fitness_sessions = {}  # user_id -> datetime (moscow tz)
user_states = {}            # user_id -> dict состояния (awaiting_note_confirm, awaiting_note и т.п.)

# ----------------- Клавиатуры -----------------
MAIN_KEYBOARD = [
    [KeyboardButton("💡 Задание"), KeyboardButton("📅 Рефлексия")],
    [KeyboardButton("✨ Я осознан!")],
    [KeyboardButton("⏱ Начать тренировку"), KeyboardButton("🏁 Закончить тренировку")],
    [KeyboardButton("📊 Статистика")]
]

STAT_CATEGORY_KEYBOARD = [
    [KeyboardButton("📊 Статистика по осознанности")],
    [KeyboardButton("📊 Статистика по спорту")],
    [KeyboardButton("🔙 Назад")]
]

STAT_PERIOD_KEYBOARD = [
    [KeyboardButton("📅 За день"), KeyboardButton("📆 За неделю")],
    [KeyboardButton("🔙 Назад")]
]

NOTE_CONFIRM_KEYBOARD = [
    [KeyboardButton("📝 Записать заметку"), KeyboardButton("❌ Отменить")]
]

NOTE_INPUT_KEYBOARD = [
    [KeyboardButton("❌ Пропустить заметку"), KeyboardButton("🔄 Отменить")]
]

# ----------------- Вспомогательные функции -----------------
def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)

def dt_to_iso(dt: datetime) -> str:
    # Сохраняем ISO с timezone
    return dt.isoformat()

def iso_to_dt(iso: str) -> datetime:
    # Парсим ISO с учётом зоны
    return datetime.fromisoformat(iso)

def load_data():
    global mindfulness_sessions, fitness_sessions
    if not os.path.exists(DATA_FILE):
        mindfulness_sessions = {}
        fitness_sessions = {}
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        mindfulness_sessions = data.get("mindfulness_sessions", {})
        fitness_sessions = data.get("fitness_sessions", {})
        # Преобразуем ключи в int (json сохраняет ключи как строки)
        mindfulness_sessions = {int(k): v for k, v in mindfulness_sessions.items()}
        fitness_sessions = {int(k): v for k, v in fitness_sessions.items()}
        logger.info("Data loaded from %s", DATA_FILE)
    except Exception as e:
        logger.error("Failed to load data: %s", e)
        mindfulness_sessions = {}
        fitness_sessions = {}

def save_data():
    try:
        # Преобразуем ключи в строки для json
        data = {
            "mindfulness_sessions": {str(k): v for k, v in mindfulness_sessions.items()},
            "fitness_sessions": {str(k): v for k, v in fitness_sessions.items()}
        }
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
        logger.info("Data saved to %s", DATA_FILE)
    except Exception as e:
        logger.error("Failed to save data: %s", e)

def add_mindfulness_session(user_id: int, time_dt: datetime, note: str):
    entry = {"time": dt_to_iso(time_dt), "note": note}
    mindfulness_sessions.setdefault(user_id, []).append(entry)
    save_data()

def add_fitness_session(user_id: int, time_dt: datetime, note: str, duration: timedelta = None):
    entry = {"time": dt_to_iso(time_dt), "note": note}
    if duration is not None:
        entry["duration_seconds"] = int(duration.total_seconds())
    fitness_sessions.setdefault(user_id, []).append(entry)
    save_data()

def format_duration_from_seconds(seconds: int) -> str:
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours:
        return f"{hours}ч {minutes}м {secs}с"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"

# ----------------- Webserver (keep-alive) -----------------
async def handle_root(request):
    return web.Response(text="Bot is alive!")

async def run_webserver():
    app = web.Application()
    app.add_routes([web.get("/", handle_root)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Webserver started on port 8080")

# ----------------- Самопинг (держит контейнер тёплым) -----------------
async def self_pinger():
    await asyncio.sleep(5)  # даём приложение стартовать
    url = "http://127.0.0.1:8080/"  # локальный вебсервер
    logger.info("Self-pinger started, pinging %s every %s sec", url, SELF_PING_INTERVAL_SECONDS)
    async with ClientSession() as sess:
        while True:
            try:
                async with sess.get(url, timeout=10) as resp:
                    logger.debug("Self-ping status %s", resp.status)
            except Exception as e:
                logger.warning("Self-ping failed: %s", e)
            await asyncio.sleep(SELF_PING_INTERVAL_SECONDS)

# ----------------- Автозавершение тренировок -----------------
async def fitness_auto_finish_checker(app):
    while True:
        now = now_moscow()
        to_finish = []
        for user_id, start_time in list(active_fitness_sessions.items()):
            duration = now - start_time
            if duration > timedelta(hours=AUTO_FINISH_HOURS):
                to_finish.append((user_id, start_time, duration))
        for user_id, start_time, duration in to_finish:
            del active_fitness_sessions[user_id]
            add_fitness_session(user_id, start_time, "Авто-завершение (превышено время)", duration)
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ Ваша тренировка, начатая в {start_time.strftime('%H:%M')}, автоматически завершена после {AUTO_FINISH_HOURS} часов."
                )
            except Exception as e:
                logger.error("Failed to notify user %s about auto-finish: %s", user_id, e)
        await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)

# ----------------- Периодическое автосохранение -----------------
async def periodic_save():
    while True:
        await asyncio.sleep(SAVE_INTERVAL_SECONDS)
        save_data()

# ----------------- Handlers -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states.pop(user_id, None)
    reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("Здравствуйте! Используйте кнопки для взаимодействия с ботом.", reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    state = user_states.get(user_id, {})

    # Обработка подтверждения заметки
    if state.get("awaiting_note_confirm"):
        if text == "📝 Записать заметку":
            user_states[user_id] = {
                "awaiting_note": True,
                "session_type": state["session_type"],
                "session_time": state["session_time"],
                "duration": state.get("duration")
            }
            await update.message.reply_text(
                "Напиши свою заметку:",
                reply_markup=ReplyKeyboardMarkup(NOTE_INPUT_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)
            )
            return
        elif text == "❌ Отменить":
            user_states.pop(user_id, None)
            await update.message.reply_text("Действие отменено.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
            return
        else:
            await update.message.reply_text("Пожалуйста, выбери из предложенных кнопок.")
            return

    # Обработка ввода заметки
    if state.get("awaiting_note"):
        if text in ["❌ Пропустить заметку", "🔄 Отменить"]:
            note = "Без заметки"
        else:
            note = text

        session_time = state["session_time"]
        session_type = state["session_type"]
        duration = state.get("duration")

        if session_type == "fitness":
            add_fitness_session(user_id, session_time, note, duration)
        else:
            add_mindfulness_session(user_id, session_time, note)

        user_states.pop(user_id, None)
        if note == "Без заметки":
            await update.message.reply_text("Сессия сохранена без заметки.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        else:
            await update.message.reply_text(f"Заметка сохранена: «{note}»", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return

    # Основные команды
    if text == "💡 Задание":
        await update.message.reply_text(random.choice([
            "Задача: остановись на 60 секунд и почувствуй тело.",
            "Задача: сделай 10 глубоких вдохов.",
            "Задача: послушай звуки вокруг тебя.",
        ]))
        return

    if text == "📅 Рефлексия":
        await update.message.reply_text(random.choice([
            "Рефлексия: что ты заметил сегодня?",
            "Рефлексия: чего ты добился на этой неделе?",
        ]))
        return

    if text == "✨ Я осознан!":
        now = now_moscow()
        user_states[user_id] = {
            "awaiting_note_confirm": True,
            "session_type": "mindfulness",
            "session_time": now,
            "duration": None
        }
        await update.message.reply_text("Хотите записать заметку об осознанности?", reply_markup=ReplyKeyboardMarkup(NOTE_CONFIRM_KEYBOARD, resize_keyboard=True))
        return

    if text == "⏱ Начать тренировку":
        if user_id in active_fitness_sessions:
            await update.message.reply_text("Тренировка уже запущена! Сначала завершите текущую.")
            return
        start_time = now_moscow()
        active_fitness_sessions[user_id] = start_time
        user_states[user_id] = {
            "awaiting_note_confirm": True,
            "session_type": "fitness",
            "session_time": start_time,
            "duration": None
        }
        await update.message.reply_text("Тренировка начата! Хотите записать заметку?", reply_markup=ReplyKeyboardMarkup(NOTE_CONFIRM_KEYBOARD, resize_keyboard=True))
        return

    if text == "🏁 Закончить тренировку":
        start_time = active_fitness_sessions.get(user_id)
        if not start_time:
            await update.message.reply_text("Тренировка не была начата.")
            return
        duration = now_moscow() - start_time
        del active_fitness_sessions[user_id]
        user_states[user_id] = {
            "awaiting_note_confirm": True,
            "session_type": "fitness",
            "session_time": start_time,
            "duration": duration
        }
        await update.message.reply_text(
            f"Тренировка завершена. Длительность: {str(duration).split('.')[0]}. Хотите записать заметку?",
            reply_markup=ReplyKeyboardMarkup(NOTE_CONFIRM_KEYBOARD, resize_keyboard=True)
        )
        return

    if text == "📊 Статистика":
        user_states[user_id] = {"menu": "stat_category"}
        reply_markup = ReplyKeyboardMarkup(STAT_CATEGORY_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Выберите категорию статистики:", reply_markup=reply_markup)
        return

    if text == "🔙 Назад":
        if user_states.get(user_id, {}).get("menu"):
            user_states.pop(user_id, None)
            await update.message.reply_text("Вернулись в главное меню.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        else:
            await update.message.reply_text("Используйте кнопки меню.")
        return

    # Выбор статистики / периодов
    state = user_states.get(user_id, {})
    if state.get("menu") == "stat_category":
        await process_stat_category(update, text, user_id)
        return
    elif state.get("menu") == "stat_period":
        await process_stat_period(update, text, user_id)
        return

    # Если ничего не подошло
    await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

# ----------------- Статистика -----------------
async def process_stat_category(update: Update, text: str, user_id: int):
    if text == "📊 Статистика по осознанности":
        user_states[user_id] = {"menu": "stat_period", "stat_category": "mindfulness"}
        reply_markup = ReplyKeyboardMarkup(STAT_PERIOD_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Выберите период для статистики по осознанности:", reply_markup=reply_markup)
    elif text == "📊 Статистика по спорту":
        user_states[user_id] = {"menu": "stat_period", "stat_category": "fitness"}
        reply_markup = ReplyKeyboardMarkup(STAT_PERIOD_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Выберите период для статистики по спорту:", reply_markup=reply_markup)
    elif text == "🔙 Назад":
        user_states.pop(user_id, None)
        await update.message.reply_text("Вернулись в главное меню.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
    else:
        await update.message.reply_text("Пожалуйста, выберите вариант из меню.")

async def process_stat_period(update: Update, text: str, user_id: int):
    state = user_states.get(user_id)
    if not state:
        await update.message.reply_text("Ошибка состояния. Возврат в главное меню.", reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))
        return

    stat_category = state.get("stat_category")
    now = now_moscow()

    if text == "📅 За день":
        period_start = now - timedelta(days=1)
    elif text == "📆 За неделю":
        period_start = now - timedelta(days=7)
    elif text == "🔙 Назад":
        user_states[user_id] = {"menu": "stat_category"}
        await update.message.reply_text("Выберите категорию статистики:", reply_markup=ReplyKeyboardMarkup(STAT_CATEGORY_KEYBOARD, resize_keyboard=True, one_time_keyboard=True))
        return
    else:
        await update.message.reply_text("Пожалуйста, выберите вариант из меню.")
        return

    if stat_category == "mindfulness":
        sessions = mindfulness_sessions.get(user_id, [])
        title = "осознанности"
    else:
        sessions = fitness_sessions.get(user_id, [])
        title = "спорта"

    # Фильтр по дате (в ISO строки сохранены с московской tz)
    filtered = []
    for s in sessions:
        s_dt = iso_to_dt(s["time"])
        if s_dt >= period_start:
            filtered.append((s_dt, s))

    if not filtered:
        await update.message.reply_text(f"За выбранный период нет данных по {title}.")
        return

    msg = f"📊 Статистика по {title} за период с {period_start.strftime('%d.%m.%Y')} по {now.strftime('%d.%m.%Y')}:\n"
    msg += f"Количество сессий: {len(filtered)}\n\n"

    for s_dt, s in filtered:
        time_str = s_dt.strftime("%d.%m %H:%M")
        note = s.get("note", "")
        dur_seconds = s.get("duration_seconds")
        dur_str = f", Длительность: {format_duration_from_seconds(dur_seconds)}" if dur_seconds is not None else ""
        msg += f"- {time_str}: {note}{dur_str}\n"

    user_states.pop(user_id, None)
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True))

# ----------------- Запуск и управление жизненным циклом -----------------
async def run_bot_instance():
    """Запускает один экземпляр приложения (и ждёт, пока оно работает)."""
    load_data()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Initializing bot application")
    await app.initialize()
    await app.start()

    # Запускаем вспомогательные задачи
    app.create_task(run_webserver())
    app.create_task(self_pinger())
    app.create_task(fitness_auto_finish_checker(app))
    app.create_task(periodic_save())

    # Запуск polling
    await app.updater.start_polling()
    logger.info("Bot polling started")

    # Ждём бесконечно (или пока не будет исключение)
    await asyncio.Event().wait()

    # При завершении корректно остановим
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        pass

async def startup_loop():
    """Запускает бот в цикле — при ошибке перезапускает."""
    while True:
        try:
            await run_bot_instance()
        except Exception as e:
            logger.exception("Bot crashed, will restart after delay: %s", e)
            await asyncio.sleep(RESTART_DELAY_SECONDS)
        else:
            # Если run_bot_instance вышел без исключения (неожиданно), перезапустим через небольшую задержку
            logger.warning("Bot instance stopped unexpectedly without exception, restarting...")
            await asyncio.sleep(RESTART_DELAY_SECONDS)

# Универсальный запуск: если event loop уже запущен (Replit), используем create_task,
# иначе asyncio.run
if __name__ == "__main__":
    try:
        # Проверяем, есть ли запущенный цикл в текущем потоке
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Нет — безопасно запускаем
        asyncio.run(startup_loop())
    else:
        # Есть — создаём таск (для Replit / Jupyter)
        loop.create_task(startup_loop())
        loop.run_forever()
