import pytest

SETTINGS_ENV = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "SEC_USER_AGENT",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "BNPL_METRICS_CACHE_DIR",
    "BNPL_METRICS_DATA_DIR",
    "BNPL_METRICS_CONFIG",
    "BNPL_METRICS_PORT",
    "BNPL_METRICS_SHARE",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Tests never read a real .env or the caller's environment."""
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("bnpl_metrics.settings.load_dotenv", lambda *a, **k: False)


@pytest.fixture
def serve():
    """Serve ``{path: (status, body)}`` from localhost; returns ``(base_url, requested_paths)``."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    servers = []

    def start(pages):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                status, body = pages.get(self.path, (404, "not found"))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body.encode() if isinstance(body, str) else body)

            def log_message(self, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}", seen

    yield start
    for httpd in servers:
        httpd.shutdown()
