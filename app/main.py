"""Điểm khởi động bot."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.token import TokenValidationError

from .ai.capabilities import ProviderCapabilities
from .ai.router import build_provider_router
from .bot.handlers import build_lifecycle_router, build_message_router
from .config import Settings, get_settings
from .core.context import ChatMemory
from .core.orchestrator import Orchestrator
from .core.rate_limiter import RateLimiter
from .core.stats import Stats
from .search.crawl4ai_client import close_crawl4ai_client
from .search.runtime import close_search_runtimes

logger = logging.getLogger(__name__)


async def _close_providers(provider_router) -> None:
    for provider in provider_router.providers:
        try:
            await provider.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("Đóng provider %s lỗi (bỏ qua)", provider.name)


def _configured_provider_names(
    provider_router,
    *,
    requires_vision: bool,
) -> tuple[str, ...]:
    reporter = getattr(provider_router, "configured_provider_names", None)
    if callable(reporter):
        return tuple(reporter(requires_vision))

    names: list[str] = []
    for provider in getattr(provider_router, "providers", ()):
        capabilities = getattr(provider, "capabilities", None)
        if isinstance(capabilities, ProviderCapabilities):
            if not capabilities.accepts(
                requires_vision=requires_vision,
                image_count=1 if requires_vision else 0,
            ):
                continue
        name = str(getattr(provider, "name", "")).strip()
        if name:
            names.append(name)
    return tuple(dict.fromkeys(names))


async def _amain(settings: Settings) -> int:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not settings.bot_token:
        logger.error("Thiếu BOT_TOKEN trong .env — bot không thể chạy.")
        return 1

    provider_router = None
    bot = None
    dp = None
    try:
        try:
            provider_router = build_provider_router(settings)
        except ValueError as exc:
            logger.error("Cấu hình AI provider không hợp lệ: %s", exc)
            return 1

        text_names = _configured_provider_names(
            provider_router,
            requires_vision=False,
        )
        vision_names = _configured_provider_names(
            provider_router,
            requires_vision=True,
        )
        if not text_names:
            logger.error(
                "Không có AI provider text nào được cấu hình hợp lệ theo "
                "TEXT_PROVIDER_ORDER."
            )
            return 1
        if settings.vision_enabled and not vision_names:
            logger.warning(
                "Vision đang bật nhưng không có AI provider vision nào được cấu hình hợp lệ; "
                "text bot vẫn hoạt động."
            )

        if not settings.allowed_group_ids_list and not settings.learn_group_id_mode:
            logger.warning(
                "ALLOWED_GROUP_IDS đang TRỐNG và LEARN_GROUP_ID_MODE=0 -> "
                "bot sẽ không trả lời ở bất kỳ đâu."
            )
        if settings.search_backend == "searxng" and not settings.searxng_url.strip():
            logger.warning("SEARCH_BACKEND=searxng nhưng SEARXNG_URL đang TRỐNG.")
        if settings.crawl4ai_enabled and not settings.crawl4ai_url.strip():
            logger.warning(
                "CRAWL4AI_ENABLED=1 nhưng CRAWL4AI_URL đang trống; dùng generic reader."
            )
        if settings.crawl4ai_enabled and not settings.crawl4ai_api_token.strip():
            logger.warning(
                "CRAWL4AI_ENABLED=1 nhưng thiếu CRAWL4AI_API_TOKEN; dùng generic reader."
            )

        try:
            bot = Bot(token=settings.bot_token, default=DefaultBotProperties())
        except TokenValidationError as exc:
            logger.error("BOT_TOKEN không đúng định dạng: %s", exc)
            return 1

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
            "Text providers: %s | Vision providers: %s | Text order: %s | "
            "Vision order: %s | Search: %s | Allowed groups: %s | Admin: %s | "
            "Context turns: %s | Learn-mode: %s",
            ", ".join(text_names) or "disabled",
            ", ".join(vision_names) or "disabled",
            ", ".join(settings.text_provider_order_list) or "disabled",
            ", ".join(settings.vision_provider_order_list) or "disabled",
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
        if bot is not None:
            await bot.session.close()
        try:
            await close_crawl4ai_client()
        finally:
            try:
                await close_search_runtimes()
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
