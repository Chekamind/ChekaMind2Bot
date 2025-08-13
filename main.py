import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiohttp import web, ClientSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ----------------- Настройки -----------------
DATA_FILE = "/var/data/data.json"
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("ERROR: Установите переменную окружения BOT_TOKEN")

YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

SAVE_INTERVAL_SECONDS = 60           # дебаунс сохранения
SELF_PING_INTERVAL_SECONDS = 240     # самопинг для keep-alive
AUTO_FINISH_HOURS = 3                # авто-завершение тренировки после X часов
AUTO_FINISH_CHECK_SECONDS = 300      # проверка каждые 5 минут
RESTART_DELAY_SECONDS = 5            # перезапуск после ошибки
CLEANUP_MAX_DAYS = 90                # чистка старше 90 дней

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- Глобальные хранилища -----------------
mindfulness_sessions = {}   # user_id -> [{'time': iso, 'note': str}]
fitness_sessions = {}       # user_id -> [{'time': iso, 'note': str, 'duration_seconds': int}]
active_fitness_sessions = {}  # user_id -> datetime (Moscow)
user_states = {}            # user_id -> dict
subscribed_users = set()    # user_id для ежедневных заданий
last_save_time = None       # для дебаунса сохранения

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
    return dt.isoformat()

def iso_to_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)

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

# ----------------- Клавиатуры как функции -----------------
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

# ----------------- Работа с данными -----------------
def load_data():
    global mindfulness_sessions, fitness_sessions, last_save_time
    if not os.path.exists(DATA_FILE):
        mindfulness_sessions = {}
        fitness_sessions = {}
        last_save_time = now_moscow()
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        mindfulness_sessions = {int(k): v for k, v in data.get("mindfulness_sessions", {}).items()}
        fitness_sessions = {int(k): v for k, v in data.get("fitness_sessions", {}).items()}
        last_save_time = now_moscow()
        logger.info("✅ Данные загружены из %s", DATA_FILE)
    except Exception as e:
        logger.error("❌ Ошибка загрузки данных: %s", e)
        mindfulness_sessions = {}
        fitness_sessions = {}
        last_save_time = now_moscow()

def save_data():
    global last_save_time
    now = now_moscow()
    if last_save_time and (now - last_save_time).total_seconds() < SAVE_INTERVAL_SECONDS:
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
        logger.info("💾 Данные сохранены в %s", DATA_FILE)
    except Exception as e:
        logger.error("❌ Ошибка сохранения данных: %s", e)

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
        mindfulness_sessions[user_id] = [
            s for s in mindfulness_sessions[user_id]
            if iso_to_dt(s["time"]) >= cutoff
        ]
        cleaned += old_len - len(mindfulness_sessions[user_id])
        if not mindfulness_sessions[user_id]:
            del mindfulness_sessions[user_id]

    for user_id in list(fitness_sessions.keys()):
        old_len = len(fitness_sessions[user_id])
        fitness_sessions[user_id] = [
            s for s in fitness_sessions[user_id]
            if iso_to_dt(s["time"]) >= cutoff
        ]
        cleaned += old_len - len(fitness_sessions[user_id])
        if not fitness_sessions[user_id]:
            del fitness_sessions[user_id]

    if cleaned:
        logger.info("🧹 Очищено %d старых сессий", cleaned)
        save_data()

# ----------------- Веб-сервер (для Render) -----------------
async def handle_root(request):
    return web.Response(text="🧘 Mindfulness Bot is alive!")

async def handle_health(request):
    return web.Response(text="OK", status=200)

async def run_webserver():
    app = web.Application()
    app.add_routes([web.get("/", handle_root), web.get("/health", handle_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Веб-сервер запущен на порту {port}")

# ----------------- Самопинг -----------------
async def self_pinger():
    await asyncio.sleep(5)
    url = f"http://127.0.0.1:{os.getenv('PORT', 8080)}/"
    logger.info("🔁 Самопинг запущен: %s каждые %d сек", url, SELF_PING_INTERVAL_SECONDS)
    async with ClientSession() as sess:
        while True:
            try:
                async with sess.get(url, timeout=10) as resp:
                    logger.debug("Ping: %d", resp.status)
            except Exception as e:
                logger.warning("Ping failed: %s", e)
            await asyncio.sleep(SELF_PING_INTERVAL_SECONDS)

# ----------------- Авто-завершение тренировок -----------------
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
                logger.error("❌ Не удалось уведомить пользователя %s: %s", user_id, e)
        await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)

# ----------------- Напоминание о долгой тренировке -----------------
async def fitness_reminder_checker(app):
    notified = set()
    while True:
        now = now_moscow()
        for user_id, start_time in active_fitness_sessions.items():
            duration = now - start_time
            if timedelta(hours=2) <= duration < timedelta(hours=2.1) and user_id not in notified:
                try:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text="🔔 Вы тренируетесь уже 2 часа. Не забудьте завершить сессию!"
                    )
                    notified.add(user_id)
                except Exception as e:
                    logger.error("❌ Ошибка напоминания: %s", e)
            elif duration >= timedelta(hours=AUTO_FINISH_HOURS):
                notified.discard(user_id)
            elif duration < timedelta(hours=2):
                notified.discard(user_id)
        await asyncio.sleep(300)

# ----------------- Ежедневная очистка -----------------
async def daily_cleanup():
    while True:
        now = now_moscow()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        cleanup_old_sessions()
        logger.info("✅ Ежедневная очистка завершена")

# ----------------- Ежедневный отчёт в 23:00 -----------------
async def daily_report(app):
    while True:
        now = now_moscow()
        next_report = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= next_report:
            next_report += timedelta(days=1)
        wait_seconds = (next_report - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        today_start = now_moscow().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        for user_id in list(mindfulness_sessions.keys()):
            mindful_today = [
                s for s in mindfulness_sessions.get(user_id, [])
                if today_start <= iso_to_dt(s["time"]) < tomorrow_start
            ]

            fitness_today = [
                s for s in fitness_sessions.get(user_id, [])
                if today_start <= iso_to_dt(s["time"]) < tomorrow_start
            ]

            total_duration = sum(s.get("duration_seconds", 0) for s in fitness_today)
            duration_str = format_duration_from_seconds(total_duration) if total_duration else "0с"

            if not mindful_today and not fitness_today:
                continue

            report = (
                "🌙 *Ежедневный отчёт*\n\n"
                f"📅 *Сегодня вы:* \n"
                f"✨ Отмечали осознанность: {len(mindful_today)} раз\n"
                f"🏋️‍♂️ Провели тренировок: {len(fitness_today)}\n"
                f"⏱ Общая длительность тренировок: {duration_str}\n\n"
                "Молодец! Завтра — ещё лучше 💪"
            )

            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=report,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error("❌ Не удалось отправить отчёт пользователю %s: %s", user_id, e)

        logger.info("✅ Ежедневные отчёты отправлены")
        await asyncio.sleep(60)

# ----------------- Ежедневные задания на осознанность -----------------
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
        wait_seconds = (next_send - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        task = random.choice(MINDFULNESS_TASKS)
        for user_id in subscribed_users:
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=f"🌅 *Задание на сегодня:*\n\n{task}\n\nУдачи! Ты справишься 💛",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error("Failed to send task to %s: %s", user_id, e)
                if "blocked" in str(e).lower() or "not found" in str(e).lower():
                    subscribed_users.discard(user_id)

        logger.info("✅ Ежедневные задания отправлены")
        await asyncio.sleep(60)

# ----------------- Команда /ai с YandexGPT -----------------
async def get_ai_response(prompt: str) -> str:
    if not YC_API_KEY or not YC_FOLDER_ID:
        return "❌ ИИ не настроен. Обратитесь к разработчику."

    system_message = (
        "Ты — тёплый и мудрый наставник по осознанности, внимательности и внутреннему росту. "
        "Отвечай кратко (1–3 предложения), с заботой, без оценок. "
        "Говори как друг, который понимает. Используй мягкие метафоры и эмодзи, когда уместно."
    )

    prompt_text = f"{system_message}\n\nВопрос: {prompt}\nОтвет:"

    payload = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "temperature": 0.6,
            "maxTokens": "500"
        },
        "messages": [
            {"role": "user", "text": prompt_text}
        ]
    }

    headers = {
        "Authorization": f"Api-Key {YC_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with ClientSession() as session:
            async with session.post(YC_API_URL, json=payload, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("YandexGPT error %d: %s", resp.status, error_text)
                    return "🧠 Извини, не могу подключиться к ИИ. Попробуй позже."
                data = await resp.json()
                return data["result"]["alternatives"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("YandexGPT request failed: %s", e)
        return "🧠 Извини, произошла ошибка при общении с ИИ."

async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text(
            "💭 Напиши после команды вопрос, например:\n"
            "`/ai Как быть спокойнее в стрессе?`\n\n"
            "Я отвечу с позиции осознанности и заботы о себе.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("🧠 Думаю...")
    response = await get_ai_response(query)
    await update.message.reply_text(response)

# ----------------- Команды -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states.pop(user_id, None)

    if user_id in active_fitness_sessions:
        start_time = active_fitness_sessions[user_id]
        await update.message.reply_text(
            f"⚠️ У вас уже запущена тренировка с {start_time.strftime('%H:%M')}!\n"
            "Не забудьте завершить её кнопкой «🏁 Закончить тренировку».\n\n"
            "Привет! Давай развиваться вместе 🌱",
            reply_markup=main_keyboard()
        )
    else:
        await update.message.reply_text(
            "Привет! Давай развиваться вместе 🌱\n"
            "Используй кнопки ниже, чтобы отмечать осознанность, тренировки и смотреть прогресс.",
            reply_markup=main_keyboard()
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = {"menu": "stat_category"}
    await update.message.reply_text("Выберите категорию статистики:", reply_markup=stat_category_keyboard())

async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in active_fitness_sessions:
        start_time = active_fitness_sessions[user_id]
        duration = now_moscow() - start_time
        await update.message.reply_text(
            f"🏋️‍♂️ Тренировка запущена с {start_time.strftime('%H:%M')}\n"
            f"⏱ Длительность: {str(duration).split('.')[0]}"
        )
    else:
        await update.message.reply_text("Нет активной тренировки.")

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in subscribed_users:
        await update.message.reply_text("✅ Вы уже подписаны на ежедневные задания.")
    else:
        subscribed_users.add(user_id)
        await update.message.reply_text(
            "✅ Подписка активирована!\n"
            "Каждый день в 10:00 по Москве вы будете получать задание на осознанность 🌱"
        )

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in subscribed_users:
        subscribed_users.remove(user_id)
        await update.message.reply_text("❌ Подписка отменена. Больше заданий не будет.")
    else:
        await update.message.reply_text("Вы не были подписаны.")

# ----------------- Обработка сообщений -----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    state = user_states.get(user_id, {})

    if state.get("awaiting_note_confirm"):
        if text == "📝 Записать заметку":
            user_states[user_id] = {
                "awaiting_note": True,
                "session_type": state["session_type"],
                "session_time": state["session_time"],
                "duration": state.get("duration")
            }
            await update.message.reply_text(
                "Напишите заметку:",
                reply_markup=note_input_keyboard()
            )
            return
        elif text == "❌ Отменить":
            user_states.pop(user_id, None)
            await update.message.reply_text("Действие отменено.", reply_markup=main_keyboard())
            return
        else:
            await update.message.reply_text("Пожалуйста, выберите действие.", reply_markup=note_confirm_keyboard())
            return

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

    # Основные кнопки
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
        await update.message.reply_text("Хотите записать заметку об осознанности?", reply_markup=note_confirm_keyboard())
        return

    if text == "⏱ Начать тренировку":
        if user_id in active_fitness_sessions:
            await update.message.reply_text("Тренировка уже запущена! Сначала завершите текущую.", reply_markup=main_keyboard())
            return
        start_time = now_moscow()
        active_fitness_sessions[user_id] = start_time
        user_states[user_id] = {
            "awaiting_note_confirm": True,
            "session_type": "fitness",
            "session_time": start_time,
            "duration": None
        }
        await update.message.reply_text(
            f"✅ Тренировка начата в {start_time.strftime('%H:%M')}!\n"
            "Не забудьте нажать «🏁 Закончить тренировку», когда закончите.",
            reply_markup=note_confirm_keyboard()
        )
        return

    if text == "🏁 Закончить тренировку":
        start_time = active_fitness_sessions.get(user_id)
        if not start_time:
            await update.message.reply_text("Тренировка не была начата.", reply_markup=main_keyboard())
            return
        end_time = now_moscow()
        duration = end_time - start_time
        del active_fitness_sessions[user_id]
        user_states[user_id] = {
            "awaiting_note_confirm": True,
            "session_type": "fitness",
            "session_time": start_time,
            "duration": duration
        }
        await update.message.reply_text(
            f"🎉 Тренировка завершена!\n"
            f"⏱ Начало: {start_time.strftime('%H:%M')}\n"
            f"⏱ Окончание: {end_time.strftime('%H:%M')}\n"
            f"⏱ Длительность: {str(duration).split('.')[0]}\n"
            "Хотите записать заметку?",
            reply_markup=note_confirm_keyboard()
        )
        return

    if text == "📊 Статистика":
        user_states[user_id] = {"menu": "stat_category"}
        await update.message.reply_text("Выберите категорию статистики:", reply_markup=stat_category_keyboard())
        return

    if text == "🔙 Назад":
        user_states.pop(user_id, None)
        await update.message.reply_text("Вернулись в главное меню.", reply_markup=main_keyboard())
        return

    # Обработка выбора статистики
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
            note = s.get("note", "").strip()
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

    await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=main_keyboard())

# ----------------- Запуск бота -----------------
async def run_bot():
    load_data()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("active", active_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()

    # Фоновые задачи
    app.create_task(run_webserver())
    app.create_task(self_pinger())
    app.create_task(fitness_auto_finish_checker(app))
    app.create_task(fitness_reminder_checker(app))
    app.create_task(daily_cleanup())
    app.create_task(periodic_save())
    app.create_task(daily_report(app))
    app.create_task(daily_task_sender(app))

    await app.updater.start_polling()
    logger.info("🚀 Бот запущен и работает")

    await asyncio.Event().wait()

async def main():
    while True:
        try:
            await run_bot()
        except Exception as e:
            logger.exception("💥 Бот упал, перезапуск через %d сек...", RESTART_DELAY_SECONDS)
            await asyncio.sleep(RESTART_DELAY_SECONDS)

if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(main())
    else:
        loop.create_task(main())
        loop.run_forever()
