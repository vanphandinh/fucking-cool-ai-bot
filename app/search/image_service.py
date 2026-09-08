"""Image search routing — unified SEARCH_BACKEND policy."""

from __future__ import annotations

import logging

from ..config import Settings
from .ddgs_backend import search_ddgs_images
from .searxng_backend import search_searxng_images

logger = logging.getLogger(__name__)


class ImageSearchError(Exception):
    pass


async def search_images(
    query: str,
    settings: Settings,
    limit: int | None = None,
) -> list[dict]:
    """Search Internet images with the same backend policy as text search."""
    effective_limit = min(
        limit or settings.image_search_max_results,
        settings.image_search_max_results,
    )
    backend = settings.search_backend.strip().lower()

    if backend == "searxng":
        try:
            return await search_searxng_images(query, settings, effective_limit)
        except Exception as exc:  # noqa: BLE001
            raise _image_search_error(backend, exc) from exc

    if backend == "ddgs":
        try:
            return await search_ddgs_images(query, settings, effective_limit)
        except Exception as exc:  # noqa: BLE001
            raise _image_search_error(backend, exc) from exc

    # auto: prefer self-hosted SearXNG when configured, then degrade to DDGS.
    if settings.searxng_url.strip():
        try:
            results = await search_searxng_images(query, settings, effective_limit)
            if results:
                return results
        except Exception as exc:  # noqa: BLE001
            logger.warning("SearXNG image search lỗi, fallback DDGS: %s", exc)

    try:
        return await search_ddgs_images(query, settings, effective_limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DDGS image search lỗi: %s", exc)
        raise ImageSearchError(
            "Không tìm kiếm được hình ảnh từ các backend miễn phí hiện tại."
        ) from exc


def _image_search_error(backend: str, exc: Exception) -> ImageSearchError:
    logger.warning("Image search backend '%s' lỗi: %s", backend, exc)
    return ImageSearchError(f"Không tìm kiếm được hình ảnh (backend {backend} lỗi: {exc}).")
