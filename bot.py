import os
import logging
import tempfile
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    init_db, save_note, get_notes, get_notes_count, clear_notes,
    get_all_user_ids, set_auto_daily, get_auto_daily, CATEGORIES,
    set_timezone, get_timezone, save_reminder, get_pending_reminders,
    get_user_reminders, delete_reminder, update_reminder_next,
)
from ai import (
    generate_daily_report,
    generate_reminders,
    generate_weekly_review,
    process_custom_request,
    analyze_photo,
    transcribe_audio,
    parse_reminder,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ALLOWED_USER_IDS: set[int] = set()
raw = os.getenv("ALLOWED_USER_IDS", "")
if raw:
    ALLOWED_USER_IDS = {int(uid.strip()) for uid in raw.split(",") if uid.strip()}

# Timezone offset for auto-daily (UTC+3 = Moscow, so 22:00 MSK = 19:00 UTC)
AUTO_DAILY_HOUR_UTC = int(os.getenv("AUTO_DAILY_HOUR_UTC", "19"))

TIMEZONE_ALIASES = {
    "cet": "Europe/Berlin", "cest": "Europe/Berlin",
    "msk": "Europe/Moscow",
    "est": "America/New_York", "edt": "America/New_York",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "gmt": "UTC", "utc": "UTC",
}


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "Привет! Я бот для заметок.\n\n"
        "Просто кидай мне любой текст — я всё сохраню. "
        "Когда нужен отчёт или напоминания — вызови команду.\n\n"
        "Напиши /help чтобы узнать как я работаю."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "📋 *Как работает бот*\n\n"
        "*Шаг 1:* Кидай мне любой текст — заметки, мысли, задачи, идеи.\n\n"
        "*Категории:* Добавь тег к заметке для категоризации:\n"
        "  `встреча с Петей #работа`\n"
        "  `купить продукты #личное`\n"
        "  `сделать кэширование #идея`\n"
        "Доступные категории: `#работа` `#личное` `#идея`\n\n"
        "*Шаг 2:* Вызови команду:\n\n"
        "📊 /daily — дейли отчёт (24ч)\n"
        "📊 /daily работа — дейли только по работе\n"
        "✅ /remind — задачи и обещания\n"
        "🗺 /review — обзор за неделю\n"
        "💬 /ask <вопрос> — вопрос по заметкам\n"
        "🔢 /count — сколько заметок\n"
        "🗑 /clear — удалить все заметки\n"
        "🔔 /autodaily — вкл/выкл авто-отчёт в 22:00\n"
        "🌍 /timezone CET — часовой пояс\n"
        "⏰ /reminders — активные напоминания\n"
        "❌ /cancel <id> — отменить напоминание\n"
        "🆔 /myid — твой Telegram ID\n\n"
        "*Напоминания:* Напиши \"напомни в 15:00 выпить таблетки\" "
        "или \"напоминай каждый день в 9:00 делать зарядку\" — "
        "бот пришлёт в нужное время.\n\n"
        "*Авто-дейли:* Каждый день в 22:00 бот сам пришлёт отчёт "
        "(если за день были заметки). Отключить: /autodaily",
        parse_mode="Markdown",
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown")


async def set_timezone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    if not context.args:
        current = await get_timezone(update.effective_user.id)
        if current:
            await update.message.reply_text(f"Текущий часовой пояс: `{current}`\nИзменить: /timezone CET", parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "Укажи часовой пояс:\n"
                "/timezone CET — Центральная Европа\n"
                "/timezone MSK — Москва\n"
                "/timezone Europe/Berlin — полное название"
            )
        return

    tz_input = context.args[0]
    tz_name = TIMEZONE_ALIASES.get(tz_input.lower(), tz_input)
    try:
        ZoneInfo(tz_name)
    except (KeyError, Exception):
        await update.message.reply_text(f"Неизвестный часовой пояс: {tz_input}")
        return

    await set_timezone(update.effective_user.id, tz_name)
    now_local = datetime.now(ZoneInfo(tz_name)).strftime("%H:%M")
    await update.message.reply_text(f"Часовой пояс установлен: `{tz_name}` (сейчас у тебя {now_local})", parse_mode="Markdown")


async def handle_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    user_id = update.effective_user.id
    text = update.message.text

    # Check for reminder keywords
    lower = text.lower().strip()
    if lower.startswith("напомни") or lower.startswith("напоминай"):
        await handle_reminder_request(update, context, text)
        return

    _, category = await save_note(user_id, text)
    total = await get_notes_count(user_id)
    cat_label = f" [{category}]" if category else ""
    await update.message.reply_text(f"✅ Сохранено{cat_label} (всего: {total})")


def _parse_category_arg(args: list[str] | None) -> str | None:
    if not args:
        return None
    tag = args[0].lower().lstrip("#")
    if tag in CATEGORIES:
        return tag
    return None


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    category = _parse_category_arg(context.args)
    label = f" ({category})" if category else ""
    await update.message.reply_text(f"Генерирую дейли отчёт{label}...")

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    notes = await get_notes(update.effective_user.id, since=since, category=category)

    if not notes:
        await update.message.reply_text("Нет заметок за последние 24 часа. Кинь мне что-нибудь сначала!")
        return

    report = await generate_daily_report(notes)
    for i in range(0, len(report), 4000):
        await update.message.reply_text(report[i : i + 4000], parse_mode="Markdown")


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    category = _parse_category_arg(context.args)
    await update.message.reply_text("Достаю напоминания...")

    notes = await get_notes(update.effective_user.id, category=category)
    if not notes:
        await update.message.reply_text("Заметок пока нет.")
        return

    result = await generate_reminders(notes)
    for i in range(0, len(result), 4000):
        await update.message.reply_text(result[i : i + 4000], parse_mode="Markdown")


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("Строю карту за неделю... Это может занять пару секунд.")

    since = datetime.now(timezone.utc) - timedelta(days=7)
    notes = await get_notes(update.effective_user.id, since=since)

    if not notes:
        await update.message.reply_text("Нет заметок за последние 7 дней.")
        return

    result = await generate_weekly_review(notes)
    for i in range(0, len(result), 4000):
        await update.message.reply_text(result[i : i + 4000], parse_mode="Markdown")


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Использование: /ask <твой вопрос по заметкам>")
        return

    question = " ".join(context.args)
    await update.message.reply_text("Думаю...")

    notes = await get_notes(update.effective_user.id)
    if not notes:
        await update.message.reply_text("Заметок пока нет.")
        return

    result = await process_custom_request(notes, question)
    for i in range(0, len(result), 4000):
        await update.message.reply_text(result[i : i + 4000], parse_mode="Markdown")


async def count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    c = await get_notes_count(update.effective_user.id)
    await update.message.reply_text(f"Всего заметок: {c}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    deleted = await clear_notes(update.effective_user.id)
    await update.message.reply_text(f"Удалено заметок: {deleted}.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("Анализирую фото...")

    user_id = update.effective_user.id
    photo = update.message.photo[-1]  # largest size
    file = await context.bot.get_file(photo.file_id)
    image_url = file.file_path  # Telegram provides a direct URL

    caption = update.message.caption
    result = await analyze_photo(image_url, caption)

    # Save photo analysis as a note
    note_text = f"[Фото] {caption}\n{result}" if caption else f"[Фото] {result}"
    _, category = await save_note(user_id, note_text)
    total = await get_notes_count(user_id)

    cat_label = f" [{category}]" if category else ""
    reply = f"{result}\n\n💾 Сохранено как заметка{cat_label} (всего: {total})"
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i : i + 4000], parse_mode="Markdown")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text("Распознаю голосовое...")

    user_id = update.effective_user.id
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

    try:
        text = await transcribe_audio(tmp_path)
    finally:
        os.unlink(tmp_path)

    note_text = f"[Голос] {text}"
    _, category = await save_note(user_id, note_text)
    total = await get_notes_count(user_id)

    cat_label = f" [{category}]" if category else ""
    reply = f"🎤 {text}\n\n✅ Сохранено{cat_label} (всего: {total})"
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i : i + 4000])


def _get_forward_source(message) -> str:
    if message.forward_from:
        name = message.forward_from.full_name
        return name
    if message.forward_from_chat:
        return message.forward_from_chat.title or "чат"
    if message.forward_sender_name:
        return message.forward_sender_name
    return "неизвестно"


async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    user_id = update.effective_user.id
    msg = update.message
    source = _get_forward_source(msg)

    # Forwarded photo
    if msg.photo:
        await msg.reply_text("Анализирую пересланное фото...")
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_url = file.file_path
        result = await analyze_photo(image_url, msg.caption)
        note_text = f"[Переслано от: {source}] [Фото] {msg.caption or ''}\n{result}"
    elif msg.text:
        note_text = f"[Переслано от: {source}] {msg.text}"
    elif msg.caption:
        note_text = f"[Переслано от: {source}] {msg.caption}"
    else:
        await msg.reply_text("Не могу сохранить это сообщение — нет текста.")
        return

    _, category = await save_note(user_id, note_text)
    total = await get_notes_count(user_id)

    cat_label = f" [{category}]" if category else ""
    reply = f"📨 Переслано от {source}\n✅ Сохранено{cat_label} (всего: {total})"
    await msg.reply_text(reply)


async def handle_reminder_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id

    tz_name = await get_timezone(user_id)
    if not tz_name:
        await update.message.reply_text(
            "Сначала укажи часовой пояс:\n"
            "/timezone CET — Центральная Европа\n"
            "/timezone MSK — Москва"
        )
        return

    await update.message.reply_text("⏳ Разбираю напоминание...")

    user_tz = ZoneInfo(tz_name)
    now_local = datetime.now(user_tz)
    current_dt_str = now_local.strftime("%Y-%m-%d %H:%M")

    parsed = await parse_reminder(text, current_dt_str)
    if not parsed or "time" not in parsed:
        _, category = await save_note(user_id, text)
        total = await get_notes_count(user_id)
        cat_label = f" [{category}]" if category else ""
        await update.message.reply_text(
            f"Не удалось разобрать напоминание, сохранено как заметка{cat_label} (всего: {total})"
        )
        return

    reminder_text = parsed["text"]
    time_str = parsed["time"]
    is_recurring = parsed.get("recurring", False)
    repeat_days = parsed.get("repeat_days")
    date_str = parsed.get("date")

    try:
        parts = time_str.split(":")
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        await update.message.reply_text("Не удалось разобрать время. Попробуй: напомни в 15:00 ...")
        return

    try:
        if is_recurring:
            target_date = now_local.date()
            target_local = datetime(target_date.year, target_date.month, target_date.day,
                                    hour, minute, tzinfo=user_tz)
            if target_local <= now_local:
                target_local += timedelta(days=1)
        else:
            if date_str:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                d = now_local.date()
            target_local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=user_tz)
            if target_local <= now_local and not date_str:
                target_local += timedelta(days=1)
    except (ValueError, TypeError):
        await update.message.reply_text("Не удалось разобрать дату. Попробуй: напомни завтра в 15:00 ...")
        return

    remind_at_utc = target_local.astimezone(timezone.utc)

    reminder_id = await save_reminder(
        user_id, reminder_text, remind_at_utc.isoformat(),
        is_recurring=is_recurring, repeat_days_left=repeat_days,
    )

    schedule_reminder_job(
        context.job_queue, reminder_id, user_id, reminder_text,
        remind_at_utc.isoformat(), is_recurring, repeat_days,
    )

    time_display = target_local.strftime("%H:%M")
    if is_recurring:
        days_info = f" в течение {repeat_days} дн." if repeat_days else ""
        await update.message.reply_text(f"⏰ {reminder_text}\n🔁 Каждый день в {time_display}{days_info}")
    else:
        date_display = target_local.strftime("%d.%m.%Y")
        await update.message.reply_text(f"⏰ {reminder_text}\n📅 {date_display} в {time_display}")


def schedule_reminder_job(job_queue, reminder_id, user_id, text,
                          remind_at_str, is_recurring, repeat_days_left):
    remind_at = datetime.fromisoformat(remind_at_str)
    if remind_at.tzinfo is None:
        remind_at = remind_at.replace(tzinfo=timezone.utc)
    delay = (remind_at - datetime.now(timezone.utc)).total_seconds()
    if delay < 1:
        delay = 1

    data = {
        "reminder_id": reminder_id,
        "user_id": user_id,
        "text": text,
        "is_recurring": is_recurring,
        "repeat_days_left": repeat_days_left,
        "remind_at": remind_at_str,
    }
    job_queue.run_once(reminder_callback, delay, data=data, name=f"reminder_{reminder_id}")


async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data

    try:
        await context.bot.send_message(
            chat_id=data["user_id"],
            text=f"⏰ Напоминание: {data['text']}",
        )
    except Exception as e:
        logger.error(f"Failed to send reminder {data['reminder_id']}: {e}")

    if data["is_recurring"]:
        days_left = data["repeat_days_left"]
        if days_left is None or days_left > 1:
            new_days_left = days_left - 1 if days_left is not None else None
            next_remind = datetime.fromisoformat(data["remind_at"]) + timedelta(days=1)
            if next_remind.tzinfo is None:
                next_remind = next_remind.replace(tzinfo=timezone.utc)
            await update_reminder_next(data["reminder_id"], next_remind.isoformat(), new_days_left)
            schedule_reminder_job(
                context.job_queue, data["reminder_id"], data["user_id"],
                data["text"], next_remind.isoformat(), True, new_days_left,
            )
        else:
            await delete_reminder(data["reminder_id"])
    else:
        await delete_reminder(data["reminder_id"])


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    user_id = update.effective_user.id
    reminders = await get_user_reminders(user_id)

    if not reminders:
        await update.message.reply_text("Нет активных напоминаний.")
        return

    tz_name = await get_timezone(user_id)
    user_tz = ZoneInfo(tz_name) if tz_name else timezone.utc

    lines = ["⏰ *Активные напоминания:*\n"]
    for r in reminders:
        remind_at = datetime.fromisoformat(r["remind_at"])
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
        local_dt = remind_at.astimezone(user_tz)
        time_str = local_dt.strftime("%d.%m %H:%M")
        recurring = " 🔁" if r["is_recurring"] else ""
        days = f" ({r['repeat_days_left']} дн.)" if r["repeat_days_left"] else ""
        lines.append(f"`{r['id']}` — {r['text']} — {time_str}{recurring}{days}")

    lines.append("\nОтменить: /cancel <id>")
    await update.message.reply_text("\n".join(lines))


async def cancel_reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Использование: /cancel <id напоминания>\nПосмотреть ID: /reminders")
        return

    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return

    deleted = await delete_reminder(reminder_id, user_id=update.effective_user.id)
    if deleted:
        jobs = context.job_queue.get_jobs_by_name(f"reminder_{reminder_id}")
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text(f"Напоминание #{reminder_id} отменено.")
    else:
        await update.message.reply_text("Напоминание не найдено.")


async def load_reminders(app):
    reminders = await get_pending_reminders()
    now = datetime.now(timezone.utc)

    for r in reminders:
        remind_at = datetime.fromisoformat(r["remind_at"])
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
        schedule_reminder_job(
            app.job_queue, r["id"], r["user_id"], r["text"],
            remind_at.isoformat(), bool(r["is_recurring"]), r["repeat_days_left"],
        )

    if reminders:
        logger.info(f"Loaded {len(reminders)} reminders from database")


async def autodaily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    user_id = update.effective_user.id
    current = await get_auto_daily(user_id)
    new_state = not current
    await set_auto_daily(user_id, new_state)
    status = "включён" if new_state else "выключен"
    await update.message.reply_text(f"Авто-дейли {status}. {'Отчёт будет приходить каждый день в 22:00.' if new_state else ''}")


# --- Auto daily job ---

async def auto_daily_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running auto-daily job...")
    user_ids = await get_all_user_ids()
    for user_id in user_ids:
        if not is_allowed(user_id):
            continue
        if not await get_auto_daily(user_id):
            continue

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        notes = await get_notes(user_id, since=since)
        if not notes:
            continue

        try:
            report = await generate_daily_report(notes)
            header = "📊 *Авто-дейли отчёт*\n\n"
            full = header + report
            for i in range(0, len(full), 4000):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=full[i : i + 4000],
                    parse_mode="Markdown",
                )
            logger.info(f"Auto-daily sent to {user_id}")
        except Exception as e:
            logger.error(f"Auto-daily failed for {user_id}: {e}")


# --- Main ---

async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in .env")

    await init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("count", count))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("autodaily", autodaily))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("timezone", set_timezone_cmd))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel_reminder_cmd))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_note))

    logger.info("Bot started!")
    async with app:
        await app.bot.set_my_commands([
            BotCommand("help", "Как работает бот"),
            BotCommand("daily", "Дейли отчёт (24ч)"),
            BotCommand("remind", "Задачи и напоминания"),
            BotCommand("review", "Обзор за неделю"),
            BotCommand("ask", "Вопрос по заметкам"),
            BotCommand("count", "Сколько заметок"),
            BotCommand("clear", "Удалить все заметки"),
            BotCommand("autodaily", "Вкл/выкл авто-отчёт"),
            BotCommand("timezone", "Часовой пояс"),
            BotCommand("reminders", "Активные напоминания"),
            BotCommand("cancel", "Отменить напоминание"),
            BotCommand("myid", "Мой Telegram ID"),
        ])

        # Schedule auto-daily at configured hour UTC (default 19:00 UTC = 22:00 MSK)
        job_queue = app.job_queue
        job_queue.run_daily(
            auto_daily_job,
            time=time(hour=AUTO_DAILY_HOUR_UTC, minute=0, tzinfo=timezone.utc),
            name="auto_daily",
        )
        logger.info(f"Auto-daily scheduled at {AUTO_DAILY_HOUR_UTC}:00 UTC")

        await load_reminders(app)

        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        import asyncio
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
