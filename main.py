
import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import ClientSession

# ----------------- РќР°СЃС‚СЂРѕР№РєРё -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("ERROR: РЈСЃС‚Р°РЅРѕРІРёС‚Рµ РїРµСЂРµРјРµРЅРЅСѓСЋ РѕРєСЂСѓР¶РµРЅРёСЏ BOT_TOKEN")

YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

AUTO_FINISH_HOURS = 3
AUTO_FINISH_CHECK_SECONDS = 300
CLEANUP_MAX_DAYS = 90

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- Р“Р»РѕР±Р°Р»СЊРЅС‹Рµ С…СЂР°РЅРёР»РёС‰Р° (РІ РїР°РјСЏС‚Рё) -----------------
mindfulness_sessions = {}
fitness_sessions = {}
active_fitness_sessions = {}
user_states = {}
subscribed_users = set()

# ----------------- РљР»Р°РІРёР°С‚СѓСЂС‹ -----------------
MAIN_KEYBOARD = [
    [KeyboardButton("рџ’Ў Р—Р°РґР°РЅРёРµ"), KeyboardButton("рџ“… Р РµС„Р»РµРєСЃРёСЏ")],
    [KeyboardButton("вњЁ РЇ РѕСЃРѕР·РЅР°РЅ!")],
    [KeyboardButton("вЏ± РќР°С‡Р°С‚СЊ С‚СЂРµРЅРёСЂРѕРІРєСѓ"), KeyboardButton("рџЏЃ Р—Р°РєРѕРЅС‡РёС‚СЊ С‚СЂРµРЅРёСЂРѕРІРєСѓ")],
    [KeyboardButton("рџ§  РџРѕРіРѕРІРѕСЂРёС‚СЊ СЃ РР")],
    [KeyboardButton("рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°")]
]

STAT_CATEGORY_KEYBOARD = [
    [KeyboardButton("рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РѕСЃРѕР·РЅР°РЅРЅРѕСЃС‚Рё")],
    [KeyboardButton("рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ СЃРїРѕСЂС‚Сѓ")],
    [KeyboardButton("рџ”™ РќР°Р·Р°Рґ")]
]

STAT_PERIOD_KEYBOARD = [
    [KeyboardButton("рџ“… Р—Р° РґРµРЅСЊ"), KeyboardButton("рџ“† Р—Р° РЅРµРґРµР»СЋ")],
    [KeyboardButton("рџ”™ РќР°Р·Р°Рґ")]
]

NOTE_CONFIRM_KEYBOARD = [
    [KeyboardButton("рџ“ќ Р—Р°РїРёСЃР°С‚СЊ Р·Р°РјРµС‚РєСѓ"), KeyboardButton("вќЊ РћС‚РјРµРЅРёС‚СЊ")]
]

NOTE_INPUT_KEYBOARD = [
    [KeyboardButton("вќЊ РџСЂРѕРїСѓСЃС‚РёС‚СЊ Р·Р°РјРµС‚РєСѓ"), KeyboardButton("рџ”„ РћС‚РјРµРЅРёС‚СЊ")]
]

def main_keyboard():
    return ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)

def stat_category_keyboard():
    return ReplyKeyboardMarkup(STAT_CATEGORY_KEYBOARD, resize_keyboard=True)

def stat_period_keyboard():
    return ReplyKeyboardMarkup(STAT_PERIOD_KEYBOARD, resize_keyboard=True)

def note_confirm_keyboard():
    return ReplyKeyboardMarkup(NOTE_CONFIRM_KEYBOARD, resize_keyboard=True)

def note_input_keyboard():
    return ReplyKeyboardMarkup(NOTE_INPUT_KEYBOARD, resize_keyboard=True)

# ----------------- Р’СЃРїРѕРјРѕРіР°С‚РµР»СЊРЅС‹Рµ -----------------
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
        return f"{hours}С‡ {minutes}Рј {secs}СЃ"
    if minutes:
        return f"{minutes}Рј {secs}СЃ"
    return f"{secs}СЃ"

# ----------------- Р Р°Р±РѕС‚Р° СЃ РґР°РЅРЅС‹РјРё -----------------
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
    for sess_dict in [mindfulness_sessions, fitness_sessions]:
        for user_id in list(sess_dict.keys()):
            sess_dict[user_id] = [s for s in sess_dict[user_id] if iso_to_dt(s["time"]) >= cutoff]
            if not sess_dict[user_id]:
                del sess_dict[user_id]

# ----------------- YandexGPT -----------------
async def get_ai_response(prompt: str) -> str:
    if not YC_API_KEY or not YC_FOLDER_ID:
        return "вќЊ РР РЅРµ РЅР°СЃС‚СЂРѕРµРЅ. РћР±СЂР°С‚РёС‚РµСЃСЊ Рє СЂР°Р·СЂР°Р±РѕС‚С‡РёРєСѓ."

    system_message = (
        "РўС‹ вЂ” С‚С‘РїР»С‹Р№ Рё РјСѓРґСЂС‹Р№ РЅР°СЃС‚Р°РІРЅРёРє РїРѕ РѕСЃРѕР·РЅР°РЅРЅРѕСЃС‚Рё, РІРЅРёРјР°С‚РµР»СЊРЅРѕСЃС‚Рё Рё РІРЅСѓС‚СЂРµРЅРЅРµРјСѓ СЂРѕСЃС‚Сѓ. "
        "РћС‚РІРµС‡Р°Р№ РєСЂР°С‚РєРѕ (1вЂ“3 РїСЂРµРґР»РѕР¶РµРЅРёСЏ), СЃ Р·Р°Р±РѕС‚РѕР№, Р±РµР· РѕС†РµРЅРѕРє. "
        "Р“РѕРІРѕСЂРё РєР°Рє РґСЂСѓРі, РєРѕС‚РѕСЂС‹Р№ РїРѕРЅРёРјР°РµС‚. РСЃРїРѕР»СЊР·СѓР№ РјСЏРіРєРёРµ РјРµС‚Р°С„РѕСЂС‹ Рё СЌРјРѕРґР·Рё, РєРѕРіРґР° СѓРјРµСЃС‚РЅРѕ."
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
                    return "рџ§  РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє РР."
                data = await resp.json()
                return data["result"]["alternatives"][0]["message"]["content"].strip()
    except Exception:
        return "рџ§  РџСЂРѕРёР·РѕС€Р»Р° РѕС€РёР±РєР° РїСЂРё РѕР±С‰РµРЅРёРё СЃ РР."

# ----------------- Р•Р¶РµРґРЅРµРІРЅС‹Рµ Р·Р°РґР°РЅРёСЏ -----------------
MINDFULNESS_TASKS = [
    "РЎРµРіРѕРґРЅСЏ Р·Р°РјРµС‡Р°Р№, РєР°Рє С‡Р°СЃС‚Рѕ С‚С‹ РґС‹С€РёС€СЊ. РЎРґРµР»Р°Р№ 3 РіР»СѓР±РѕРєРёС… РІРґРѕС…Р° РєР°Р¶РґС‹Р№ С‡Р°СЃ.",
    "РџРѕС‡СѓРІСЃС‚РІСѓР№ СЃРІРѕРё СЃС‚РѕРїС‹. РҐРѕРґРё Р±РѕСЃРёРєРѕРј С…РѕС‚СЏ Р±С‹ 5 РјРёРЅСѓС‚.",
    "РџРµР№ С‡Р°Р№ РёР»Рё РєРѕС„Рµ, РЅРµ РіР»СЏРґСЏ РІ С‚РµР»РµС„РѕРЅ. РџРѕС‡СѓРІСЃС‚РІСѓР№ РІРєСѓСЃ, С‚РµРјРїРµСЂР°С‚СѓСЂСѓ, Р·Р°РїР°С…."
]

async def daily_task_sender(app):
    while True:
        now = now_moscow()
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        task = random.choice(MINDFULNESS_TASKS)
        for user_id in subscribed_users:
            try:
                await app.bot.send_message(chat_id=user_id, text=f"рџЊ… Р—Р°РґР°РЅРёРµ РЅР° СЃРµРіРѕРґРЅСЏ:\n\n{task}")
            except:
                pass

# ----------------- Р•Р¶РµРґРЅРµРІРЅС‹Р№ РѕС‚С‡С‘С‚ -----------------
async def daily_report(app):
    while True:
        now = now_moscow()
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        today_start = now_moscow().replace(hour=0, minute=0, second=0)
        tomorrow_start = today_start + timedelta(days=1)

        for user_id in list(mindfulness_sessions.keys()):
            mindful_today = [s for s in mindfulness_sessions.get(user_id, []) if today_start <= iso_to_dt(s["time"]) < tomorrow_start]
            fitness_today = [s for s in fitness_sessions.get(user_id, []) if today_start <= iso_to_dt(s["time"]) < tomorrow_start]
            total_duration = sum(s.get("duration_seconds", 0) for s in fitness_today)

            if mindful_today or fitness_today:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "рџЊ™ Р•Р¶РµРґРЅРµРІРЅС‹Р№ РѕС‚С‡С‘С‚\n"
                        f"вњЁ РћСЃРѕР·РЅР°РЅРЅРѕСЃС‚СЊ: {len(mindful_today)} СЂР°Р·\n"
                        f"рџЏ‹пёЏвЂЌв™‚пёЏ РўСЂРµРЅРёСЂРѕРІРѕРє: {len(fitness_today)}\n"
                        f"вЏ± Р’СЂРµРјСЏ РІ Р·Р°Р»Рµ: {format_duration_from_seconds(total_duration) if total_duration else '0СЃ'}"
                    )
                )

# ----------------- РђРІС‚Рѕ-Р·Р°РІРµСЂС€РµРЅРёРµ С‚СЂРµРЅРёСЂРѕРІРѕРє -----------------
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
            add_fitness_session(user_id, start_time, "РђРІС‚Рѕ-Р·Р°РІРµСЂС€РµРЅРёРµ", duration)
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=f"вљ пёЏ Р’Р°С€Р° С‚СЂРµРЅРёСЂРѕРІРєР°, РЅР°С‡Р°С‚Р°СЏ РІ {start_time.strftime('%H:%M')}, Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё Р·Р°РІРµСЂС€РµРЅР°."
                )
            except:
                pass
        await asyncio.sleep(AUTO_FINISH_CHECK_SECONDS)

# ----------------- РќР°РїРѕРјРёРЅР°РЅРёРµ Рѕ РґРѕР»РіРѕР№ С‚СЂРµРЅРёСЂРѕРІРєРµ -----------------
async def fitness_reminder_checker(app):
    notified = set()
    while True:
        now = now_moscow()
        for user_id, start_time in active_fitness_sessions.items():
            duration = now - start_time
            if timedelta(hours=2) <= duration < timedelta(hours=2, minutes=6) and user_id not in notified:
                try:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text="рџ”” Р’С‹ С‚СЂРµРЅРёСЂСѓРµС‚РµСЃСЊ СѓР¶Рµ 2 С‡Р°СЃР°. РќРµ Р·Р°Р±СѓРґСЊС‚Рµ Р·Р°РІРµСЂС€РёС‚СЊ СЃРµСЃСЃРёСЋ!"
                    )
                    notified.add(user_id)
                except:
                    pass
            elif duration >= timedelta(hours=AUTO_FINISH_HOURS) or duration < timedelta(hours=2):
                notified.discard(user_id)
        await asyncio.sleep(300)

# ----------------- РћР±СЂР°Р±РѕС‚С‡РёРєРё -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_users.add(update.effective_user.id)
    await update.message.reply_text("РџСЂРёРІРµС‚! Р”Р°РІР°Р№ СЂР°Р·РІРёРІР°С‚СЊСЃСЏ РІРјРµСЃС‚Рµ рџЊ±", reply_markup=main_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id
    state = user_states.get(user_id, {})

    if state.get("awaiting_ai"):
        await update.message.reply_text("рџ§  Р”СѓРјР°СЋ...")
        response = await get_ai_response(text)
        await update.message.reply_text(response, reply_markup=main_keyboard())
        user_states.pop(user_id, None)
        return

    if text == "рџ§  РџРѕРіРѕРІРѕСЂРёС‚СЊ СЃ РР":
        user_states[user_id] = {"awaiting_ai": True}
        await update.message.reply_text("РќР°РїРёС€Рё СЃРІРѕР№ РІРѕРїСЂРѕСЃ РР рџ“ќ")
        return

    if text == "вњЁ РЇ РѕСЃРѕР·РЅР°РЅ!":
        add_mindfulness_session(user_id, now_moscow(), "Р‘РµР· Р·Р°РјРµС‚РєРё")
        await update.message.reply_text("РћС‚Р»РёС‡РЅРѕ! РЎРµСЃСЃРёСЏ СЃРѕС…СЂР°РЅРµРЅР° вњ…")
        return

    if text == "вЏ± РќР°С‡Р°С‚СЊ С‚СЂРµРЅРёСЂРѕРІРєСѓ":
        active_fitness_sessions[user_id] = now_moscow()
        await update.message.reply_text("РўСЂРµРЅРёСЂРѕРІРєР° РЅР°С‡Р°С‚Р° рџ’Є")
        return

    if text == "рџЏЃ Р—Р°РєРѕРЅС‡РёС‚СЊ С‚СЂРµРЅРёСЂРѕРІРєСѓ":
        start_time = active_fitness_sessions.pop(user_id, None)
        if not start_time:
            await update.message.reply_text("РўСЂРµРЅРёСЂРѕРІРєР° РЅРµ РЅР°Р№РґРµРЅР°.")
            return
        duration = now_moscow() - start_time
        add_fitness_session(user_id, start_time, "Р‘РµР· Р·Р°РјРµС‚РєРё", duration)
        await update.message.reply_text(f"РўСЂРµРЅРёСЂРѕРІРєР° Р·Р°РІРµСЂС€РµРЅР°! вЏ± {format_duration_from_seconds(int(duration.total_seconds()))}")
        return

    if text == "рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°":
        mindful_today = len([s for s in mindfulness_sessions.get(user_id, []) if iso_to_dt(s["time"]).date() == now_moscow().date()])
        fitness_today = fitness_sessions.get(user_id, [])
        dur = sum(s.get("duration_seconds", 0) for s in fitness_today if iso_to_dt(s["time"]).date() == now_moscow().date())
        await update.message.reply_text(
            f"рџ“… РЎРµРіРѕРґРЅСЏ:\n"
            f"вњЁ РћСЃРѕР·РЅР°РЅРЅРѕСЃС‚СЊ: {mindful_today} СЂР°Р·\n"
            f"рџЏ‹пёЏвЂЌв™‚пёЏ РўСЂРµРЅРёСЂРѕРІРѕРє: {len(fitness_today)}\n"
            f"вЏ± Р’СЂРµРјСЏ РІ Р·Р°Р»Рµ: {format_duration_from_seconds(dur) if dur else '0СЃ'}"
        )
        return

    await update.message.reply_text("РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РёСЃРїРѕР»СЊР·СѓР№ РєРЅРѕРїРєРё РјРµРЅСЋ.", reply_markup=main_keyboard())

# ----------------- Р—Р°РїСѓСЃРє Р±РѕС‚Р° -----------------
async def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.create_task(daily_report(app))
    app.create_task(daily_task_sender(app))
    app.create_task(fitness_auto_finish_checker(app))
    app.create_task(fitness_reminder_checker(app))

    await app.run_polling()

async def main():
    while True:
        try:
            await run_bot()
        except Exception as e:
            logger.exception("рџ’Ґ Р‘РѕС‚ СѓРїР°Р», РїРµСЂРµР·Р°РїСѓСЃРє...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
