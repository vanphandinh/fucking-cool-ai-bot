"""Điểm khởi động bot."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.token import TokenValidationError

from .ai.router import build_provider_router
from .bot.handlers import build_lifecycle_router, build_message_router
from .config import Settings, get_settings
from .core.context import ChatMemory
from .core.orchestrator import Orchestrator
from .core.rate_limiter import RateLimiter
from .core.stats import Stats

logger = logging.getLogger(__name__)


async def _close_providers(provider_router) -> None:
    for provider in provider_router.providers:
        try:
            await provider.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("Đóng provider %s lỗi (bỏ qua)", provider.name)


async def _amain(settings: Settings) -> int:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not settings.bot_token:
        logger.error("Thiếu BOT_TOKEN trong .env — bot không thể chạy.")
        return 1
    if not settings.configured_provider_names:
        logger.error(
            "Chưa cấu hình API key text nào (GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY)."
        )
        return 1

    if settings.cloudflare_api_token and not settings.cloudflare_account_id:
        logger.warning(
            "Có CLOUDFLARE_API_TOKEN nhưng thiếu CLOUDFLARE_ACCOUNT_ID — bỏ qua Cloudflare vision."
        )
    if settings.vision_enabled and not settings.configured_vision_provider_names:
        logger.warning("Vision đang bật nhưng chưa có vision provider hợp lệ; text bot vẫn hoạt động.")

    if not settings.allowed_group_ids_list and not settings.learn_group_id_mode:
        logger.warning(
            "ALLOWED_GROUP_IDS đang TRỐNG và LEARN_GROUP_ID_MODE=0 -> bot sẽ không trả lời ở bất kỳ đâu."
        )
    if settings.search_backend == "searxng" and not settings.searxng_url.strip():
        logger.warning("SEARCH_BACKEND=searxng nhưng SEARXNG_URL đang TRỐNG.")
    if settings.search_backend == "tavily" and not settings.tavily_api_key.strip():
        logger.warning("SEARCH_BACKEND=tavily nhưng TAVILY_API_KEY đang TRỐNG.")

    try:
        bot = Bot(token=settings.bot_token, default=DefaultBotProperties())
    except TokenValidationError as exc:
        logger.error("BOT_TOKEN không đúng định dạng: %s", exc)
        return 1

    provider_router = None
    dp = None
    try:
        provider_router = build_provider_router(settings)
        stats = Stats()
        memory = ChatMemory(max_turns_per_chat=settings.max_context_turns)
        limiter = RateLimiter(max_requests_per_min=settings.max_questions_per_min_per_user)
        orchestrator = Orchestrator(settings, provider_router)

        dp = Dispatcher()
        dp.include_router(build_message_router(settings, orchestrator, memory, limiter, stats))
        dp.include_router(build_lifecycle_router(settings))

        try:
            me = await bot.get_me()
            logger.info("Kết nối Telegram OK — bot @%s (%s)", me.username, me.first_name)
            settings.bot_username = me.username or settings.bot_username
        except TelegramAPIError as exc:
            logger.error("BOT_TOKEN không hợp lệ hoặc bot bị chặn: %s", exc)
            return 1

        logger.info(
            "Text providers: %s | Vision: %s | Search: %s | Allowed groups: %s | Admin: %s | "
            "Context turns: %s | Learn-mode: %s",
            ", ".join(settings.configured_provider_names) or "-",
            ", ".join(settings.configured_vision_provider_names) or "disabled",
            settings.search_backend,
            settings.allowed_group_ids_list or "-",
            settings.admin_ids_list or "-",
            settings.max_context_turns,
            settings.learn_group_id_mode,
        )

        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except TelegramAPIError as exc:
            logger.warning("Không xoá được webhook cũ: %s", exc)

        await dp.start_polling(
            bot,
            allowed_updates=["message", "my_chat_member"],
            close_bot_session=False,
        )
        return 0
    finally:
        if dp is not None:
            pending = tuple(dp._handle_update_tasks)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        try:
            await bot.session.close()
        finally:
            if provider_router is not None:
                await _close_providers(provider_router)


def main() -> None:
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
        logging.getLogger(__name__).error("Cấu hình không hợp lệ: %s", exc)
        raise SystemExit(1) from exc
    try:
        code = asyncio.run(_amain(settings))
    except KeyboardInterrupt:
        return
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
