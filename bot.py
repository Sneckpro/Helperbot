import os
import logging
from datetime import datetime, timedelta, time, timezone

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
)
from ai import (
    generate_daily_report,
    generate_reminders,
    generate_weekly_review,
    process_custom_request,
    analyze_photo,
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
        "🆔 /myid — твой Telegram ID\n\n"
        "*Авто-дейли:* Каждый день в 22:00 бот сам пришлёт отчёт "
        "(если за день были заметки). Отключить: /autodaily",
        parse_mode="Markdown",
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой Telegram ID: `{update.effective_user.id}`", parse_mode="Markdown")


async def handle_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    user_id = update.effective_user.id
    text = update.message.text

    note_id, category = await save_note(user_id, text)
    total = await get_notes_count(user_id)
    cat_label = f" [{category}]" if category else ""
    await update.message.reply_text(f"Сохранено{cat_label} (#{note_id}, всего: {total})")


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

    photo = update.message.photo[-1]  # largest size
    file = await context.bot.get_file(photo.file_id)
    image_url = file.file_path  # Telegram provides a direct URL

    caption = update.message.caption
    result = await analyze_photo(image_url, caption)
    for i in range(0, len(result), 4000):
        await update.message.reply_text(result[i : i + 4000], parse_mode="Markdown")


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
