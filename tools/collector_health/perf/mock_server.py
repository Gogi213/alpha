"""Стенд макета: живая страница (page.html из репо) + /api/arb и /api/arb/chart из каталога build_mock_dir.py. Запуск: python mock_server.py <репо> <каталог> <порт>."""
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(sys.argv[1])
sys.path.insert(0, str(REPO / "tools" / "collector_health"))
import arb_api  # noqa: E402
import serve  # noqa: E402

d = Path(sys.argv[2])
serve.v3.payload = lambda *a, **k: {}
serve.Handler.arb = arb_api.Arb(d)
serve.Handler.health_dir = d
serve.Handler.expected = []
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[3])), serve.Handler).serve_forever()
