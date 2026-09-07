"""Điểm khởi động bot."""

from __future__ import annotations

import asyncio
import logging
import signal
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
            "Chưa cấu hình API key nào (GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY)."
        )
        return 1

    if not settings.allowed_group_ids_list and not settings.learn_group_id_mode:
        logger.warning(
            "ALLOWED_GROUP_IDS đang TRỐNG và LEARN_GROUP_ID_MODE=0 -> bot sẽ không trả lời "
            "ở bất kỳ đâu. Bật LEARN_GROUP_ID_MODE=1 (lần đầu) để học chat_id group."
        )
    if settings.search_backend == "searxng" and not settings.searxng_url.strip():
        logger.warning(
            "SEARCH_BACKEND=searxng nhưng SEARXNG_URL đang TRỐNG — tìm kiếm web sẽ lỗi "
            "khi được gọi. Khởi động SearXNG (docker compose --profile searxng) và điền URL."
        )
    if settings.search_backend == "tavily" and not settings.tavily_api_key.strip():
        logger.warning(
            "SEARCH_BACKEND=tavily nhưng TAVILY_API_KEY đang TRỐNG — tìm kiếm web sẽ lỗi "
            "khi được gọi. Điền key Tavily vào .env."
        )

    # Bot() tự validate cú pháp token và có thể ném TokenValidationError ngay tại
    # constructor (trước get_me). Bắt riêng để startup fail-fast sạch, không traceback
    # và không tạo provider client rồi bỏ quên chưa đóng.
    try:
        bot = Bot(token=settings.bot_token, default=DefaultBotProperties())
    except TokenValidationError as exc:
        logger.error("BOT_TOKEN không đúng định dạng: %s", exc)
        return 1

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
        # Luôn lấy username THẬT từ Telegram làm nguồn chuẩn cho @mention
        # (BOT_USERNAME trong .env chỉ là fallback, tránh sai typo làm hỏng trigger).
        settings.bot_username = me.username or settings.bot_username
    except TelegramAPIError as exc:
        logger.error("BOT_TOKEN không hợp lệ hoặc bot bị chặn: %s", exc)
        await bot.session.close()
        await _close_providers(provider_router)
        return 1

    logger.info(
        "Providers: %s | Search: %s | Allowed groups: %s | Admin: %s | "
        "Context turns: %s | Learn-mode: %s",
        ", ".join(settings.configured_provider_names) or "-",
        settings.search_backend,
        settings.allowed_group_ids_list or "-",
        settings.admin_ids_list or "-",
        settings.max_context_turns,
        settings.learn_group_id_mode,
    )

    try:
        # Xoá webhook cũ (nếu có) trước khi polling — nếu lỗi thì polling sẽ báo 409
        # và tiến trình khởi động lại, nên không cần coi đây là lỗi chí mạng.
        await bot.delete_webhook(drop_pending_updates=True)
    except TelegramAPIError as exc:
        logger.warning("Không xoá được webhook cũ: %s", exc)

    # Dừng sạch sẽ khi nhận SIGINT/SIGTERM (Ctrl+C, docker stop) — đóng session
    # và provider thay vì để tiến trình bị kill giữa chừng.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    installed_signals: list[int] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            installed_signals.append(sig)
        except NotImplementedError:  # nền tảng không hỗ trợ (vd Windows)
            pass

    polling_task = asyncio.create_task(
        dp.start_polling(
            bot,
            # my_chat_member là sự kiện bot bị thêm/gỡ khỏi chat (dùng để tự rời
            # group lạ); chat_member (thành viên khác) không dùng tới nên không nhận.
            allowed_updates=["message", "my_chat_member"],
        )
    )
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        await asyncio.wait({polling_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if not polling_task.done():
            logger.info("Nhận tín hiệu dừng — đang tắt bot...")
            polling_task.cancel()
        stop_task.cancel()  # luôn huỷ — tránh gather chờ vô hạn nếu polling lỗi trước
        await asyncio.gather(polling_task, stop_task, return_exceptions=True)
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        await bot.session.close()
        await _close_providers(provider_router)

    # Polling tự kết thúc do lỗi (không phải tín hiệu dừng) -> ném lại để log rõ.
    if polling_task.done() and not polling_task.cancelled():
        poll_error = polling_task.exception()
        if poll_error:
            raise poll_error
    return 0


def main() -> None:
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError & lỗi đọc .env
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
