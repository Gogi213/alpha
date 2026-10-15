#!/usr/bin/env python3
"""Замер скорости страницы формы в браузере (TK-246, тот же метод, что tk245-let): клик по «Летопись» → первые строки и все строки; загрузка страницы → «Запуск» виден.
  python -I -X utf8 tools/launch/tests/tk246_let.py [--page page.html] [--url http://host:port/<токен>/] [--n 5]
Без --url поднимает сервер на 127.0.0.1 с реестром репозитория; --page — подменить page.html (сравнение «до»: git show tk245:tools/launch/page.html)."""
import argparse, os, shutil, statistics, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from playwright.sync_api import sync_playwright
import server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page")
    ap.add_argument("--url")
    ap.add_argument("--n", type=int, default=5)
    o = ap.parse_args()
    url = o.url
    if not url:
        if o.page:
            d = tempfile.mkdtemp()
            shutil.copy(o.page, os.path.join(d, "page.html"))
            server.HERE = d
        server.prewarm()
        s = server.make_server(0)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{s.server_address[1]}/"
    first, full, boot = [], [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for _ in range(o.n):
            pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
            t0 = time.perf_counter()
            pg.goto(url)
            pg.wait_for_selector(".hrow")
            boot.append((time.perf_counter() - t0) * 1000)
            pg.wait_for_timeout(300)
            r = pg.evaluate("""() => new Promise(res => {
              const t0 = performance.now(), lt = document.getElementById('lt'); let first = null;
              document.getElementById('t-let').click();
              (function tick() {
                const n = lt.querySelectorAll('tr').length - 1, cnt = parseInt(document.getElementById('lcnt').textContent) || 0;
                if (first === null && n >= 20) first = performance.now() - t0;
                if (cnt > 0 && n === cnt) return res([first, performance.now() - t0]);
                requestAnimationFrame(tick);
              })();
            })""")   # время до отрисованных кадров с первыми строками и со всеми, прямо в странице (без накладных Playwright)
            first.append(r[0])
            full.append(r[1])
            rows = pg.evaluate("document.querySelectorAll('#lt tr').length - 1")
            pg.context.close()
        b.close()
    f = lambda a: f"медиана {statistics.median(a):.0f} мс (min {min(a):.0f}, max {max(a):.0f})"
    print(f"страница → список гипотез: {f(boot)}\nклик «Летопись» → первые строки: {f(first)}\nклик → все строки ({rows}): {f(full)}")


if __name__ == "__main__":
    main()
