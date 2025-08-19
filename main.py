import os
import logging
import random
import asyncio
import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import Forbidden, BadRequest

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Требуется переменная окружения BOT_TOKEN")

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Настройки времени
AUTO_FINISH_HOURS = 3
AUTO_FINISH_CHECK_SECONDS = 300
DAILY_REPORT_HOUR = 23

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Блокировка для thread-safe операций
data_lock = threading.Lock()

# ==================== ХРАНЕНИЕ ДАННЫХ ====================
DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 0:
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Конвертируем строки обратно в datetime
                for sessions in data.get("mindfulness_sessions", {}).values():
                    for s in sessions:
                        if isinstance(s["time"], str):
                            s["time"] = datetime.fromisoformat(s["time"])
                for sessions in data.get("fitness_sessions", {}).values():
                    for s in sessions:
                        if isinstance(s["time"], str):
                            s["time"] = datetime.fromisoformat(s["time"])
                return data
        except Exception as e:
            logger.error(f"Ошибка чтения данных: {e}")
    return {
        "mindfulness_sessions": {},
        "fitness_sessions": {},
        "active_fitness_sessions": {},
        "user_states": {}
    }

def save_data():
    with data_lock:
        def datetime_to_str(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError

        data = {
            "mindfulness_sessions": {
                str(k): [{"time": s["time"], "note": s["note"]} for s in v]
                for k, v in storage.mindfulness_sessions.items()
            },
            "fitness_sessions": {
                str(k): [
                    {"time": s["time"], "note": s["note"], "duration_seconds": s["duration_seconds"]}
                    for s in v
                ]
                for k, v in storage.fitness_sessions.items()
            },
            "active_fitness_sessions": {
                str(k): v.isoformat() for k, v in storage.active_fitness_sessions.items()
            },
            "user_states": storage.user_states
        }
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=datetime_to_str)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")

class DataStorage:
    def __init__(self):
        raw = load_data()
        self.mindfulness_sessions = raw.get("mindfulness_sessions", {})
        self.fitness_sessions = raw.get("fitness_sessions", {})
        
        # Безопасная загрузка active_fitness_sessions
        self.active_fitness_sessions = {}
        active_sessions = raw.get("active_fitness_sessions", {})
        for k, v in active_sessions.items():
            try:
                if isinstance(v, str):
                    self.active_fitness_sessions[int(k)] = datetime.fromisoformat(v)
                elif isinstance(v, datetime):
                    self.active_fitness_sessions[int(k)] = v
            except (ValueError, TypeError) as e:
                logger.error(f"Ошибка конвертации времени для пользователя {k}: {e}")
        
        self.user_states = raw.get("user_states", {})

storage = DataStorage()

# ==================== КЛАВИАТУРЫ ====================
def create_keyboard(buttons, resize=True, one_time=False):
    return ReplyKeyboardMarkup(buttons, resize_keyboard=resize, one_time_keyboard=one_time)

def main_menu():
    return create_keyboard([
        [KeyboardButton("💡 Задание"), KeyboardButton("📅 Рефлексия")],
        [KeyboardButton("✨ Я осознан!")],
        [KeyboardButton("⏱ Начать тренировку"), KeyboardButton("🏁 Закончить тренировку")],
        [KeyboardButton("📊 Статистика")]
    ])

def stats_category_menu():
    return create_keyboard([
        [KeyboardButton("📊 Статистика по осознанности")],
        [KeyboardButton("📊 Статистика по спорту")],
        [KeyboardButton("🔙 Назад")]
    ])

def stats_period_menu():
    return create_keyboard([
        [KeyboardButton("📅 За день"), KeyboardButton("📆 За неделю")],
        [KeyboardButton("🔙 Назад")]
    ])

def note_confirmation_menu():
    return create_keyboard([
        [KeyboardButton("📝 Записать заметку"), KeyboardButton("❌ Отменить")]
    ])

def note_input_menu():
    return create_keyboard([
        [KeyboardButton("❌ Пропустить заметку"), KeyboardButton("🔄 Отменить")]
    ], one_time=True)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)

def format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}ч {minutes}м {seconds}с"
    if minutes > 0:
        return f"{minutes}м {seconds}с"
    return f"{seconds}с"

# ==================== БЕЗОПАСНАЯ ОТПРАВКА СООБЩЕНИЙ ====================
async def safe_send_message(bot, user_id: int, text: str, **kwargs):
    try:
        await bot.send_message(user_id, text, **kwargs)
    except Forbidden:
        logger.warning(f"Пользователь {user_id} заблокировал бота.")
    except BadRequest as e:
        logger.error(f"Bad Request для {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка отправки {user_id}: {e}")

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage.user_states.pop(user.id, None)

    if user.id in storage.active_fitness_sessions:
        start_time = storage.active_fitness_sessions[user.id]
        await update.message.reply_text(
            f"⚠️ У вас уже запущена тренировка с {start_time.strftime('%H:%M')}!\n"
            "Не забудьте завершить её кнопкой «🏁 Закончить тренировку».\n\n"
            "Привет! Давай развиваться вместе 🌱",
            reply_markup=main_menu()
        )
    else:
        await update.message.reply_text(
            "Привет! Я бот для осознанности и тренировок. "
            "Используй кнопки ниже, чтобы отмечать свою активность.",
            reply_markup=main_menu()
        )
    save_data()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    state = storage.user_states.get(user_id, {})

    if state.get("awaiting_note"):
        await handle_note_input(update, user_id, text)
        return

    if state.get("awaiting_confirmation"):
        await handle_note_confirmation(update, user_id, text)
        return

    if text == "💡 Задание":
        await send_random_task(update)
    elif text == "📅 Рефлексия":
        await send_random_reflection(update)
    elif text == "✨ Я осознан!":
        await start_mindfulness_session(update, user_id)
    elif text == "⏱ Начать тренировку":
        await start_workout_session(update, user_id)
    elif text == "🏁 Закончить тренировку":
        await finish_workout_session(update, user_id)
    elif text == "📊 Статистика":
        await show_statistics_menu(update, user_id)
    elif text == "🔙 Назад":
        await return_to_main_menu(update, user_id)
    else:
        await handle_statistics_menus(update, user_id, text, state)

# ==================== ОБРАБОТКА СОСТОЯНИЙ ====================
async def handle_note_input(update: Update, user_id: int, text: str):
    state = storage.user_states[user_id]
    note = "Без заметки" if text in ["❌ Пропустить заметку", "🔄 Отменить"] else text

    key = str(user_id)
    if state["session_type"] == "mindfulness":
        storage.mindfulness_sessions.setdefault(key, []).append({
            "time": state["session_time"],
            "note": note
        })
    else:
        duration = state["duration"]
        storage.fitness_sessions.setdefault(key, []).append({
            "time": state["session_time"],
            "note": note,
            "duration_seconds": int(duration.total_seconds()) if duration else 0
        })

    storage.user_states.pop(user_id, None)
    message = f"✅ Заметка сохранена: «{note}»" if note != "Без заметки" else "Сессия сохранена без заметки."
    await update.message.reply_text(message, reply_markup=main_menu())
    save_data()

async def handle_note_confirmation(update: Update, user_id: int, text: str):
    state = storage.user_states[user_id]

    if text == "📝 Записать заметку":
        storage.user_states[user_id] = {
            "awaiting_note": True,
            "session_type": state["session_type"],
            "session_time": state["session_time"],
            "duration": state.get("duration")
        }
        await update.message.reply_text("Напишите заметку:", reply_markup=note_input_menu())
    elif text == "❌ Отменить":
        storage.user_states.pop(user_id, None)
        await update.message.reply_text("Действие отменено.", reply_markup=main_menu())
    else:
        await update.message.reply_text("Пожалуйста, выберите действие.", reply_markup=note_confirmation_menu())
    save_data()

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================
async def send_random_task(update: Update):
    tasks = [
        "Задача: остановись на 60 секунд и почувствуй тело.",
        "Задача: сделай 10 глубоких вдохов.",
        "Задача: послушай звуки вокруг тебя."
    ]
    await update.message.reply_text(random.choice(tasks))

async def send_random_reflection(update: Update):
    reflections = [
        "Рефлексия: что ты заметил сегодня?",
        "Рефлексия: чего ты добился на этой неделе?"
    ]
    await update.message.reply_text(random.choice(reflections))

async def start_mindfulness_session(update: Update, user_id: int):
    storage.user_states[user_id] = {
        "awaiting_confirmation": True,
        "session_type": "mindfulness",
        "session_time": now_moscow(),
        "duration": None
    }
    await update.message.reply_text("Хотите записать заметку об осознанности?", reply_markup=note_confirmation_menu())
    save_data()

async def start_workout_session(update: Update, user_id: int):
    if user_id in storage.active_fitness_sessions:
        await update.message.reply_text("Тренировка уже запущена! Сначала завершите текущую.", reply_markup=main_menu())
        return

    start_time = now_moscow()
    storage.active_fitness_sessions[user_id] = start_time
    storage.user_states[user_id] = {
        "awaiting_confirmation": True,
        "session_type": "fitness",
        "session_time": start_time,
        "duration": None
    }
    await update.message.reply_text(
        f"✅ Тренировка начата в {start_time.strftime('%H:%M')}!",
        reply_markup=note_confirmation_menu()
    )
    save_data()

async def finish_workout_session(update: Update, user_id: int):
    start_time = storage.active_fitness_sessions.pop(user_id, None)
    if not start_time:
        await update.message.reply_text("Тренировка не была начата.", reply_markup=main_menu())
        return

    duration = now_moscow() - start_time
    storage.user_states[user_id] = {
        "awaiting_confirmation": True,
        "session_type": "fitness",
        "session_time": start_time,
        "duration": duration
    }
    await update.message.reply_text(
        f"🎉 Тренировка завершена!\n"
        f"⏱ Длительность: {format_duration(int(duration.total_seconds()))}\n"
        "Хотите записать заметку?",
        reply_markup=note_confirmation_menu()
    )
    save_data()

async def show_statistics_menu(update: Update, user_id: int):
    storage.user_states[user_id] = {"menu": "stat_category"}
    await update.message.reply_text("Выберите категорию статистики:", reply_markup=stats_category_menu())
    save_data()

async def return_to_main_menu(update: Update, user_id: int):
    storage.user_states.pop(user_id, None)
    await update.message.reply_text("Главное меню:", reply_markup=main_menu())
    save_data()

# ==================== ОБРАБОТКА СТАТИСТИКИ ====================
async def handle_statistics_menus(update: Update, user_id: int, text: str, state: dict):
    if state.get("menu") == "stat_category":
        await handle_stat_category(update, user_id, text)
    elif state.get("menu") == "stat_period":
        await handle_stat_period(update, user_id, text, state)
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=main_menu())

async def handle_stat_category(update: Update, user_id: int, text: str):
    if text == "📊 Статистика по осознанности":
        storage.user_states[user_id] = {"menu": "stat_period", "stat_category": "mindfulness"}
        await update.message.reply_text("Выберите период:", reply_markup=stats_period_menu())
    elif text == "📊 Статистика по спорту":
        storage.user_states[user_id] = {"menu": "stat_period", "stat_category": "fitness"}
        await update.message.reply_text("Выберите период:", reply_markup=stats_period_menu())
    else:
        await update.message.reply_text("Выберите из меню.", reply_markup=stats_category_menu())
    save_data()

async def handle_stat_period(update: Update, user_id: int, text: str, state: dict):
    if text == "🔙 Назад":
        storage.user_states[user_id] = {"menu": "stat_category"}
        await update.message.reply_text("Выберите категорию:", reply_markup=stats_category_menu())
        save_data()
        return

    now = now_moscow()
    if text == "📅 За день":
        period_start = now - timedelta(days=1)
    elif text == "📆 За неделю":
        period_start = now - timedelta(days=7)
    else:
        await update.message.reply_text("Выберите из меню.", reply_markup=stats_period_menu())
        return

    cat = state["stat_category"]
    sessions_dict = storage.mindfulness_sessions if cat == "mindfulness" else storage.fitness_sessions
    user_sessions = sessions_dict.get(str(user_id), [])
    title = "осознанности" if cat == "mindfulness" else "спорта"

    filtered = [s for s in user_sessions if s["time"] >= period_start]
    if not filtered:
        await update.message.reply_text(f"За выбранный период нет данных по {title}.", reply_markup=main_menu())
        storage.user_states.pop(user_id, None)
        save_data()
        return

    msg = format_statistics_message(filtered, period_start, now, title, cat)
    await update.message.reply_text(msg, reply_markup=main_menu(), parse_mode="Markdown")
    storage.user_states.pop(user_id, None)
    save_data()

def format_statistics_message(sessions, period_start, now, title, cat):
    msg = (f"📊 *Статистика по {title}* за период с {period_start.strftime('%d.%m.%Y')} "
           f"по {now.strftime('%d.%m.%Y')}:\n🔢 Всего сессий: {len(sessions)}\n\n")

    for s in sessions:
        time_str = s["time"].strftime("%d.%m %H:%M")
        note = s.get("note", "").strip()
        dur = s.get("duration_seconds")
        dur_str = f"⏱ {format_duration(dur)}" if dur else ""

        entry = f"🔹 *{time_str}*"
        if dur_str:
            entry += f" | {dur_str}"
        entry += "\n"
        if note and note != "Без заметки":
            entry += f"  📝 _{note}_"
        else:
            entry += f"  💬 _Без заметки_"
        msg += entry + "\n\n"

    return msg

# ==================== ФОНОВЫЕ ЗАДАЧИ ====================
async def fitness_auto_finish_checker(app):
    while True:
        try:
            now = now_moscow()
            users_to_remove = []
            for user_id, start_time in list(storage.active_fitness_sessions.items()):
                if now - start_time > timedelta(hours=AUTO_FINISH_HOURS):
                    duration = int((now - start_time).total_seconds())
                    storage.fitness_sessions.setdefault(str(user_id), []).append({
                        "time": start_time,
                        "note": "Авто-завершение",
                        "duration_seconds": duration
                    })
                    users_to_remove.append(user_id)
                    await safe_send_message(
                        app.bot, user_id,
                        f"⏳ Тренировка автоматически завершена после {AUTO_FINISH_HOURS} часов"
                    )
            
            for user_id in users_to_remove:
                storage.active_fitness_sessions.pop(user_id, None)
            
            if users_to_remove:
                save_data()
                
            await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)
        except Exception as e:
            logger.error(f"Ошибка в auto-finish: {e}")
            await asyncio.sleep(10)

async def daily_report(app):
    while True:
        try:
            now = now_moscow()
            target_time = now.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
            if now > target_time:
                target_time += timedelta(days=1)
            
            wait_seconds = (target_time - now).total_seconds()
            logger.info(f"Ожидание ежедневного отчета: {wait_seconds} секунд")
            await asyncio.sleep(wait_seconds)

            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            for user_id_str in list(storage.mindfulness_sessions.keys()):
                user_id = int(user_id_str)
                mindful_today = len([s for s in storage.mindfulness_sessions.get(user_id_str, []) 
                                  if s["time"] >= today_start])
                fitness_today = [s for s in storage.fitness_sessions.get(user_id_str, []) 
                               if s["time"] >= today_start]
                total_duration = sum(s.get("duration_seconds", 0) for s in fitness_today)

                if mindful_today or fitness_today:
                    await safe_send_message(
                        app.bot, user_id,
                        f"🌙 *Ежедневный отчёт*\n\n"
                        f"✨ Осознанность: {mindful_today} раз\n"
                        f"🏋️‍♂️ Тренировок: {len(fitness_today)}\n"
                        f"⏱ Время тренировок: {format_duration(total_duration)}",
                        parse_mode="Markdown"
                    )
        except Exception as e:
            logger.error(f"Ошибка в ежедневном отчёте: {e}")
            await asyncio.sleep(60)

# ==================== ЗАПУСК БОТА ====================
async def main():
    # Создаем приложение
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем фоновые задачи
    asyncio.create_task(fitness_auto_finish_checker(app))
    asyncio.create_task(daily_report(app))

    logger.info("✅ Бот запущен и работает 24/7")
    
    # Запускаем polling
    await app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

# =============== ЗАПУСК ДЛЯ RENDER ===============
if __name__ == "__main__":
    # Для Render используем простой запуск
    try:
        logger.info("🚀 Запуск бота как Background Worker на Render...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        save_data()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        save_data()
