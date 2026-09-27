#!/usr/bin/env python3
"""Приёмка новой страницы дашборда (Playwright): открывает index-new.html и проверяет «Главную» и «Гипотезы».

    python tools/dashboard/check_page.py [data/titration-dashboard/index-new.html] [--shots <папка>] [--seed N]

Выход 0 — все проверки прошли; иначе 1. Печатает строку на проверку.
"""
import argparse
import os
import random
import re
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (" — " + detail if detail else ""))


def money_val(s):
    """«+$1 234» / «−$56» → число; «—» → None."""
    s = s.replace(" ", "").replace(" ", "").replace("\xa0", "").replace(" ", "")
    m = re.match(r"^([+−-])\$(\d+)$", s)
    if not m:
        return None
    return (-1 if m.group(1) in "−-" else 1) * int(m.group(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", nargs="?", default=os.path.join(ROOT, "data", "titration-dashboard", "index-new.html"))
    ap.add_argument("--shots", default=None, help="папка для скриншотов 1400 px")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else random.randrange(10**6)
    rnd = random.Random(seed)
    url = "file:///" + os.path.abspath(a.page).replace("\\", "/")
    errors = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
        pg.goto(url, wait_until="load")
        pg.wait_for_selector("#rank tbody tr[data-k]")

        # раскладка 1400 px: таблица справа от графика
        eq = pg.eval_on_selector("#eq-box", "e => e.getBoundingClientRect().right")
        tb = pg.eval_on_selector("#panel-rank", "e => e.getBoundingClientRect().left")
        check("1400 px: таблица справа от графика", tb > eq, f"левый край таблицы {tb:.0f} > правый край графика {eq:.0f}")

        # все строки — чтобы выбрать случайные с расчётом
        pg.click("#more") if pg.is_visible("#more") else None
        keys = pg.eval_on_selector_all("#rank tbody tr[data-k]:not(.nocalc)", "els => els.map(e => e.dataset.k)")
        nocalc = pg.eval_on_selector_all("#rank tbody tr.nocalc", "els => els.length")
        print(f"seed {seed}; строк с расчётом {len(keys)}, без расчёта {nocalc}")
        picks = rnd.sample([k for k in keys if k != "btc4h_trail"], 5) + ["btc4h_trail"]

        path = lambda: pg.get_attribute("#eq-line", "d") if pg.query_selector("#eq-line") else None
        prev = path()
        for k in picks:
            row = f"#rank tbody tr[data-k='{k}']"
            cell = lambda c: pg.inner_text(f"{row} [data-c='{c}']").strip()
            pg.click(f"{row} td.name")
            tile = lambda t: pg.inner_text(f"#tiles [data-t='{t}']").strip()
            same = all(tile(t) == cell(t) for t in ["kpi-aug", "kpi-sep", "usd-aug", "usd-sep"])
            name_ok = pg.inner_text("#sel-name").strip() == pg.inner_text(f"{row} [data-c='name']").split(" · нет расчёта")[0].strip()
            sel_ok = "cur" in (pg.get_attribute(row, "class") or "").split()
            cur = path()
            check(f"выбор {k}", same and name_ok and sel_ok and cur is not None and cur != prev,
                  f"плитки=строка {same}, имя {name_ok}, выделение {sel_ok}, график перерисован {cur is not None and cur != prev}")
            prev = cur

        # период меняет график
        for per in ["sep", "aug", "augsep"]:
            before = path()
            pg.click(f"#period button[data-period='{per}']")
            check(f"период {per} меняет график", path() != before)

        # плечо ×3: деньги в плитках и таблице ×3
        row = "#rank tbody tr[data-k='btc4h_trail']"
        u1 = money_val(pg.inner_text("#tiles [data-t='usd-aug']"))
        pg.click("#size button[data-size='1500']")
        u3 = money_val(pg.inner_text("#tiles [data-t='usd-aug']"))
        r3 = money_val(pg.inner_text(f"{row} [data-c='usd-aug']"))
        check("плечо ×3 меняет деньги", u1 is not None and u3 is not None and abs(u3 - 3 * u1) <= 2 and r3 == u3, f"×1 {u1}, ×3 плитка {u3}, строка {r3}")
        pg.click("#size button[data-size='500']")

        # фильтр-чип сокращает таблицу
        pg.click("#chips [data-g='all']")
        n_all = pg.eval_on_selector_all("#rank tbody tr[data-k]", "e => e.length")
        pg.click("#chips [data-g='1']")
        n_f = pg.eval_on_selector_all("#rank tbody tr[data-k]", "e => e.length")
        check("фильтр-чип сокращает таблицу", 0 < n_f < n_all, f"{n_all} → {n_f}")
        pg.click("#chips [data-g='all']")
        pg.fill("#search", "H13")
        n_q = pg.eval_on_selector_all("#rank tbody tr[data-k]", "e => e.length")
        check("поиск сокращает таблицу", 0 < n_q < n_all, f"{n_all} → {n_q}")
        pg.fill("#search", "")

        if a.shots:
            os.makedirs(a.shots, exist_ok=True)
            pg.evaluate("window.scrollTo(0, 0)")
            pg.screenshot(path=os.path.join(a.shots, "new-top-1400.png"))
            pg.eval_on_selector("#panel-rank", "e => e.scrollIntoView()")
            pg.screenshot(path=os.path.join(a.shots, "new-table-1400.png"))
            pg.eval_on_selector("#panel-cfg", "e => e.scrollIntoView()")
            pg.screenshot(path=os.path.join(a.shots, "new-cfg-1400.png"))
            pg.set_viewport_size({"width": 1920, "height": 1080})
            pg.evaluate("window.scrollTo(0, 0)")
            pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(a.shots, "new-top-1920.png"))
            pg.set_viewport_size({"width": 1400, "height": 900})

        # вкладка «Гипотезы»
        pg.click("#tabs button[data-tab='hyp']")
        hyp_vis = pg.is_visible("#panel-hyp") and pg.is_visible("#panel-p07") and pg.is_visible("#p05-details")
        main_hid = not pg.is_visible("#tiles") and not pg.is_visible("#panel-rank")
        hyp_rows = pg.eval_on_selector_all("#hyp-table tbody tr", "e => e.length")
        check("«Гипотезы» показывает блоки и скрывает главную", hyp_vis and main_hid and hyp_rows > 0, f"блоки {hyp_vis}, главная скрыта {main_hid}, строк T-36 {hyp_rows}")
        if a.shots:
            pg.evaluate("window.scrollTo(0, 0)")
            pg.screenshot(path=os.path.join(a.shots, "new-hyp-1400.png"))
        pg.click("#tabs button[data-tab='main']")

        # телефон 375 px — без горизонтальной прокрутки страницы
        pg.set_viewport_size({"width": 375, "height": 812})
        pg.reload(wait_until="load")
        pg.wait_for_selector("#rank tbody tr[data-k]")
        over = lambda: pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        o_main = over()
        pg.click("#tabs button[data-tab='hyp']")
        o_hyp = over()
        pg.click("#tabs button[data-tab='main']")
        check("375 px без горизонтальной прокрутки", o_main <= 0 and o_hyp <= 0, f"главная {o_main} px, гипотезы {o_hyp} px")
        if a.shots:
            pg.evaluate("window.scrollTo(0, 0)")
            pg.screenshot(path=os.path.join(a.shots, "new-top-375.png"))
        br.close()
    check("0 ошибок JS", not errors, "; ".join(errors[:5]))
    print(f"итог: {sum(results)}/{len(results)} проверок прошли")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
