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
BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"

YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

AUTO_FINISH_HOURS = 3
AUTO_FINISH_CHECK_SECONDS = 300
RESTART_DELAY_SECONDS = 5

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- Глобальные хранилища (в памяти) -----------------
mindfulness_sessions = {}   # user_id -> [{'time': dt, 'note': str}]
fitness_sessions = {}       # user_id -> [{'time': dt, 'note': str, 'duration_seconds': int}]
active_fitness_sessions = {}  # user_id -> datetime (Moscow)
user_states = {}            # user_id -> dict

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

# ----------------- Вспомогательные функции -----------------
def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)

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

# ----------------- Веб-сервер (для keep-alive) -----------------
async def handle_root(request):
    return web.Response(text="🧘 Mindfulness Bot is alive!")

async def handle_health(request):
    return web.Response(text="OK", status=200)

async def run_webserver():
    app = web.Application()
    app.add_routes([web.get("/", handle_root), web.get("/health", handle_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Веб-сервер запущен на порту {port}")

# ----------------- Самопинг (для keep-alive) -----------------
async def self_pinger():
    await asyncio.sleep(5)
    url = f"http://127.0.0.1:{os.getenv('PORT', 10000)}/"
    logger.info("🔁 Самопинг запущен: %s каждые 240 сек", url)
    async with ClientSession() as sess:
        while True:
            try:
                async with sess.get(url, timeout=10) as resp:
                    logger.debug("Ping: %d", resp.status)
            except Exception as e:
                logger.warning("Ping failed: %s", e)
            await asyncio.sleep(240)

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
            if user_id not in fitness_sessions:
                fitness_sessions[user_id] = []
            fitness_sessions[user_id].append({
                "time": start_time,
                "note": "Авто-завершение (превышено время)",
                "duration_seconds": int(duration.total_seconds())
            })
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ Ваша тренировка, начатая в {start_time.strftime('%H:%M')}, автоматически завершена после {AUTO_FINISH_HOURS} часов."
                )
            except Exception as e:
                logger.error("❌ Не удалось уведомить пользователя %s: %s", user_id, e)
        await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)

# ----------------- Ежедневный отчёт в 23:00 (статистика за день) -----------------
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
                if s["time"] >= today_start and s["time"] < tomorrow_start
            ]

            fitness_today = [
                s for s in fitness_sessions.get(user_id, [])
                if s["time"] >= today_start and s["time"] < tomorrow_start
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

# ----------------- YandexGPT: общение с ИИ -----------------
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
        "completionOptions": {
            "temperature": 0.6,
            "maxTokens": 500
        },
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]
    }

    headers = {
        "Authorization": f"Api-Key {YC_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with ClientSession() as session:
            async with session.post(YC_API_URL, json=payload, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("YandexGPT error %d: %s", resp.status, error_text)
                    return "🧠 Извини, не могу подключиться к ИИ. Попробуй позже."
                data = await resp.json()
                return data["result"]["alternatives"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("YandexGPT request failed: %s", e)
        return "🧠 Извини, произошла ошибка при общении с ИИ."

# ----------------- Команды и обработка сообщений -----------------
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    state = user_states.get(user_id, {})

    # Обработка вопроса к ИИ
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
        if user_id not in mindfulness_sessions:
            mindfulness_sessions[user_id] = []
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
        if user_id not in fitness_sessions:
            fitness_sessions[user_id] = []
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

    if text == "🧠 Поговорить с ИИ":
        user_states[user_id] = {"awaiting_ai_question": True}
        await update.message.reply_text(
            "💭 Напиши, что тебя волнует. Я постараюсь помочь с позиции осознанности.\n"
            "Например: «Как справиться с тревогой?»",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("❌ Отмена")]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
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

    # Обработка ввода заметки
    if state.get("awaiting_note"):
        note = "Без заметки" if text in ["❌ Пропустить заметку", "🔄 Отменить"] else text
        session_time = state["session_time"]
        session_type = state["session_type"]
        duration = state.get("duration")

        if session_type == "fitness":
            if user_id not in fitness_sessions:
                fitness_sessions[user_id] = []
            fitness_sessions[user_id].append({
                "time": session_time,
                "note": note,
                "duration_seconds": int(duration.total_seconds()) if duration else None
            })
        else:
            if user_id not in mindfulness_sessions:
                mindfulness_sessions[user_id] = []
            mindfulness_sessions[user_id].append({
                "time": session_time,
                "note": note
            })

        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Заметка сохранена: «{note}»" if note != "Без заметки" else "Сессия сохранена без заметки.",
            reply_markup=main_keyboard()
        )
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

        filtered = [(s["time"], s) for s in sessions if s["time"] >= period_start]
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
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()

    # Фоновые задачи
    app.create_task(run_webserver())
    app.create_task(self_pinger())
    app.create_task(fitness_auto_finish_checker(app))
    app.create_task(daily_report(app))

    # Запуск polling
    await app.run_polling()

async def main():
    while True:
        try:
            await run_bot()
        except Exception as e:
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
    except Exception as e:
        logger.exception("Fatal error in main loop")
