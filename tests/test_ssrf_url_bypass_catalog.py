"""Pin the SSRF URL-validation bypass catalog.

``validate_public_url`` is the first gate in front of every outbound page
read (generic reader, Crawl4AI, URL tools). These cases must stay rejected:
if a future refactor accepts any of them, this test fails loudly instead of
silently reopening server-side request forgery.
"""

from __future__ import annotations

import unittest

from app.search.reader import validate_public_url


# Every entry must be rejected (non-None reason). Shapes cover: userinfo,
# non-http schemes, localhost, private/loopback/link-local literals, numeric
# IP evasions (decimal/octal/hex/short), parser-confusion characters,
# IPv6 zone index, local TLDs, empty/overlong input, bad ports and hosts.
REJECTED_URLS = (
    "http://user:pass@example.org/",
    "ftp://example.org/f",
    "javascript:alert(1)",
    "https://localhost/x",
    "http://x.localhost/",
    "http://foo.local/",
    "http://127.0.0.1/",
    "http://[::1]/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest",
    "http://0x7f000001/",
    "http://2130706433/",
    "http://127.1/",
    "http://0x7f.0x0.0x0.0x1/",
    "http://0177.0.0.01/",
    r"http://example.org\@evil/",
    "http://a b.example.org/",
    "http://[fe80::1%25eth0]/",
    "",
    "https://example.org:99999/",
    "http://[::ffff::1]/",
    "http:///no-host",
)

# Ordinary public URLs must keep working; the guard must not fail closed on
# everything.
ACCEPTED_URLS = (
    "https://www.nasa.gov/mission/artemis-iii",
    "http://example.com:8080/a?b=c",
    "https://example.org/",
)


class SsrfBypassCatalogTests(unittest.TestCase):
    def test_bypass_shapes_stay_rejected(self):
        for url in REJECTED_URLS:
            with self.subTest(url=url):
                reason = validate_public_url(url)
                self.assertIsNotNone(reason, f"bypass accepted: {url!r}")
                self.assertTrue(reason.strip())

    def test_overlong_url_is_rejected(self):
        self.assertIsNotNone(validate_public_url("https://example.org/" + "x" * 8192))

    def test_public_urls_stay_accepted(self):
        for url in ACCEPTED_URLS:
            with self.subTest(url=url):
                self.assertIsNone(validate_public_url(url))


if __name__ == "__main__":
    unittest.main()
