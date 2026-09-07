"""Điểm khởi động bot."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError

from .ai.router import build_provider_router
from .bot.handlers import build_lifecycle_router, build_message_router
from .config import Settings, get_settings
from .core.context import ChatMemory
from .core.orchestrator import Orchestrator
from .core.rate_limiter import RateLimiter
from .core.stats import Stats

logger = logging.getLogger(__name__)


async def _amain(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not settings.bot_token:
        logger.error("Thiếu BOT_TOKEN trong .env — bot không thể chạy.")
        return
    if not settings.configured_provider_names:
        logger.error(
            "Chưa cấu hình API key nào (GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY)."
        )
        return

    if not settings.allowed_group_ids_list and not settings.learn_group_id_mode:
        logger.warning(
            "ALLOWED_GROUP_IDS đang TRỐNG và LEARN_GROUP_ID_MODE=0 -> bot sẽ không trả lời "
            "ở bất kỳ đâu. Bật LEARN_GROUP_ID_MODE=1 (lần đầu) để học chat_id group."
        )

    provider_router = build_provider_router(settings)
    stats = Stats()
    memory = ChatMemory(max_turns_per_chat=settings.max_context_turns)
    limiter = RateLimiter(max_requests_per_min=settings.max_questions_per_min_per_user)
    orchestrator = Orchestrator(settings, provider_router, bot_display_name=settings.bot_username)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties())
    dp = Dispatcher()

    dp.include_router(build_message_router(settings, orchestrator, memory, limiter, stats))
    dp.include_router(build_lifecycle_router(settings))

    try:
        me = await bot.get_me()
        logger.info("Kết nối Telegram OK — bot @%s (%s)", me.username, me.first_name)
    except TelegramAPIError as exc:
        logger.error("BOT_TOKEN không hợp lệ hoặc bot bị chặn: %s", exc)
        await bot.session.close()
        return

    logger.info(
        "Providers: %s | Search: %s | Allowed groups: %s | Admin: %s | Context turns: %s",
        ", ".join(settings.configured_provider_names) or "-",
        settings.search_backend,
        settings.allowed_group_ids_list or "-",
        settings.admin_ids_list or "-",
        settings.max_context_turns,
    )

    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "chat_member"],
        )
    finally:
        await bot.session.close()


def main() -> None:
    settings = get_settings()
    try:
        asyncio.run(_amain(settings))
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
