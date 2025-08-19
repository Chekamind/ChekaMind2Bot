import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"
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

# ==================== ХРАНЕНИЕ ДАННЫХ ====================
class DataStorage:
    def __init__(self):
        self.mindfulness_sessions = {}
        self.fitness_sessions = {}
        self.active_fitness_sessions = {}
        self.user_states = {}

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

    if state["session_type"] == "mindfulness":
        storage.mindfulness_sessions.setdefault(user_id, []).append({
            "time": state["session_time"],
            "note": note
        })
    else:
        storage.fitness_sessions.setdefault(user_id, []).append({
            "time": state["session_time"],
            "note": note,
            "duration_seconds": int(state["duration"].total_seconds()) if state["duration"] else None
        })

    storage.user_states.pop(user_id, None)
    message = f"✅ Заметка сохранена: «{note}»" if note != "Без заметки" else "Сессия сохранена без заметки."
    await update.message.reply_text(message, reply_markup=main_menu())

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
        f"⏱ Длительность: {str(duration).split('.')[0]}\n"
        "Хотите записать заметку?",
        reply_markup=note_confirmation_menu()
    )

async def show_statistics_menu(update: Update, user_id: int):
    storage.user_states[user_id] = {"menu": "stat_category"}
    await update.message.reply_text("Выберите категорию статистики:", reply_markup=stats_category_menu())

async def return_to_main_menu(update: Update, user_id: int):
    storage.user_states.pop(user_id, None)
    await update.message.reply_text("Главное меню:", reply_markup=main_menu())

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

async def handle_stat_period(update: Update, user_id: int, text: str, state: dict):
    if text == "🔙 Назад":
        storage.user_states[user_id] = {"menu": "stat_category"}
        await update.message.reply_text("Выберите категорию:", reply_markup=stats_category_menu())
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
    sessions = storage.mindfulness_sessions if cat == "mindfulness" else storage.fitness_sessions
    user_sessions = sessions.get(user_id, [])
    title = "осознанности" if cat == "mindfulness" else "спорта"

    filtered = [s for s in user_sessions if s["time"] >= period_start]
    if not filtered:
        await update.message.reply_text(f"За выбранный период нет данных по {title}.", reply_markup=main_menu())
        storage.user_states.pop(user_id, None)
        return

    msg = format_statistics_message(filtered, period_start, now, title, cat)
    storage.user_states.pop(user_id, None)
    await update.message.reply_text(msg, reply_markup=main_menu(), parse_mode="Markdown")

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
        now = now_moscow()
        for user_id, start_time in list(storage.active_fitness_sessions.items()):
            if now - start_time > timedelta(hours=AUTO_FINISH_HOURS):
                duration = int((now - start_time).total_seconds())
                storage.fitness_sessions.setdefault(user_id, []).append({
                    "time": start_time,
                    "note": "Авто-завершение",
                    "duration_seconds": duration
                })
                del storage.active_fitness_sessions[user_id]
                try:
                    await app.bot.send_message(
                        user_id,
                        f"⏳ Тренировка автоматически завершена после {AUTO_FINISH_HOURS} часов"
                    )
                except Exception as e:
                    logger.error(f"Ошибка уведомления: {e}")
        await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)

async def daily_report(app):
    while True:
        now = now_moscow()
        target = now.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for user_id in list(storage.mindfulness_sessions.keys()):
            mindful_today = len([s for s in storage.mindfulness_sessions.get(user_id, []) 
                              if s["time"] >= today_start])
            fitness_today = [s for s in storage.fitness_sessions.get(user_id, []) 
                           if s["time"] >= today_start]
            total_duration = sum(s.get("duration_seconds", 0) for s in fitness_today)

            if mindful_today or fitness_today:
                try:
                    await app.bot.send_message(
                        user_id,
                        f"🌙 *Ежедневный отчёт*\n\n"
                        f"✨ Осознанность: {mindful_today} раз\n"
                        f"🏋️‍♂️ Тренировок: {len(fitness_today)}\n"
                        f"⏱ Время тренировок: {format_duration(total_duration)}",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки отчёта: {e}")

# ==================== ЗАПУСК БОТА ====================
async def main():
    app
