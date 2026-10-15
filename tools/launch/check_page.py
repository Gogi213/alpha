#!/usr/bin/env python3
"""Клик-тест формы запуска на настоящих данных реестра (Playwright, TK-242): python tools/launch/check_page.py [--shot-dir каталог].
Десктоп и ≤ 980 px, тёмная и светлая тема; консоль без ошибок; постановка — только пробная (dry_run подмешивается в POST /api/submit).
Выход 0 — все проверки прошли."""
import argparse, json, os, re, sys, threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import launch, server

ok_all = []


def check(name, ok, detail=""):
    ok_all.append(bool(ok))
    print(("OK   " if ok else "FAIL ") + name + (" — " + detail if detail else ""))


def scenario(b, url, tag, size, scheme, shot_dir):
    ctx = b.new_context(viewport=size, color_scheme=scheme)
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "fonts.g" not in m.text and "ERR_" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("**/api/submit", lambda r: r.continue_(post_data=json.dumps({**json.loads(r.request.post_data), "dry_run": True})))
    pg.goto(url)
    pg.wait_for_selector(".hrow")
    n_hyp = len(pg.query_selector_all(".hrow"))
    check(f"[{tag}] список гипотез из реестра", n_hyp > 10, f"{n_hyp} строк")
    check(f"[{tag}] кольца семейств", len(pg.query_selector_all(".rb")) > 2)
    # выбрать гипотезу: строка → оси с отметками по месяцам
    pg.click(".hrow[data-h='Г-141']")
    check(f"[{tag}] оси выбранной гипотезы", "Г-141" in pg.inner_text("#axh") and len(pg.query_selector_all(".axis")) > 0 and len(pg.query_selector_all(".val .ticks i")) > 0)
    v0 = pg.inner_text("#axh b")
    pg.click(".val:not(.on), .val.on >> nth=0")
    check(f"[{tag}] клик по значению оси меняет число вариантов", pg.inner_text("#axh b") != v0, f"{v0} → {pg.inner_text('#axh b')}")
    pg.click(".val:not(.on) >> nth=0")   # вернуть
    # матрица, «Всё непосчитанное», счёт «к счёту / уже есть»
    check(f"[{tag}] матрица: строка выбранной гипотезы", len(pg.query_selector_all("#mx .cell")) > 0)
    pg.click("#allmiss")
    big = int(pg.inner_text("#bigN"))
    check(f"[{tag}] «Всё непосчитанное» даёт к счёту > 0", big > 0, f"{big}; уже есть {pg.inner_text('#legS')}")
    pg.click("#noneSel")
    check(f"[{tag}] «Снять» обнуляет", pg.inner_text("#bigN") == "0")
    # клик по клетке / строке / месяцу
    cells = pg.query_selector_all("#mx .cell:not(.f)")
    pg.click("#mx .cell:not(.f) >> nth=0")
    check(f"[{tag}] клик по клетке выбирает её", "sel" in (pg.query_selector("#mx .cell.sel") and pg.query_selector("#mx .cell.sel").get_attribute("class") or ""))
    pg.click("#noneSel")
    pg.click("#mx [data-row]")
    check(f"[{tag}] клик по строке выбирает недостающие месяцы", int(pg.inner_text("#bigN")) > 0)
    pg.click("#noneSel")
    pg.click("#mx th[data-col] >> nth=0")
    pg.click("#noneSel")
    # подтверждение: «к счёту» страницы = плану сервера; Отмена; Да → пробная постановка
    pg.click("#allmiss")
    big = int(pg.inner_text("#bigN"))
    pg.click("#go")
    pg.wait_for_selector("#conf:not([hidden])")
    m = re.search(r"Поставить (\d+)\?", pg.inner_text("#confTxt"))
    check(f"[{tag}] подтверждение: число сервера = числу страницы", m and int(m.group(1)) == big, pg.inner_text("#confTxt"))
    pg.click("#no")
    check(f"[{tag}] «Отмена» возвращает кнопку", pg.is_visible("#go") and not pg.is_visible("#conf"))
    pg.click("#go")
    pg.wait_for_selector("#conf:not([hidden])")
    pg.click("#yes")
    pg.wait_for_function("document.querySelector('#toast').textContent.includes('Поставлено')")
    check(f"[{tag}] пробная постановка (dry_run)", "L-" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.wait_for_selector("#v-prn:not([hidden])")
    check(f"[{tag}] после «Да» страница открывает «Прогоны»", pg.get_attribute("#t-prn", "aria-selected") == "true")
    pg.click("#t-run")
    # поиск и клавиатура
    pg.keyboard.press("/")
    check(f"[{tag}] «/» ставит фокус в поиск", pg.evaluate("document.activeElement.id") == "q")
    pg.fill("#q", "Г-141")
    check(f"[{tag}] поиск сужает список", len(pg.query_selector_all(".hrow")) < n_hyp)
    pg.fill("#q", "")
    pg.keyboard.press("Escape")
    # гипотеза целиком вне формы (Г-28: посчитано вне Летописи): отдельное состояние с причиной, а не «не считали» и не «Всё уже посчитано»
    for pid in pg.eval_on_selector_all(".hrow.pin", "e=>e.map(x=>x.dataset.h)"):
        pg.click(f"[data-pin='{pid}']")
    pg.click(".hrow[data-h='Г-28']")
    row = pg.query_selector("#mx [data-row='Г-28']")
    cls = [c.get_attribute("class") for c in pg.query_selector_all("#mx tr:has([data-row='Г-28']) .cell")]
    check(f"[{tag}] Г-28 вне формы: в списке «вне», в матрице все клетки «вне»", pg.inner_text(".hrow[data-h='Г-28'] .hn") == "вне" and row and cls and all(" o" in " " + c for c in cls) and "0/" not in pg.inner_text("#mx tr:has([data-row='Г-28'])"), f"{len(cls)} клеток")
    check(f"[{tag}] Г-28: причина словами в легенде, карточке и подсказке", "вне формы" in pg.inner_text(".legend") and "посчитано вне Летописи" in pg.inner_text("#axes") and "посчитано вне Летописи" in pg.get_attribute("#mx tr:has([data-row='Г-28']) .cell", "title"))
    pg.click("#mx tr:has([data-row='Г-28']) .cell >> nth=0")
    check(f"[{tag}] Г-28: клик по клетке — причина, не выбор", "вне формы" in pg.inner_text("#toast") and pg.query_selector("#mx tr:has([data-row='Г-28']) .cell.sel") is None, pg.inner_text("#toast"))
    pg.click("#allmiss")
    go = pg.inner_text("#go")
    check(f"[{tag}] Г-28: кнопка называет причину, а не «Всё уже посчитано»", pg.inner_text("#bigN") == "0" and "Всё уже посчитано" not in go and "посчитано вне Летописи" in go and pg.is_disabled("#go"), go)
    if shot_dir:
        pg.screenshot(path=os.path.join(shot_dir, f"{tag}-vne-formy.png"))
    pg.click("#noneSel")
    pg.click("[data-pin='Г-28']")
    # вкладка «Летопись»
    pg.click("#t-let")
    pg.wait_for_selector("#lt tr .mc")
    rows = len(pg.query_selector_all("#lt tr")) - 1
    check(f"[{tag}] «Летопись»: таблица вариантов × месяцы", rows > 0, f"{rows} строк, {pg.inner_text('#lcnt')}")
    pg.click("#linc")
    inc_n, links = len(pg.query_selector_all("#lt tr")) - 1, len(pg.query_selector_all("#lt .link"))
    check(f"[{tag}] «Только неполные» оставляет строки, у части — «Досчитать →»", inc_n > 0 and 0 < links <= inc_n, f"{inc_n} строк, {links} ссылок")
    pg.click("#vseg [data-vd='не считали']")
    check(f"[{tag}] фильтр по вердикту", "из" in pg.inner_text("#lcnt"), pg.inner_text("#lcnt"))
    pg.click("#vseg [data-vd='']")
    shot = os.path.join(shot_dir, f"{tag}-letopis.png") if shot_dir else None
    if shot:
        pg.screenshot(path=shot)
    pg.click("#lt .link >> nth=0")
    pg.wait_for_selector("#v-run:not([hidden])")
    check(f"[{tag}] «Досчитать →» переносит недостающие месяцы в матрицу", int(pg.inner_text("#bigN")) > 0 and pg.query_selector("#mx .cell.sel") is not None, f"к счёту {pg.inner_text('#bigN')}")
    if shot:
        pg.screenshot(path=os.path.join(shot_dir, f"{tag}-zapusk.png"), full_page=size["width"] <= 980)
    if size["width"] > 980:
        check(f"[{tag}] три колонки без горизонтального скролла", pg.evaluate("document.documentElement.scrollWidth<=innerWidth+1"))
    else:
        check(f"[{tag}] узкий экран: одна колонка", pg.evaluate("getComputedStyle(document.querySelector('.grid')).display") == "block")
    check(f"[{tag}] консоль без ошибок", not errs, "; ".join(errs[:3]))
    ctx.close()


def scenario_runs(b, url, tag, size, scheme, shot_dir):
    """TK-244: вкладка «Прогоны» на фикстурах всех пяти статусов (tests/runs_fixture.py)."""
    ctx = b.new_context(viewport=size, color_scheme=scheme)
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "fonts.g" not in m.text and "ERR_" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url)
    pg.wait_for_selector(".hrow")
    pg.click("#t-prn")
    pg.wait_for_selector("#pcards .pr")
    chips = sorted(c.inner_text() for c in pg.query_selector_all("#pcards .chip"))
    check(f"[{tag}] «Прогоны»: пять статусов", chips == sorted(["идёт", "в очереди", "ждёт Летопись", "в Летописи", "отказ"]), ", ".join(chips))
    check(f"[{tag}] значок на ярлыке = идущие и ждущие", pg.inner_text("#tbadge") == "2", pg.inner_text("#tbadge"))
    na = len(pg.query_selector_all("#pcards .mv.na"))
    check(f"[{tag}] метрики: у посчитанных прогонов числа, у идущих/в очереди/отказа прочерк", na == 12, f"прочерков {na} из 20")
    pg.click("#pcards .pr >> nth=0")
    pg.wait_for_selector("#pcards .pdd")
    check(f"[{tag}] клик раскрывает этапы и таблицу по гипотезам", pg.query_selector("#pcards .steps") is not None and pg.query_selector("#pcards .pht") is not None)
    pg.click("#pseg [data-pf='fail']")
    check(f"[{tag}] фильтр «Отказ»", len(pg.query_selector_all("#pcards .pr")) == 1 and "потолок 501" in pg.inner_text("#pcards"), pg.inner_text("#pcnt"))
    pg.click("#pseg [data-pf='']")
    check(f"[{tag}] «Другие задания на calc (не из формы)»", "tk231-psim" in pg.inner_text("#pforeign"))
    if shot_dir:
        pg.screenshot(path=os.path.join(shot_dir, f"{tag}-progony.png"), full_page=size["width"] <= 980)
    if size["width"] > 980:
        check(f"[{tag}] «Прогоны»: без горизонтального скролла", pg.evaluate("document.documentElement.scrollWidth<=innerWidth+1"))
    else:
        check(f"[{tag}] «Прогоны»: узкий экран — шапка таблицы скрыта", pg.evaluate("getComputedStyle(document.querySelector('.pr-h')).display") == "none")
    check(f"[{tag}] «Прогоны»: консоль без ошибок", not errs, "; ".join(errs[:3]))
    ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot-dir")
    o = ap.parse_args()
    if o.shot_dir:
        os.makedirs(o.shot_dir, exist_ok=True)
    launch.status = lambda cfg=None, run_cmd=None: {"text": "", "busy": 0, "warn": False, "errors": [], "pending": 0}   # ssh на calc из теста не нужен
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    with sync_playwright() as p:
        b = p.chromium.launch()
        for tag, size, scheme in (("desktop-dark", {"width": 1440, "height": 900}, "dark"), ("desktop-light", {"width": 1440, "height": 900}, "light"),
                                  ("narrow-dark", {"width": 900, "height": 1200}, "dark"), ("narrow-light", {"width": 900, "height": 1200}, "light")):
            scenario(b, url, tag, size, scheme, o.shot_dir)
        import tempfile
        sys.path.insert(0, os.path.join(HERE, "tests"))
        import runs_fixture
        cfg0, st0 = launch.load_config, launch.status
        cfg_fx, ps_fx = runs_fixture.build(os.path.join(tempfile.mkdtemp(), "fx"))
        launch.load_config = lambda path=None: cfg_fx
        launch.status = lambda cfg=None, run_cmd=None: {"text": ps_fx, "busy": 1, "warn": False, "errors": [], "pending": 1}
        try:
            for tag, size, scheme in (("desktop-dark", {"width": 1440, "height": 900}, "dark"), ("desktop-light", {"width": 1440, "height": 900}, "light"),
                                      ("narrow-dark", {"width": 900, "height": 1200}, "dark"), ("narrow-light", {"width": 900, "height": 1200}, "light")):
                scenario_runs(b, url, tag, size, scheme, o.shot_dir)
        finally:
            launch.load_config, launch.status = cfg0, st0
        b.close()
    srv.shutdown()
    return 0 if all(ok_all) else 1


if __name__ == "__main__":
    sys.exit(main())
