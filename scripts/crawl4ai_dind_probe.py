"""Run a real Crawl4AI /crawl probe from the isolated fcai-bot test container.

Copy into the inner test client container, then run with CRAWL4AI_TEST_TOKEN
set to the isolated crawler's test token. No host ports or credentials needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL hosted by the controlled test fixture")
    parser.add_argument("--expect", choices=("success", "blocked"), required=True)
    parser.add_argument("--contains", default="Controlled public fixture",
                        help="Text required in a successful crawl's Markdown")
    args = parser.parse_args()

    token = os.environ.get("CRAWL4AI_TEST_TOKEN", "")
    if not token:
        print("CRAWL4AI_TEST_TOKEN is required", file=sys.stderr)
        return 2
    payload = {
        "urls": [args.url],
        "browser_config": {
            "type": "BrowserConfig",
            "params": {"headless": True, "accept_downloads": False},
        },
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {"cache_mode": "bypass", "stream": False},
        },
    }
    req = request.Request(
        "http://crawl4ai:11235/crawl",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    http_status = None
    response = None
    try:
        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=75) as stream:
            http_status = stream.status
            response = json.load(stream)
    except error.HTTPError as exc:
        http_status = exc.code
        try:
            response = json.load(exc)
        except (ValueError, UnicodeDecodeError):
            pass
    except (error.URLError, TimeoutError, OSError) as exc:
        print(f"connection_error={type(exc).__name__}")
    results = response.get("results", []) if isinstance(response, dict) else []
    first = results[0] if isinstance(results, list) and results else None
    success = bool(
        http_status == 200
        and isinstance(response, dict)
        and response.get("success")
        and isinstance(first, dict)
        and first.get("success")
    )
    result_error = first.get("error_message") if isinstance(first, dict) else None
    print(
        f"status={http_status} success={success} "
        f"error_type={type(result_error).__name__ if result_error else 'none'}"
    )
    contains_fixture = False
    if success:
        markdown = first.get("markdown")
        text = (
            markdown.get("raw_markdown", "")
            if isinstance(markdown, dict)
            else str(markdown or "")
        )
        contains_fixture = args.contains in text
        print("contains_fixture=" + str(contains_fixture))
    expected = args.expect == "success"
    return 0 if success == expected and (not expected or contains_fixture) else 1


if __name__ == "__main__":
    raise SystemExit(main())
