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
    pg.click(".hrow[data-h='Г-106']")
    check(f"[{tag}] оси выбранной гипотезы", "Г-106" in pg.inner_text("#axh") and len(pg.query_selector_all(".axis")) > 0 and len(pg.query_selector_all(".val .ticks i")) > 0)
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
    pg.fill("#q", "Г-106")
    check(f"[{tag}] поиск сужает список", len(pg.query_selector_all(".hrow")) < n_hyp)
    pg.fill("#q", "")
    pg.keyboard.press("Escape")
    # гипотеза целиком вне формы (Г-28: клетки R1 есть, $ нет): отдельное состояние с причиной, а не «не считали» и не «Всё уже посчитано»
    for pid in pg.eval_on_selector_all(".hrow.pin", "e=>e.map(x=>x.dataset.h)"):
        pg.click(f"[data-pin='{pid}']")
    pg.click(".hrow[data-h='Г-28']")
    row = pg.query_selector("#mx [data-row='Г-28']")
    cls = [c.get_attribute("class") for c in pg.query_selector_all("#mx tr:has([data-row='Г-28']) .cell")]
    check(f"[{tag}] Г-28 вне формы: в списке «вне», в матрице все клетки «вне»", pg.inner_text(".hrow[data-h='Г-28'] .hn") == "вне" and row and cls and all(" o" in " " + c for c in cls) and "0/" not in pg.inner_text("#mx tr:has([data-row='Г-28'])"), f"{len(cls)} клеток")
    check(f"[{tag}] Г-28: причина словами в легенде, карточке и подсказке", "вне формы" in pg.inner_text(".legend") and "клетки R1 есть, $ нет" in pg.inner_text("#axes") and "клетки R1 есть, $ нет" in pg.get_attribute("#mx tr:has([data-row='Г-28']) .cell", "title"))
    pg.click("#mx tr:has([data-row='Г-28']) .cell >> nth=0")
    check(f"[{tag}] Г-28: клик по клетке — причина, не выбор", "вне формы" in pg.inner_text("#toast") and pg.query_selector("#mx tr:has([data-row='Г-28']) .cell.sel") is None, pg.inner_text("#toast"))
    pg.click("#allmiss")
    go = pg.inner_text("#go")
    check(f"[{tag}] Г-28: кнопка называет причину, а не «Всё уже посчитано»", pg.inner_text("#bigN") == "0" and "Всё уже посчитано" not in go and "клетки R1 есть, $ нет" in go and pg.is_disabled("#go"), go)
    if shot_dir:
        pg.screenshot(path=os.path.join(shot_dir, f"{tag}-vne-formy.png"))
    pg.click("#noneSel")
    pg.click("[data-pin='Г-28']")
    # TK-247: клетка частично «вне формы» — зелёная «✓ +N вне», жёлтая только пока форма-считаемое не досчитано
    for pid in ("Г-39", "Г-35", "Г-117"):
        pg.click(f".hrow[data-h='{pid}']")
        cells = pg.query_selector_all(f"#mx tr:has([data-row='{pid}']) .cell")
        cl = [c.get_attribute("class") for c in cells]
        tx = [c.inner_text() for c in cells]
        check(f"[{tag}] {pid}: все клетки зелёные «✓ +N вне», жёлтых нет", cells and all(" f" in " " + c for c in cl) and all(t.startswith("✓") and "вне" in t for t in tx), f"{len(cells)} клеток, {tx[:1]}")
        check(f"[{tag}] {pid}: в списке «9/9» зелёным, подсказка — «вне формы»", pg.inner_text(f".hrow[data-h='{pid}'] .hn") == "9/9" and "hn f" in pg.get_attribute(f".hrow[data-h='{pid}'] .hn", "class") and "вне формы" in cells[0].get_attribute("title"), pg.get_attribute(f".hrow[data-h='{pid}'] .hn", "class"))
    for pid in ("Г-39", "Г-35"):
        ttl = pg.get_attribute(f"#mx tr:has([data-row='{pid}']) .cell >> nth=0", "title")
        check(f"[{tag}] {pid}: подпись «вне формы» — «не считали — данных нет», не «посчитано»", "не считали — данных нет" in ttl and "посчитано вне Летописи" not in ttl, ttl)
    pg.click("#mx tr:has([data-row='Г-39']) .cell >> nth=0")
    check(f"[{tag}] Г-39: клик по зелёной клетке — «всё посчитано · +N вне формы», не выбор", "всё посчитано" in pg.inner_text("#toast") and "вне формы" in pg.inner_text("#toast") and pg.query_selector("#mx tr:has([data-row='Г-39']) .cell.sel") is None, pg.inner_text("#toast"))
    pg.click("#allmiss")
    go = pg.inner_text("#go")
    check(f"[{tag}] Г-39/Г-35/Г-117: считать нечего — «Всё уже посчитано», не «Вне формы»", pg.inner_text("#bigN") == "0" and "Всё уже посчитано" in go and pg.is_disabled("#go"), go)
    if shot_dir:
        pg.screenshot(path=os.path.join(shot_dir, f"{tag}-chast-vne-formy.png"))
    # инвариант по всем гипотезам: жёлтая клетка — только если форма-считаемое осталось; «вне» — только если посчитанного нет
    while pg.query_selector("[data-gall]:text-is('добавить все')"):   # список перерисовывается после клика — кнопку берём заново
        pg.click("[data-gall]:text-is('добавить все') >> nth=0")
    pt = pg.eval_on_selector_all("#mx .cell.p", "e=>e.map(x=>x.innerText.trim())")
    bad = [t for t in pt if not re.fullmatch(r"\d+/\d+", t) or int(t.split("/")[0]) >= int(t.split("/")[1])]
    check(f"[{tag}] ни одной жёлтой клетки без остатка к счёту (все гипотезы в матрице)", len(pg.query_selector_all("#mx tr")) > 40 and not bad, f"жёлтых {len(pt)}, плохих {len(bad)} {bad[:3]}")
    check(f"[{tag}] серая «вне» — только «вне», зелёная — без «N/M»", all(t == "вне" for t in pg.eval_on_selector_all("#mx .cell.o", "e=>e.map(x=>x.innerText.trim())")) and all("/" not in t for t in pg.eval_on_selector_all("#mx .cell.f", "e=>e.map(x=>x.innerText)")))
    while pg.query_selector(".hrow.pin"):
        pg.click(".hrow.pin [data-pin] >> nth=0")
    # смешанный случай: Г-106 — часть посчитана, часть к счёту (жёлтая «N/M», N < M)
    pg.click(".hrow[data-h='Г-106']")
    pt = pg.eval_on_selector_all("#mx tr:has([data-row='Г-106']) .cell.p", "e=>e.map(x=>x.innerText.trim())")
    check(f"[{tag}] Г-106: жёлтая клетка «посчитано/можно посчитать» остаётся, пока есть что считать", pt and all(re.fullmatch(r"\d+/\d+", t) and int(t.split("/")[0]) < int(t.split("/")[1]) for t in pt), str(pt[:2]))
    pg.click("[data-pin='Г-106']")
    pg.click("#noneSel")
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
    order = [c.inner_text() for c in pg.query_selector_all("#pcards .chip")]
    check(f"[{tag}] порядок как в макете: идёт, очередь, ждёт Летопись, в Летописи, отказ", order == ["идёт", "в очереди", "ждёт Летопись", "в Летописи", "отказ"], ", ".join(order))
    sub = pg.query_selector("#pcards .pr.queue .sub").inner_text()
    check(f"[{tag}] строка «в очереди» с «N хеш-мес»", "хеш-мес" in sub, sub)
    tms = [t.inner_text() for t in pg.query_selector_all("#pcards .pr.wait .tm, #pcards .pr.done .tm")]
    check(f"[{tag}] у готовых в «Время» длительность и час «считано ЧЧ:ММ»", all("мин" in t and "считано" in t for t in tms) and len(tms) == 2, " | ".join(t.replace(chr(10), " ") for t in tms))
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


def scenario_real(b, url, tag, size, scheme, shot_dir, run_ids):
    """TK-244: вкладка «Прогоны» на копии НАСТОЯЩЕГО каталога прогона (run_dir скопирован с calc): строка, этапы, метрики равны run_metrics напрямую."""
    import metrics, runs
    ctx = b.new_context(viewport=size, color_scheme=scheme)
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "ERR_" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url)
    pg.wait_for_selector(".hrow")
    pg.click("#t-prn")
    pg.wait_for_selector("#pcards .pr")
    d = launch.load_config()["run_dir"]
    for rid in run_ids:
        row = pg.query_selector(f'#pcards .pr[data-po="{rid}"]')
        check(f"[{tag}] настоящий {rid}: строка есть", row is not None)
        if row is None:
            continue
        txt = row.inner_text()
        want = (runs._json(os.path.join(d, rid, "run.json")) or {}).get("hyps") or ["?"]
        check(f"[{tag}] {rid}: подпись гипотезы = заявленная ({want[0]}), не соседняя с тем же хешем", any(want[0] + " ·" in ln or ln.startswith(want[0]) for ln in txt.splitlines()), txt.replace(chr(10), " | ")[:120])
        rec = runs.run_record(os.path.join(d, rid), rid, __import__("time").time(), in_canon=None)
        met = runs.run_metrics(os.path.join(d, rid))
        best = metrics.best_by_sharpe(met)[1]
        check(f"[{tag}] {rid}: статус и % как у run_record ({rec['s']}, {rec['pct']} %)", launch.runs_view.__module__ and f"{rec['pct']} %" in txt, txt.replace(chr(10), " | ")[:160])
        if best:
            check(f"[{tag}] {rid}: PnL/Шарп/Винрейт строки = пересчёт run_metrics", f"{round(best['pnl'])}" in txt and f"{best['sharpe']:.2f}" in txt and f"{round(best['winrate'] * 100)} %" in txt,
                  f"pnl {best['pnl']:.1f} sh {best['sharpe']:.2f} wr {best['winrate']:.2f}: {txt.replace(chr(10), ' | ')[:200]}")
        row.click()
        pg.wait_for_selector("#pcards .pdd")
        pdd = pg.inner_text("#pcards .pdd")
        check(f"[{tag}] {rid}: раскрытие — этапы, дни {rec['days'][0]} из {rec['days'][1]}, таблица по гипотезам", f"{rec['days'][0]} из {rec['days'][1]}" in pdd and "Гипотеза" in pdd, pdd.replace(chr(10), " | ")[:200])
    if shot_dir:
        pg.screenshot(path=os.path.join(shot_dir, f"{tag}-real.png"))
    check(f"[{tag}] настоящий прогон: консоль без ошибок", not errs, "; ".join(errs[:3]))
    ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot-dir")
    ap.add_argument("--real", help="каталог-копия run_dir с настоящими L-… (с calc): только сценарий «Прогоны» на них")
    o = ap.parse_args()
    if o.shot_dir:
        os.makedirs(o.shot_dir, exist_ok=True)
    launch.status = lambda cfg=None, run_cmd=None: {"text": "", "busy": 0, "warn": False, "errors": [], "pending": 0}   # ssh на calc из теста не нужен
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    if o.real:
        ids = sorted(n for n in os.listdir(o.real) if n.startswith("L-"))
        cfg0 = launch.load_config()
        launch.load_config = lambda path=None: cfg0 | {"run_dir": o.real, "request_dir": os.path.join(o.real, "_req")}
        with sync_playwright() as p:
            b = p.chromium.launch()
            for tag, size, scheme in (("real-desktop-dark", {"width": 1440, "height": 900}, "dark"), ("real-narrow-light", {"width": 900, "height": 1200}, "light")):
                scenario_real(b, url, tag, size, scheme, o.shot_dir, ids)
            b.close()
        srv.shutdown()
        return 0 if all(ok_all) else 1
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
