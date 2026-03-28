# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for personal note-taking with AI-powered analysis. Users send text/photo messages which are saved as notes; bot commands generate reports, reminders, and weekly reviews using OpenAI GPT-4o-mini. Interface language is Russian.

## Running

```bash
# Local (requires .env with TELEGRAM_BOT_TOKEN and OPENAI_API_KEY)
pip install -r requirements.txt
python bot.py

# Docker
docker compose up --build -d
```

No test framework is configured. No linter is configured.

## Environment Variables

- `TELEGRAM_BOT_TOKEN` — Telegram Bot API token (required)
- `OPENAI_API_KEY` — OpenAI API key (required)
- `ALLOWED_USER_IDS` — comma-separated Telegram user ID whitelist (optional, open access if unset)
- `AUTO_DAILY_HOUR_UTC` — hour for auto-daily job (default: 19, i.e. 22:00 MSK)
- `DB_PATH` — SQLite database path (default: `./notes.db`)

## Architecture

Three-file Python app with clear separation:

- **bot.py** — Telegram handlers, command routing, auto-daily scheduler. Uses `python-telegram-bot` async Application pattern. All handlers check `is_allowed()` for user whitelist. Long AI responses are chunked at 4000 chars for Telegram limits.
- **ai.py** — OpenAI API calls. `_call()` is the shared sync wrapper (despite async function signatures, the actual OpenAI call is synchronous via the `OpenAI` client). System prompt includes anti-injection rules. `analyze_photo()` uses vision API separately from the main `_call()` helper.
- **database.py** — Async SQLite via `aiosqlite`. Two tables: `notes` (user_id, text, category, created_at) and `user_settings` (auto_daily_enabled). Categories extracted from `#tag` in note text with Russian/English aliases.

## Key Patterns

- Every new DB connection is opened/closed per operation (`async with aiosqlite.connect(DB_PATH)`), no connection pooling.
- `init_db()` includes a migration-safe `ALTER TABLE` wrapped in try/except for the category column.
- Auto-daily runs via `job_queue.run_daily()` at configured UTC hour, iterating all users with the setting enabled.
- Bot uses `asyncio.Event().wait()` for the main loop with graceful shutdown on KeyboardInterrupt.

## Deployment

Push to `main` triggers GitHub Actions → SSH to VDS → `git pull` + `docker compose up --build -d`. Database persists via Docker named volume `bot-data` mounted at `/app/data`.
