"""The public web-search API owns MAX_RESULTS, not its callers.

``search()`` is the only public web entry point, so the cap has to live there:
an internal caller that passes ``limit=100`` must still receive at most
``MAX_RESULTS``, and a caller that passes ``0`` must get an empty list without a
backend round trip. Image search keeps its own separate policy.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Settings
from app.search import image_service, service
from app.search.router import route_auto
from app.search.runtime import reset_search_runtimes


def _hits(count: int, *, host: str = "site") -> list[dict]:
    return [
        {
            "title": f"result {i}",
            "url": f"https://{host}{i}.example.org/page{i}",
            "snippet": "solar capacity detail",
        }
        for i in range(count)
    ]


class EffectiveLimitTests(unittest.TestCase):
    def test_values_at_or_below_the_cap_pass_through(self):
        for requested in (1, 2, 7, service.MAX_RESULTS):
            self.assertEqual(service.effective_limit(requested), requested)

    def test_values_above_the_cap_are_clamped(self):
        for requested in (service.MAX_RESULTS + 1, 100, 10_000):
            self.assertEqual(service.effective_limit(requested), service.MAX_RESULTS)

    def test_zero_and_negative_mean_no_results(self):
        for requested in (0, -1, -100):
            self.assertEqual(service.effective_limit(requested), 0)

    def test_malformed_limit_falls_back_to_the_cap_not_unbounded(self):
        for malformed in (None, "abc", float("inf"), float("nan")):
            self.assertEqual(service.effective_limit(malformed), service.MAX_RESULTS)


class WebSearchLimitEnforcementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_search_runtimes()

    async def test_zero_limit_returns_empty_without_calling_a_backend(self):
        settings = Settings(_env_file=None, search_backend="ddgs")
        calls: list[int] = []

        async def fake_backend(query, cfg, limit):
            calls.append(limit)
            return _hits(limit)

        with patch("app.search.ddgs_backend.search_ddgs", new=fake_backend):
            result = await service.search("solar capacity", settings, 0)

        self.assertEqual(result, [])
        self.assertEqual(calls, [])

    async def test_each_limit_value_returns_at_most_the_cap(self):
        settings = Settings(_env_file=None, search_backend="ddgs")
        for requested in (1, 8, 9, 100):
            with self.subTest(requested=requested):
                seen: list[int] = []

                async def fake_backend(query, cfg, limit):
                    seen.append(limit)
                    return _hits(limit)

                with patch("app.search.ddgs_backend.search_ddgs", new=fake_backend):
                    result = await service.search(
                        f"solar capacity {requested}", settings, requested
                    )
                expected = min(requested, service.MAX_RESULTS)
                self.assertEqual(seen, [expected])
                self.assertEqual(len(result), expected)

    async def test_backend_overreturn_is_truncated_in_both_direct_branches(self):
        for backend, target in (
            ("ddgs", "app.search.ddgs_backend.search_ddgs"),
            ("searxng", "app.search.searxng_backend.search_searxng"),
        ):
            with self.subTest(backend=backend):
                settings = Settings(
                    _env_file=None,
                    search_backend=backend,
                    searxng_url="https://searx.example.org",
                )
                seen = []

                async def overreturn(query, cfg, limit):
                    seen.append(limit)
                    return _hits(20)

                with patch(target, new=overreturn):
                    result = await service.search("overreturn", settings, 100)
                self.assertEqual(seen, [service.MAX_RESULTS])
                self.assertEqual(len(result), service.MAX_RESULTS)

    async def test_route_auto_direct_web_calls_respect_cap_and_zero(self):
        for enabled in (False, True):
            with self.subTest(web_evidence_v2_enabled=enabled):
                settings = Settings(
                    _env_file=None,
                    search_backend="auto",
                    web_evidence_v2_enabled=enabled,
                )
                seen = []

                async def overreturn(query, cfg, limit):
                    seen.append(limit)
                    return _hits(100)

                options = dict(
                    kind="web",
                    result_url_key="url",
                    searx_call=None,
                    ddgs_call=overreturn,
                )
                result = await route_auto("direct web cap", settings, 100, **options)
                self.assertEqual(seen, [service.MAX_RESULTS])
                self.assertEqual(len(result), service.MAX_RESULTS)
                empty = await route_auto("direct web zero", settings, 0, **options)
                self.assertEqual(empty, [])
                self.assertEqual(seen, [service.MAX_RESULTS])

    async def test_searxng_branch_is_clamped_at_the_same_boundary(self):
        settings = Settings(
            _env_file=None,
            search_backend="searxng",
            searxng_url="https://searx.example.org",
        )
        seen: list[int] = []

        async def fake_searx(query, cfg, limit):
            seen.append(limit)
            return _hits(limit, host="searx")

        with patch("app.search.searxng_backend.search_searxng", new=fake_searx):
            result = await service.search("solar capacity", settings, 100)

        self.assertEqual(seen, [service.MAX_RESULTS])
        self.assertEqual(len(result), service.MAX_RESULTS)

    async def test_auto_branch_clamps_and_keeps_diverse_urls_under_a_flood(self):
        settings = Settings(
            _env_file=None,
            search_backend="auto",
            searxng_url="https://searx.example.org",
            web_evidence_v2_enabled=True,
        )
        flood = [
            {
                "title": f"duplicate {i}",
                "url": "https://dup.example.org/same-page",
                "snippet": "solar capacity detail",
            }
            for i in range(24)
        ]

        async def fake_searx(query, cfg, limit):
            return [dict(item) for item in flood]

        async def fake_ddgs(query, cfg, limit):
            return _hits(8, host="independent")

        with (
            patch("app.search.searxng_backend.search_searxng", new=fake_searx),
            patch("app.search.ddgs_backend.search_ddgs", new=fake_ddgs),
        ):
            result = await service.search("solar capacity", settings, 100)

        self.assertLessEqual(len(result), service.MAX_RESULTS)
        urls = {str(item.get("url")) for item in result}
        self.assertTrue(
            any("independent" in url for url in urls),
            f"a duplicate flood evicted the diverse backend: {urls}",
        )

    async def test_image_search_keeps_its_own_policy_and_cap(self):
        settings = Settings(_env_file=None, search_backend="ddgs")
        seen: list[int] = []

        async def fake_images(query, cfg, limit):
            seen.append(limit)
            return [
                {"image_url": f"https://img.example.org/{i}.jpg", "title": "tree"}
                for i in range(limit)
            ]

        with patch("app.search.image_service.search_ddgs_images", new=fake_images):
            result = await image_service.search_images("tree", settings, 100)

        self.assertEqual(len(result), settings.image_search_max_results)
        self.assertEqual(
            seen, [min(settings.image_search_max_results * 3, 12)]
        )


if __name__ == "__main__":
    unittest.main()
