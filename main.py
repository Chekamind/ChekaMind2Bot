import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import ClientSession

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "7276083736:AAGgMbHlOo5ccEvuUV-KXuJ0i2LQlgqEG_I"
YC_API_KEY = "YCMvITcnXbFMtzgZbkrm-kd8KFW-0uZr3wd-1Bii"
YC_FOLDER_ID = "blg8phjv3u31mg7urlac"
PORT = 10000

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
YC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# ==================== НАСТРОЙКИ ====================
AUTO_FINISH_HOURS = 3
AUTO_FINISH_CHECK_SECONDS = 300
CLEANUP_MAX_DAYS = 90
DAILY_TASK_HOUR = 10
DAILY_REPORT_HOUR = 23

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ХРАНИЛИЩА ДАННЫХ ====================
mindfulness_sessions = {}
fitness_sessions = {}
active_fitness_sessions = {}
user_states = {}
subscribed_users = set()

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard():
    buttons = [
        [KeyboardButton("💡 Задание"), KeyboardButton("📅 Рефлексия")],
        [KeyboardButton("✨ Я осознан!")],
        [KeyboardButton("⏱ Начать тренировку"), KeyboardButton("🏁 Закончить тренировку")],
        [KeyboardButton("🧠 Поговорить с ИИ"), KeyboardButton("📊 Статистика")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_stat_keyboard():
    buttons = [
        [KeyboardButton("📊 За сегодня"), KeyboardButton("📈 За неделю")],
        [KeyboardButton("🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ==================== СЛУЖЕБНЫЕ ФУНКЦИИ ====================
def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)

def format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}ч {minutes}м {seconds}с"
    elif minutes > 0:
        return f"{minutes}м {seconds}с"
    return f"{seconds}с"

def cleanup_old_data():
    cutoff = now_moscow() - timedelta(days=CLEANUP_MAX_DAYS)
    
    for user_id in list(mindfulness_sessions.keys()):
        mindfulness_sessions[user_id] = [t for t in mindfulness_sessions[user_id] if t > cutoff]
        if not mindfulness_sessions[user_id]:
            del mindfulness_sessions[user_id]
    
    for user_id in list(fitness_sessions.keys()):
        fitness_sessions[user_id] = [s for s in fitness_sessions[user_id] if s['start'] > cutoff]
        if not fitness_sessions[user_id]:
            del fitness_sessions[user_id]

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    subscribed_users.add(user.id)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я твой помощник для осознанности и тренировок.",
        reply_markup=get_main_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    current_state = user_states.get(user_id, {})

    # Обработка состояний
    if current_state.get('awaiting_note'):
        if text == "❌ Пропустить заметку":
            note = "Без заметки"
        else:
            note = text
        
        if current_state['note_type'] == 'mindfulness':
            mindfulness_sessions.setdefault(user_id, []).append({
                "time": now_moscow(),
                "note": note
            })
            await update.message.reply_text("Сессия осознанности сохранена!", reply_markup=get_main_keyboard())
        
        elif current_state['note_type'] == 'fitness':
            if user_id in active_fitness_sessions:
                start_time = active_fitness_sessions.pop(user_id)
                duration = (now_moscow() - start_time).total_seconds()
                fitness_sessions.setdefault(user_id, []).append({
                    "start": start_time,
                    "duration": duration,
                    "note": note
                })
                await update.message.reply_text(
                    f"Тренировка сохранена! Длительность: {format_duration(int(duration))}",
                    reply_markup=get_main_keyboard()
                )
        
        user_states.pop(user_id, None)
        return

    if current_state.get('awaiting_ai'):
        await update.message.reply_text("🧠 Думаю...")
        response = await get_ai_response(text)
        user_states.pop(user_id, None)
        await update.message.reply_text(response, reply_markup=get_main_keyboard())
        return

    # Обработка основных команд
    if text == "✨ Я осознан!":
        user_states[user_id] = {
            'awaiting_note': True,
            'note_type': 'mindfulness'
        }
        buttons = [
            [KeyboardButton("❌ Пропустить заметку")]
        ]
        await update.message.reply_text(
            "Опишите ваше состояние (или пропустите):",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
    
    elif text == "⏱ Начать тренировку":
        active_fitness_sessions[user_id] = now_moscow()
        await update.message.reply_text("🏋️ Тренировка начата! Не забудьте завершить её.")
    
    elif text == "🏁 Закончить тренировку":
        if user_id not in active_fitness_sessions:
            await update.message.reply_text("У вас нет активной тренировки.")
            return
        
        user_states[user_id] = {
            'awaiting_note': True,
            'note_type': 'fitness'
        }
        buttons = [
            [KeyboardButton("❌ Пропустить заметку")]
        ]
        await update.message.reply_text(
            "Опишите вашу тренировку (или пропустите):",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
    
    elif text == "🧠 Поговорить с ИИ":
        user_states[user_id] = {'awaiting_ai': True}
        await update.message.reply_text("Напишите ваш вопрос ИИ:")
    
    elif text == "📊 Статистика":
        await show_statistics(update)
    
    elif text == "📊 За сегодня":
        await show_daily_stats(update)
    
    elif text == "📈 За неделю":
        await show_weekly_stats(update)
    
    elif text == "🔙 Назад":
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
    
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню", reply_markup=get_main_keyboard())

# ==================== ФУНКЦИИ СТАТИСТИКИ ====================
async def show_statistics(update: Update):
    await update.message.reply_text(
        "Выберите тип статистики:",
        reply_markup=get_stat_keyboard()
    )

async def show_daily_stats(update: Update):
    user_id = update.effective_user.id
    today = now_moscow().date()
    
    mindful_count = len([s for s in mindfulness_sessions.get(user_id, []) 
                      if s['time'].date() == today])
    
    fitness_sessions_today = [s for s in fitness_sessions.get(user_id, []) 
                            if s['start'].date() == today]
    total_duration = sum(s['duration'] for s in fitness_sessions_today)
    
    await update.message.reply_text(
        f"📊 Статистика за сегодня:\n\n"
        f"✨ Сессий осознанности: {mindful_count}\n"
        f"🏋️‍♂️ Тренировок: {len(fitness_sessions_today)}\n"
        f"⏱ Общее время тренировок: {format_duration(int(total_duration))}",
        reply_markup=get_stat_keyboard()
    )

async def show_weekly_stats(update: Update):
    user_id = update.effective_user.id
    week_ago = now_moscow() - timedelta(days=7)
    
    mindful_count = len([s for s in mindfulness_sessions.get(user_id, []) 
                      if s['time'] >= week_ago])
    
    fitness_sessions_week = [s for s in fitness_sessions.get(user_id, []) 
                           if s['start'] >= week_ago]
    total_duration = sum(s['duration'] for s in fitness_sessions_week)
    
    await update.message.reply_text(
        f"📈 Статистика за неделю:\n\n"
        f"✨ Сессий осознанности: {mindful_count}\n"
        f"🏋️‍♂️ Тренировок: {len(fitness_sessions_week)}\n"
        f"⏱ Общее время тренировок: {format_duration(int(total_duration))}",
        reply_markup=get_stat_keyboard()
    )

# ==================== YANDEX GPT ====================
async def get_ai_response(prompt: str) -> str:
    if not YC_API_KEY or not YC_FOLDER_ID:
        return "ИИ-модуль не настроен"
    
    system_prompt = (
        "Ты - дружелюбный помощник по осознанности и здоровому образу жизни. "
        "Отвечай кратко (1-3 предложения), поддерживающе, с эмодзи где уместно. "
        "Избегай сложных терминов, говори как друг."
    )
    
    payload = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "temperature": 0.6,
            "maxTokens": 500
        },
        "messages": [
            {"role": "system", "content": system_prompt},
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
                    return "😕 Не удалось получить ответ от ИИ"
                data = await resp.json()
                return data["result"]["alternatives"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}")
        return "⚠️ Произошла ошибка при запросе к ИИ"

# ==================== ФОНОВЫЕ ЗАДАЧИ ====================
async def daily_tasks(app):
    while True:
        try:
            now = now_moscow()
            # Ежедневное задание в 10:00
            if now.hour == DAILY_TASK_HOUR and now.minute == 0:
                tasks = [
                    "Сегодня попробуйте 5 минут глубокого дыхания каждый час",
                    "Попробуйте осознанную ходьбу - замечайте каждое движение",
                    "Во время еды сосредоточьтесь только на вкусе пищи"
                ]
                task = random.choice(tasks)
                for user_id in subscribed_users:
                    try:
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=f"🌞 Доброе утро! Сегодняшнее задание:\n\n{task}"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки задания: {e}")
            
            # Ежедневный отчет в 23:00
            elif now.hour == DAILY_REPORT_HOUR and now.minute == 0:
                today_start = now.replace(hour=0, minute=0, second=0)
                
                for user_id in list(mindfulness_sessions.keys()):
                    mindful_today = len([s for s in mindfulness_sessions[user_id] 
                                      if s['time'] >= today_start])
                    
                    fitness_today = [s for s in fitness_sessions.get(user_id, []) 
                                   if s['start'] >= today_start]
                    total_duration = sum(s['duration'] for s in fitness_today)
                    
                    if mindful_today or fitness_today:
                        try:
                            await app.bot.send_message(
                                chat_id=user_id,
                                text=(
                                    "🌙 Ежедневный отчет\n\n"
                                    f"✨ Сессий осознанности: {mindful_today}\n"
                                    f"🏋️‍♂️ Тренировок: {len(fitness_today)}\n"
                                    f"⏱ Общее время тренировок: {format_duration(int(total_duration))}"
                                )
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки отчета: {e}")
            
            # Автозавершение тренировок
            for user_id, start_time in list(active_fitness_sessions.items()):
                duration = now - start_time
                if duration > timedelta(hours=AUTO_FINISH_HOURS):
                    fitness_sessions.setdefault(user_id, []).append({
                        "start": start_time,
                        "duration": duration.total_seconds(),
                        "note": "Автозавершение"
                    })
                    del active_fitness_sessions[user_id]
                    try:
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=f"⏳ Тренировка автоматически завершена после {AUTO_FINISH_HOURS} часов"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка автозавершения тренировки: {e}")
            
            # Очистка старых данных
            if now.hour == 3 and now.minute == 0:  # В 3:00 ночи
                cleanup_old_data()
            
            await asyncio.sleep(60)
        
        except Exception as e:
            logger.error(f"Ошибка в фоновых задачах: {e}")
            await asyncio.sleep(60)

# ==================== ЗАПУСК БОТА ====================
async def main():
    try:
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Запуск фоновых задач
        asyncio.create_task(daily_tasks(application))
        
        # Запуск бота
        logger.info("🟢 Бот запускается...")
        await application.run_polling()
    
    except Exception as e:
        logger.error(f"🔴 Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
