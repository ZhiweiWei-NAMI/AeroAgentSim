"""Local control requests must not expose their credentials to ambient proxies."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from aero_bench.control.client import ControlApiClient


def test_loopback_json_and_stream_ignore_proxy_environment(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
                 "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            assert self.headers["Authorization"] == "Bearer " + "a" * 64
            self.send_response(200)
            media = "text/event-stream" if "/events?" in self.path else "application/json"
            raw = b"" if media == "text/event-stream" else b"{}"
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = ControlApiClient(base_url=f"http://127.0.0.1:{server.server_port}",
                                      origin="http://127.0.0.1:5310", bearer_token="a" * 64,
                                      timeout_seconds=2)
            assert client._request("GET", "/v1/catalog") == {}
            assert tuple(client.events("b" * 64)) == ()
            assert len(received) == 2
        finally:
            server.shutdown()
            thread.join(timeout=5)
