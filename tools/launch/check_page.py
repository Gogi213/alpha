#!/usr/bin/env python3
"""Клик-тест формы запуска (Playwright): python tools/launch/check_page.py [--shot файл.png]. Выход 0 — все проверки прошли."""
import argparse, os, sys, threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server

ok_all = []


def check(name, ok, detail=""):
    ok_all.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (" — " + detail if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot")
    o = ap.parse_args()
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 1500})
        pg.goto(url)
        pg.wait_for_selector("#m-2026-01")

        def num(s):
            return int(pg.inner_text(s))

        # Г-10 за январь посчитана целиком: К счёту = 0, кнопка даёт «нечего считать»
        pg.check("input[data-hyp='Г-10']")
        pg.click("#m-2026-01")
        pg.wait_for_function("document.querySelector('#cnt-grid').textContent!='0'")
        g1, h1, n1 = num("#cnt-grid"), num("#cnt-have"), num("#cnt-new")
        check("январь Г-10: всё в Летописи", g1 > 0 and h1 == g1 and n1 == 0, f"{g1}/{h1}/{n1}")
        pg.click("#btn-submit")
        pg.wait_for_function("document.querySelector('#msg').textContent.includes('нечего')")
        check("при К счёту = 0 — «нечего считать»", True)
        # клик по второму месяцу меняет счётчики
        pg.click("#m-2026-09")
        pg.wait_for_function(f"document.querySelector('#cnt-grid').textContent!='{g1}'")
        g2, h2, n2 = num("#cnt-grid"), num("#cnt-have"), num("#cnt-new")
        check("клик по месяцу меняет счётчики", g2 == 2 * g1 and (h2, n2) != (h1, n1), f"{g2}/{h2}/{n2}")
        # добавим не посчитанную Г-04: К счёту > 0
        pg.check("input[data-hyp='Г-04']")
        pg.wait_for_function("document.querySelector('#cnt-new').textContent!='0'")
        g2 = num("#cnt-grid")
        check("не посчитанная Г-04 даёт К счёту > 0", num("#cnt-new") > 0, f"{g2}/{num('#cnt-have')}/{num('#cnt-new')}")
        # ось: зелёные чипы не выбираются, клик по не зелёному меняет счётчики
        chips = pg.query_selector_all("#axes .chip")
        free = [c for c in chips if "done" not in (c.get_attribute("class") or "")]
        check("есть оси и чипы", len(chips) > 0 and len(free) > 0, f"{len(chips)} чипов, {len(free)} не зелёных")
        pg.click("#axes .chip[data-axis=keep_q]:not(.done)")
        pg.wait_for_function(f"document.querySelector('#cnt-grid').textContent!='{g2}'")
        check("клик по оси меняет счётчики", num("#cnt-grid") < g2, f"{num('#cnt-grid')}")
        if o.shot:
            pg.screenshot(path=o.shot, full_page=True)
        # вкладка «Летопись»
        pg.click(".tab[data-tab=log]")
        pg.wait_for_selector("#log tbody tr")
        check("вкладка «Летопись» показывает строки", len(pg.query_selector_all("#log tbody tr")) > 0)
        pg.select_option("#f-status", "full")
        pg.wait_for_timeout(500)
        rows = pg.query_selector_all("#log tbody tr")
        check("фильтр «посчитана полностью»", rows and all("full" in (r.get_attribute("class") or "") for r in rows), f"{len(rows)} строк")
        b.close()
    srv.shutdown()
    return 0 if all(ok_all) else 1


if __name__ == "__main__":
    sys.exit(main())
