"""Entry point: `python -m bot`."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from pydantic import ValidationError

from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import build_router
from bot.scheduler import build_scheduler
from bot.sheets import SheetsRepo, explain_startup_error

log = logging.getLogger("bot")


def load_settings() -> Settings:
    """Load `.env`; exit with a readable message when something required is missing."""
    try:
        return Settings()
    except ValidationError as exc:
        print("Configuration error - check your .env (see .env.example):", file=sys.stderr)
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]).upper() or "settings"
            print(f"  {loc}: {err['msg']}", file=sys.stderr)
        sys.exit(2)


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    repo = SheetsRepo(settings)
    try:
        counts = await repo.ensure_schema()
    except Exception as exc:  # anything here means "cannot reach the sheet" - explain and exit
        log.debug("ensure_schema failed", exc_info=exc)
        print(
            "Cannot open the Google Sheet:\n  "
            + explain_startup_error(exc, settings, repo.service_account_email),
            file=sys.stderr,
        )
        sys.exit(2)
    log.info("sheet ready: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))

    ai = GeminiClient(
        settings.gemini_api_key, settings.gemini_vision_model, settings.gemini_text_model
    )
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    scheduler, jobs = build_scheduler(bot, repo, ai, settings)

    dp = Dispatcher(repo=repo, ai=ai, settings=settings, jobs=jobs)
    dp.include_router(build_router())

    @dp.startup()
    async def on_startup() -> None:
        me = await bot.get_me()
        log.info("starting as @%s for chats %s", me.username, sorted(settings.allowed_chat_ids))
        scheduler.start()
        await jobs.refresh_ping_jobs()

    @dp.shutdown()
    async def on_shutdown() -> None:
        scheduler.shutdown(wait=False)
        log.info("stopped")

    # start_polling installs SIGINT/SIGTERM handlers and stops gracefully (handle_signals=True).
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
