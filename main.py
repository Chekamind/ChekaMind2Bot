# main.py — финальная версия (webhook + YandexGPT + сохранение в /tmp)
import os
import logging
import random
import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web, ClientSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ----------------- КОНФИГУРАЦИЯ (вставлены ваши данные) -----------------
# Токен Telegram (временно в коде; рекомендую вынести в env)
BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

# Вебхук (ваш Render URL)
WEBHOOK_URL = "https://chekamind2bot.onrender.com/webhook"

# Секретный заголовок для вебхука — генерируем фиксированную строку здесь
# (Telegram пришлёт заголовок X-Telegram-Bot-Api-Secret-Token = этот токен)
TELEGRAM_SECRET_TOKEN = "chekamind2bot-secret-2025"  # можно заменить на длинную случайную строку

# Yandex Cloud keys (вы вставили их)
YC_API_KEY = "YCAJEXPfGNRARTTxjsV6Y14ek"
YC_FOLDER_ID = "YCMvITcnXbFMtzgZbkrm-kd8KFW-0uZr3wd-1Bii"  # если это не folder id — замените на правильный
YC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Файл для хранения — используем /tmp чтобы не было проблем с правами на Render
DATA_FILE = os.getenv("DATA_FILE", "/tmp/chekamind_data.json")

# Таймзоны и интервалы
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
SAVE_INTERVAL_SECONDS = 60
SELF_PING_INTERVAL_SECONDS = 240
AUTO_FINISH_HOURS = 3
AUTO_FINISH_CHECK_SECONDS = 300
RESTART_DELAY_SECONDS = 5
CLEANUP_MAX_DAYS = 90

# Порт (Render задаёт PORT)
PORT = int(os.getenv("PORT", 10000))

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("chekamind-bot")

# ----------------- Глобальные хранилища -----------------
mindfulness_sessions = {}
fitness_sessions = {}
active_fitness_sessions = {}
user_states = {}
subscribed_users = set()
last_save_time = None

# ----------------- Клавиатуры -----------------
MAIN_KEYBOARD = [
    [KeyboardButton("💡 Задание"), KeyboardButton("📅 Рефлексия")],
    [KeyboardButton("✨ Я осознан!")],
    [KeyboardButton("⏱ Начать тренировку"), KeyboardButton("🏁 Закончить тренировку")],
    [KeyboardButton("🧠 Поговорить с ИИ")],
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

def main_keyboard():
    return ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)

def stat_category_keyboard():
    return ReplyKeyboardMarkup(STAT_CATEGORY_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)

def stat_period_keyboard():
    return ReplyKeyboardMarkup(STAT_PERIOD_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)

def note_confirm_keyboard():
    return ReplyKeyboardMarkup(NOTE_CONFIRM_KEYBOARD, resize_keyboard=True)

def note_input_keyboard():
    return ReplyKeyboardMarkup(NOTE_INPUT_KEYBOARD, resize_keyboard=True, one_time_keyboard=True)

# ----------------- Утилиты по времени и форматам -----------------
def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)

def dt_to_iso(dt: datetime) -> str:
    return dt.astimezone(MOSCOW_TZ).isoformat()

def iso_to_dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=MOSCOW_TZ)

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

# ----------------- Работа с данными (файл в /tmp) -----------------
def load_data():
    global mindfulness_sessions, fitness_sessions, last_save_time
    if not os.path.exists(DATA_FILE):
        mindfulness_sessions = {}
        fitness_sessions = {}
        last_save_time = now_moscow()
        logger.info("ℹ️ Файл данных не найден — создадим новый в %s", DATA_FILE)
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        mindfulness_sessions = {int(k): v for k, v in data.get("mindfulness_sessions", {}).items()}
        fitness_sessions = {int(k): v for k, v in data.get("fitness_sessions", {}).items()}
        last_save_time = now_moscow()
        logger.info("✅ Данные загружены из %s", DATA_FILE)
    except Exception as e:
        logger.exception("❌ Ошибка загрузки данных: %s", e)
        mindfulness_sessions = {}
        fitness_sessions = {}
        last_save_time = now_moscow()

def save_data(force: bool = False):
    global last_save_time
    now = now_moscow()
    if not force and last_save_time and (now - last_save_time).total_seconds() < SAVE_INTERVAL_SECONDS:
        return
    try:
        data = {
            "mindfulness_sessions": {str(k): v for k, v in mindfulness_sessions.items()},
            "fitness_sessions": {str(k): v for k, v in fitness_sessions.items()}
        }
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
        last_save_time = now
        logger.info("💾 Data saved to %s", DATA_FILE)
    except Exception as e:
        logger.exception("❌ Error saving data: %s", e)

async def periodic_save():
    while True:
        await asyncio.sleep(SAVE_INTERVAL_SECONDS)
        save_data()

def add_mindfulness_session(user_id: int, time_dt: datetime, note: str):
    entry = {"time": dt_to_iso(time_dt), "note": note}
    mindfulness_sessions.setdefault(user_id, []).append(entry)

def add_fitness_session(user_id: int, time_dt: datetime, note: str, duration: timedelta = None):
    entry = {"time": dt_to_iso(time_dt), "note": note}
    if duration is not None:
        entry["duration_seconds"] = int(duration.total_seconds())
    fitness_sessions.setdefault(user_id, []).append(entry)

def cleanup_old_sessions():
    cutoff = now_moscow() - timedelta(days=CLEANUP_MAX_DAYS)
    cleaned = 0
    for user_id in list(mindfulness_sessions.keys()):
        old_len = len(mindfulness_sessions[user_id])
        mindfulness_sessions[user_id] = [s for s in mindfulness_sessions[user_id] if iso_to_dt(s["time"]) >= cutoff]
        cleaned += old_len - len(mindfulness_sessions[user_id])
        if not mindfulness_sessions[user_id]:
            del mindfulness_sessions[user_id]
    for user_id in list(fitness_sessions.keys()):
        old_len = len(fitness_sessions[user_id])
        fitness_sessions[user_id] = [s for s in fitness_sessions[user_id] if iso_to_dt(s["time"]) >= cutoff]
        cleaned += old_len - len(fitness_sessions[user_id])
        if not fitness_sessions[user_id]:
            del fitness_sessions[user_id]
    if cleaned:
        logger.info("🧹 Cleaned %d old sessions", cleaned)
        save_data(force=True)

# ----------------- Вебсервер и webhook -----------------
async def handle_root(request):
    return web.Response(text="🧘 Mindfulness Bot is alive!")

async def handle_health(request):
    return web.Response(text="OK", status=200)

def make_webhook_handler(tg_app):
    async def _handler(request):
        # проверяем секретный заголовок
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != TELEGRAM_SECRET_TOKEN:
            logger.warning("Forbidden webhook call (bad secret token)")
            return web.Response(status=403, text="Forbidden")
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Bad JSON")
        try:
            update = Update.de_json(payload, tg_app.bot)
            await tg_app.process_update(update)
        except Exception as e:
            logger.exception("Failed to process update: %s", e)
            return web.Response(status=500, text="Internal Error")
        return web.Response(text="OK")
    return _handler

async def run_webserver(tg_app):
    aio = web.Application()
    aio.add_routes([
        web.get("/", handle_root),
        web.get("/health", handle_health),
        web.post("/webhook", make_webhook_handler(tg_app)),
    ])
    runner = web.AppRunner(aio)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("🌐 Web server started on port %s (webhook path: /webhook)", PORT)

# ----------------- Самопинг -----------------
async def self_pinger():
    await asyncio.sleep(5)
    url = f"http://127.0.0.1:{PORT}/"
    logger.info("🔁 Self-pinger: %s every %d sec", url, SELF_PING_INTERVAL_SECONDS)
    async with ClientSession() as sess:
        while True:
            try:
                async with sess.get(url, timeout=10) as resp:
                    logger.debug("Ping: %d", resp.status)
            except Exception as e:
                logger.debug("Ping failed: %s", e)
            await asyncio.sleep(SELF_PING_INTERVAL_SECONDS)

# ----------------- Фоновые задачи: авто-завершение, напоминания и т.д. -----------------
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
                await app.bot.send_message(chat_id=user_id, text=f"⚠️ Ваша тренировка автоматически завершена после {AUTO_FINISH_HOURS} часов.")
            except Exception as e:
                logger.debug("Notify failed: %s", e)
        await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)

async def fitness_reminder_checker(app):
    notified = set()
    while True:
        now = now_moscow()
        for user_id, start_time in list(active_fitness_sessions.items()):
            duration = now - start_time
            if timedelta(hours=2) <= duration < timedelta(hours=2, minutes=6) and user_id not in notified:
                try:
                    await app.bot.send_message(chat_id=user_id, text="🔔 Вы тренируетесь уже 2 часа. Не забудьте завершить сессию!")
                    notified.add(user_id)
                except Exception:
                    pass
            elif duration >= timedelta(hours=AUTO_FINISH_HOURS) or duration < timedelta(hours=2):
                notified.discard(user_id)
        await asyncio.sleep(300)

async def daily_cleanup():
    while True:
        now = now_moscow()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        cleanup_old_sessions()

async def daily_report(app):
    while True:
        now = now_moscow()
        next_report = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= next_report:
            next_report += timedelta(days=1)
        await asyncio.sleep((next_report - now).total_seconds())

        today_start = now_moscow().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        all_users = set(mindfulness_sessions.keys()) | set(fitness_sessions.keys())
        for user_id in list(all_users):
            mindful_today = [s for s in mindfulness_sessions.get(user_id, []) if today_start <= iso_to_dt(s["time"]) < tomorrow_start]
            fitness_today = [s for s in fitness_sessions.get(user_id, []) if today_start <= iso_to_dt(s["time"]) < tomorrow_start]
            if not mindful_today and not fitness_today:
                continue
            total_duration = sum(s.get("duration_seconds", 0) for s in fitness_today)
            duration_str = format_duration_from_seconds(total_duration) if total_duration else "0с"
            report = (
                "🌙 *Ежедневный отчёт*\n\n"
                f"📅 *Сегодня вы:* \n"
                f"✨ Отмечали осознанность: {len(mindful_today)} раз\n"
                f"🏋️‍♂️ Провели тренировок: {len(fitness_today)}\n"
                f"⏱ Общая длительность тренировок: {duration_str}\n\n"
                "Молодец! Завтра — ещё лучше 💪"
            )
            try:
                await app.bot.send_message(chat_id=user_id, text=report, parse_mode="Markdown")
            except Exception:
                pass
        await asyncio.sleep(60)

MINDFULNESS_TASKS = [
    "Сегодня замечай, как часто ты дышишь. Сделай 3 глубоких вдоха каждый час.",
    "Почувствуй свои стопы. Ходи босиком хотя бы 5 минут.",
    "Пей чай или кофе, не глядя в телефон. Почувствуй вкус, температуру, запах.",
    "Заметь одно чувство в теле каждые 2 часа: тепло, напряжение, лёгкость.",
    "Послушай 1 минуту тишины. Что слышишь? А если ничего — это тоже ок.",
    "Сделай 10 шагов очень медленно. Почувствуй каждый момент движения.",
    "Заметь, что ты чувствуешь прямо сейчас. Назови это: 'Это — тревога', 'Это — усталость'.",
    "Посмотри в окно и найди 3 зелёных предмета. Просто посмотри — без оценок.",
    "Дотронься до чего-то прохладного или тёплого. Почувствуй температуру.",
    "Сделай паузу перед тем, как ответить в чате. Подыши 3 раза.",
]

async def daily_task_sender(app):
    while True:
        now = now_moscow()
        next_send = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= next_send:
            next_send += timedelta(days=1)
        await asyncio.sleep((next_send - now).total_seconds())
        task = random.choice(MINDFULNESS_TASKS)
        for user_id in list(subscribed_users):
            try:
                await app.bot.send_message(chat_id=user_id, text=f"🌅 *Задание на сегодня:*\n\n{task}\n\nУдачи! Ты справишься 💛", parse_mode="Markdown")
            except Exception:
                subscribed_users.discard(user_id)
        await asyncio.sleep(60)

# ----------------- YANDEX GPT -----------------
async def get_ai_response(prompt: str) -> str:
    if not YC_API_KEY or not YC_FOLDER_ID:
        return "❌ ИИ не настроен. Обратитесь к разработчику."
    system_message = (
        "Ты — тёплый и мудрый наставник по осознанности, внимательности и внутреннему росту. "
        "Отвечай кратко (1–3 предложения), с заботой, без оценок. "
        "Говори как друг, который понимает. Используй мягкие метафоры и эмодзи, когда уместно."
    )
    payload = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {"temperature": 0.6, "maxTokens": 500},
        "messages": [
            {"role": "system", "text": system_message},
            {"role": "user", "text": prompt},
        ],
    }
    headers = {"Authorization": f"Api-Key {YC_API_KEY}", "Content-Type": "application/json"}
    try:
        async with ClientSession() as session:
            async with session.post(YC_API_URL, json=payload, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("YandexGPT error %d: %s", resp.status, error_text)
                    return "🧠 Извини, не могу подключиться к ИИ. Попробуй позже."
                data = await resp.json()
                try:
                    return data["result"]["alternatives"][0]["message"]["text"].strip()
                except Exception:
                    logger.error("Unexpected YandexGPT response: %s", data)
                    return "🧠 Извини, ИИ ответил в неожиданном формате."
    except Exception as e:
        logger.exception("YandexGPT request failed: %s", e)
        return "🧠 Извини, произошла ошибка при общении с ИИ."

# ----------------- Хендлеры -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states.pop(user_id, None)
    if user_id in active_fitness_sessions:
        start_time = active_fitness_sessions[user_id]
        await update.message.reply_text(f"⚠️ У вас уже запущена тренировка с {start_time.strftime('%H:%M')}!\nНе забудьте завершить её кнопкой «🏁 Закончить тренировку».\n\nПривет! Давай развиваться вместе 🌱", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("Привет! Давай развиваться вместе 🌱\nИспользуй кнопки ниже, чтобы отмечать осознанность, тренировки и смотреть прогресс.", reply_markup=main_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    state = user_states.get(user_id, {})

    # Вопрос к ИИ
    if state.get("awaiting_ai_question"):
        if text == "❌ Отмена":
            user_states.pop(user_id, None)
            await update.message.reply_text("Общение с ИИ отменено.", reply_markup=main_keyboard())
            return
        await update.message.reply_text("🧠 Думаю...")
        response = await get_ai_response(text)
        await update.message.reply_text(response, reply_markup=main_keyboard())
        user_states.pop(user_id, None)
        return

    # Основные кнопки
    if text == "💡 Задание":
        await update.message.reply_text(random.choice(["Задача: остановись на 60 секунд и почувствуй тело.", "Задача: сделай 10 глубоких вдохов.", "Задача: послушай звуки вокруг тебя."]))
        return

    if text == "📅 Рефлексия":
        await update.message.reply_text(random.choice(["Рефлексия: что ты заметил сегодня?", "Рефлексия: чего ты добился на этой неделе?"]))
        return

    if text == "✨ Я осознан!":
        now = now_moscow()
        user_states[user_id] = {"awaiting_note_confirm": True, "session_type": "mindfulness", "session_time": now, "duration": None}
        await update.message.reply_text("Хотите записать заметку об осознанности?", reply_markup=note_confirm_keyboard())
        return

    if text == "⏱ Начать тренировку":
        if user_id in active_fitness_sessions:
            await update.message.reply_text("Тренировка уже запущена! Сначала завершите текущую.", reply_markup=main_keyboard())
            return
        start_time = now_moscow()
        active_fitness_sessions[user_id] = start_time
        user_states[user_id] = {"awaiting_note_confirm": True, "session_type": "fitness", "session_time": start_time, "duration": None}
        await update.message.reply_text(f"✅ Тренировка начата в {start_time.strftime('%H:%M')}!\nНе забудьте нажать «🏁 Закончить тренировку», когда закончите.", reply_markup=note_confirm_keyboard())
        return

    if text == "🏁 Закончить тренировку":
        start_time = active_fitness_sessions.get(user_id)
        if not start_time:
            await update.message.reply_text("Тренировка не была начата.", reply_markup=main_keyboard())
            return
        end_time = now_moscow()
        duration = end_time - start_time
        del active_fitness_sessions[user_id]
        user_states[user_id] = {"awaiting_note_confirm": True, "session_type": "fitness", "session_time": start_time, "duration": duration}
        await update.message.reply_text(f"🎉 Тренировка завершена!\n⏱ Начало: {start_time.strftime('%H:%M')}\n⏱ Окончание: {end_time.strftime('%H:%M')}\n⏱ Длительность: {str(duration).split('.')[0]}\nХотите записать заметку?", reply_markup=note_confirm_keyboard())
        return

    if text == "🧠 Поговорить с ИИ":
        user_states[user_id] = {"awaiting_ai_question": True}
        await update.message.reply_text("💭 Напиши, что тебя волнует. Я постараюсь помочь с позиции осознанности.\nНапример: «Как справиться с тревогой?»", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True, one_time_keyboard=True))
        return

    if text == "📊 Статистика":
        user_states[user_id] = {"menu": "stat_category"}
        await update.message.reply_text("Выберите категорию статистики:", reply_markup=stat_category_keyboard())
        return

    if text == "🔙 Назад":
        user_states.pop(user_id, None)
        await update.message.reply_text("Вернулись в главное меню.", reply_markup=main_keyboard())
        return

    # Подтверждение заметки
    if state.get("awaiting_note_confirm"):
        if text == "📝 Записать заметку":
            user_states[user_id] = {"awaiting_note": True, "session_type": state["session_type"], "session_time": state["session_time"], "duration": state.get("duration")}
            await update.message.reply_text("Напишите заметку:", reply_markup=note_input_keyboard())
            return
        elif text == "❌ Отменить":
            user_states.pop(user_id, None)
            await update.message.reply_text("Действие отменено.", reply_markup=main_keyboard())
            return
        else:
            await update.message.reply_text("Пожалуйста, выберите действие.", reply_markup=note_confirm_keyboard())
            return

    # Ввод заметки
    if state.get("awaiting_note"):
        note = "Без заметки" if text in ["❌ Пропустить заметку", "🔄 Отменить"] else text
        session_time = state["session_time"]
        session_type = state["session_type"]
        duration = state.get("duration")
        if session_type == "fitness":
            add_fitness_session(user_id, session_time, note, duration)
        else:
            add_mindfulness_session(user_id, session_time, note)
        user_states.pop(user_id, None)
        save_data()
        if note == "Без заметки":
            await update.message.reply_text("Сессия сохранена без заметки.", reply_markup=main_keyboard())
        else:
            await update.message.reply_text(f"✅ Заметка сохранена: «{note}»", reply_markup=main_keyboard())
        return

    # Меню статистики и вывод
    if state.get("menu") == "stat_category":
        if text == "📊 Статистика по осознанности":
            user_states[user_id] = {"menu": "stat_period", "stat_category": "mindfulness"}
            await update.message.reply_text("Выберите период:", reply_markup=stat_period_keyboard())
        elif text == "📊 Статистика по спорту":
            user_states[user_id] = {"menu": "stat_period", "stat_category": "fitness"}
            await update.message.reply_text("Выберите период:", reply_markup=stat_period_keyboard())
        else:
            await update.message.reply_text("Выберите из меню.", reply_markup=stat_category_keyboard())
        return

    if state.get("menu") == "stat_period":
        now = now_moscow()
        if text == "📅 За день":
            period_start = now - timedelta(days=1)
        elif text == "📆 За неделю":
            period_start = now - timedelta(days=7)
        elif text == "🔙 Назад":
            user_states[user_id] = {"menu": "stat_category"}
            await update.message.reply_text("Выберите категорию:", reply_markup=stat_category_keyboard())
            return
        else:
            await update.message.reply_text("Выберите из меню.", reply_markup=stat_period_keyboard())
            return

        cat = state["stat_category"]
        sessions = mindfulness_sessions.get(user_id, []) if cat == "mindfulness" else fitness_sessions.get(user_id, [])
        title = "осознанности" if cat == "mindfulness" else "спорта"
        filtered = [(iso_to_dt(s["time"]), s) for s in sessions if iso_to_dt(s["time"]) >= period_start]
        if not filtered:
            await update.message.reply_text(f"За выбранный период нет данных по {title}.", reply_markup=main_keyboard())
            return

        msg = (f"📊 *Статистика по {title}* за период с {period_start.strftime('%d.%m.%Y')} по {now.strftime('%d.%m.%Y')}:\n"
               f"🔢 Всего сессий: {len(filtered)}\n\n")
        for dt, s in filtered:
            time_str = dt.strftime("%d.%m %H:%M")
            note = (s.get("note") or "").strip()
            dur = s.get("duration_seconds")
            dur_str = f"⏱ {format_duration_from_seconds(dur)}" if dur else ""
            entry = f"🔹 *{time_str}*"
            if dur_str:
                entry += f" | {dur_str}"
            entry += "\n"
            if note and note != "Без заметки":
                entry += f"  📝 _{note}_"
            else:
                entry += f"  💬 _Без заметки_"
            msg += entry + "\n\n"
        user_states.pop(user_id, None)
        await update.message.reply_text(msg, reply_markup=main_keyboard(), parse_mode="Markdown")
        return

    # fallback
    await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=main_keyboard())

# ----------------- Запуск (webhook) -----------------
async def run_bot():
    load_data()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # инициализация и установка webhook с секретом
    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL, secret_token=TELEGRAM_SECRET_TOKEN, drop_pending_updates=True)
    await application.start()

    # фоновые таски и вебсервер (aiohttp)
    application.create_task(run_webserver(application))
    application.create_task(self_pinger())
    application.create_task(fitness_auto_finish_checker(application))
    application.create_task(fitness_reminder_checker(application))
    application.create_task(daily_cleanup())
    application.create_task(periodic_save())
    application.create_task(daily_report(application))
    application.create_task(daily_task_sender(application))

    logger.info("🚀 Bot started, webhook set to %s", WEBHOOK_URL)
    await asyncio.Event().wait()

async def main():
    while True:
        try:
            await run_bot()
        except Exception:
            logger.exception("💥 Бот упал, перезапуск через %d сек...", RESTART_DELAY_SECONDS)
            await asyncio.sleep(RESTART_DELAY_SECONDS)

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception:
        logger.exception("Fatal error in main loop")
