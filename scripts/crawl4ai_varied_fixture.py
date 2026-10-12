"""Disposable HTTP fixture for Docker-in-Docker Crawl4AI staging checks.

Only bind this server to an isolated, project-controlled test network.
The generated manifest varies HTML structure, JS rendering, subresources and
public redirects. It cannot substitute for 20 independent production-like sites.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


ROUTES = (
    *(f"/static/{i}" for i in range(1, 13)),
    *(f"/js/{i}" for i in range(1, 5)),
    "/assets/image", "/assets/iframe",
    "/redirect/one", "/redirect/two",
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/redirect/one", "/redirect/two"):
            target = "/static/1" if path.endswith("one") else "/static/2"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        if path == "/image.png":
            data = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
                    b"\x00\x00\x00\x0bIDATx\x9cc`\x00\x02\x00\x00\x05"
                    b"\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82")
            content_type = "image/png"
        elif path == "/frame":
            data = b"<html><body>FCAI PUBLIC FRAME LOADED</body></html>"
            content_type = "text/html; charset=utf-8"
        elif path in ROUTES:
            script = ""
            if path.startswith("/js/"):
                script = ("<script>document.getElementById('render').textContent="
                          "'FCAI JS RENDERED';</script>")
            assets = ""
            if path == "/assets/image":
                assets = '<img src="/image.png" alt="FCAI PUBLIC IMAGE">'
            if path == "/assets/iframe":
                assets = '<iframe src="/frame"></iframe>'
            article = "".join(
                f"<p>Section {i}: This controlled article exercises realistic "
                "HTML extraction, paragraphs, punctuation and page navigation "
                "without loading any third-party resources. The isolation "
                "firewall must permit the public page while continuing to "
                "reject every private destination.</p>"
                for i in range(1, 7)
            )
            data = (f"<html><body><h1>FCAI VARIED FIXTURE PASS</h1>"
                    f"<p>Route {path}</p><main>{article}</main>"
                    f"<div id='render'></div>{assets}"
                    f"{script}</body></html>").encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-base", help="Print the 20 test URLs and exit")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address; use 0.0.0.0 only inside disposable Docker")
    parser.add_argument("--port", type=int, default=80)
    args = parser.parse_args()
    if args.manifest_base:
        for path in ROUTES:
            print(args.manifest_base.rstrip("/") + path)
        return
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
