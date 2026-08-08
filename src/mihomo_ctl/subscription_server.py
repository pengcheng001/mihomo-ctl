"""Small token-protected HTTP endpoint for generated Decodo subscriptions."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import config as cfg_mod
from . import decodo as decodo_mod
from .utils import say


class _SubscriptionHandler(BaseHTTPRequestHandler):
    server_version = "mihomo-ctl-subscription/1"

    def _send(self, status: int, content_type: str, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _serve_subscription(self) -> None:
        path = urlsplit(self.path).path
        parts = path.split("/")
        if len(parts) != 3 or parts[1] != "sub" or not parts[2]:
            self._send(404, "text/plain; charset=utf-8", b"not found\n")
            return

        token = unquote(parts[2])
        try:
            cfg = cfg_mod.load(create_if_missing=False)
            if not decodo_mod.token_is_valid(cfg, token):
                # Do not distinguish an unknown token from a missing resource.
                self._send(404, "text/plain; charset=utf-8", b"not found\n")
                return
            body = decodo_mod.render_yaml(cfg)
        except ValueError:
            # Configuration problems must not disclose credentials or file details.
            self._send(503, "text/plain; charset=utf-8", b"subscription unavailable\n")
            return
        except Exception:
            self._send(500, "text/plain; charset=utf-8", b"internal error\n")
            return
        self._send(200, "text/yaml; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._serve_subscription()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._serve_subscription()

    def log_message(self, format: str, *args: object) -> None:
        # Default HTTP access logs would record the bearer token in /sub/<token>.
        # Keep the endpoint silent so credentials and subscription URLs stay out
        # of ordinary runtime logs.
        return


class SubscriptionServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(host: str, port: int) -> SubscriptionServer:
    return SubscriptionServer((host, port), _SubscriptionHandler)


def serve(host: str, port: int) -> None:
    server = create_server(host, port)
    say(f"Decodo 订阅服务已监听: {host}:{port} (仅 token URL 返回 YAML)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
