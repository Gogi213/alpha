#!/usr/bin/env python3
"""Отдаёт каталог дашборда по localhost одной командой.

Страницу собирает `alpha.exe lob dashboard --root <каталог записи> --out
<dir>`; этот скрипт только показывает готовые `index.html` и `data.json`.
Свободный порт выбирается сам и печатается — вместе с готовым адресом,
который остаётся скопировать в браузер.

    python tools/serve_dashboard.py data/dashboard
    python tools/serve_dashboard.py data/dashboard --port 8765

Ничего, кроме `--out`-каталога, наружу не отдаётся, и слушает скрипт только
127.0.0.1: это локальная страница для владельца, а не публичный сервер.
Зависимостей нет — стандартная библиотека, тот же приём, что у
`.autopilot/sync.py`.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socket
import socketserver
import sys

HOST = "127.0.0.1"


def free_port() -> int:
    """Порт, который сейчас свободен: ядро выдаёт его само, гадать нечего."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Тот же обработчик, что у `python -m http.server`, но с коротким логом:
    в stdout остаётся `GET /data.json -> 200`, по нему и видно, что страница
    жива и перечитывает данные."""

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - имя из базового класса
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="Локальный сервер для дашборда alpha")
    ap.add_argument("directory", help="каталог с index.html и data.json (--out команды lob dashboard)")
    ap.add_argument("--port", type=int, default=0, help="порт; 0 или без флага — свободный")
    args = ap.parse_args()

    directory = os.path.abspath(args.directory)
    index = os.path.join(directory, "index.html")
    if not os.path.isfile(index):
        sys.stderr.write(
            "нет %s — сначала соберите страницу:\n"
            "  alpha.exe lob dashboard --root <каталог записи> --out %s\n" % (index, args.directory)
        )
        return 2

    port = args.port or free_port()
    handler = functools.partial(QuietHandler, directory=directory)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, port), handler) as httpd:
        url = "http://%s:%d/index.html" % (HOST, port)
        print("dashboard: каталог %s" % directory)
        print("dashboard: порт %d" % port)
        print("dashboard: откройте %s" % url)
        print("dashboard: остановить — Ctrl+C")
        sys.stdout.flush()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("dashboard: остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
