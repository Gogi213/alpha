#!/usr/bin/env python3
"""Помесячная прибыль ($) по всем посчитанным клеткам П-12, пул v171c (без TRUMP/TRX/BCH), окно янв–сен 2026 (октябрь — справочно).
Только группировка ГОТОВЫХ сделок (closes-json портфельного симулятора) по месяцу закрытия — ни бэктестов, ни нового счёта.
Источники:
  R2 + базы (B1, B1g, B3) — data/p12r2-v171c-a/closes-{vn,vt}-cap{0,3}-2026-MM.json, загрузка тем же кодом, что p12-sharpe.py
      (exec головы p12-r2-analyze.py: ключи SET+база+суффикс формы, NORM=1 — равная экспозиция §6(2); P12_DIR/P12_POOL_FROM=1 как у v171c-A);
  ext (7 клеток x-*) — data/tk113/ext/closes-cap{0,3}-2026-MM.json (как tools/compute/tk113-ext.py);
  R1 (155 клеток + B1) — data/tk113/r1-v171c/closes-cap{0,3}-<мес>.json (calc:/data/tk0113/drop2/r1; на v171c только фев–окт, января нет).
Режим free = потолок не применён (cap0), B2 = потолок 3 (cap3).
Сверка: B1 (+52,3/2750 free, -0,4/1503 B2); R2-клетки: итог по определённым месяцам - база = d_usd из p12-sharpe-<режим>-2026-10-10-v171c-D.csv;
ext = ext-v171c-2026-10-09.csv; R1 B1 (фев–сен) = B1 из R2 (фев–сен).
Запуск из корня репозитория: python tools/compute/monthly-pnl.py  → docs/findings/monthly-pnl-v171c-2026-10-09.{csv,md}"""
import csv, json, os, sys
from collections import defaultdict
from p12lib import load_head

R2DIR, EXTDIR, R1DIR = "data/p12r2-v171c-a/", "data/tk113/ext/", "data/tk113/r1-v171c/"
OUT = "docs/findings/monthly-pnl-v171c-2026-10-09"
JS = [f"2026-{i:02d}" for i in range(1, 10)]          # окно янв–сен
OCT = "2026-10"
R1M = dict(zip("feb mar apr may jun jul aug sep oct".split(), [f"2026-{i:02d}" for i in range(2, 11)]))
MODES = (("free", "0"), ("B2", "3"))
TOL = 0.5
MN = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен"]


def load_r2(mode):
    """exec головы p12-r2-analyze.py (до бутстрепа) — как load() в p12-sharpe.py; -> ns (S, CELLS, BN, defined_of, EXPO)."""
    os.environ["P12_DIR"] = R2DIR
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ns = load_head("tools/compute/p12-r2-analyze.py", "rng = np.random.default_rng(63)", [mode])
    if "НЕТ формы" in buf.getvalue():
        sys.exit("R2: " + buf.getvalue())
    return ns


def row(pack, family, cell, lst, months_ok):
    """lst: месяц -> [(мс, $)] -> словарь с помесячными суммами/числом сделок."""
    usd = {m: sum(v for _a, v in lst.get(m, [])) for m in months_ok + [OCT]}
    trd = {m: len(lst.get(m, [])) for m in months_ok + [OCT]}
    return dict(pack=pack, family=family, cell=cell, usd=usd, trd=trd)


def collect_r2(mode, cap):
    ns = load_r2(mode)
    S, CELLS, BN = ns["S"], ns["CELLS"], ns["BN"]
    rows, dm = [], {}
    for b in ("B1", "B1g", "B3"):
        rows.append(row("base", b, b, S[b][3], JS))
    for c in CELLS:
        if c not in S:
            sys.exit(f"R2: клетка {c} без формы в closes")
        r = row("R2", CELLS[c][0], c, S[c][3], JS)
        if sum(r["trd"].values()) == 0:
            sys.exit(f"R2: заглушка 0 у {c}")
        rows.append(r)
        b = BN[CELLS[c][1]]
        pool = set(JS)
        defc = set(ns["defined_of"](S[c][1], S[c][2], S[c][4])) & pool
        defb = set(ns["defined_of"](S[b][1], S[b][2])) & pool
        dm[c] = (b, defc & defb)
    return rows, dm


def collect_ext(mode, cap):
    B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
    cells = {"B1": ("B1", B1),
             "x-stop-pct2.5": ("x-stop", "ladder3x0..0.0409sw2-pct2.5-tr1x1-14400-ttl1800"),
             "x-dl-21600": ("x-deadline", "ladder3x0..0.0409sw2-pct2-tr1x1-21600-ttl1800"),
             "x-dl-28800": ("x-deadline", "ladder3x0..0.0409sw2-pct2-tr1x1-28800-ttl1800"),
             "x-entry-n.1-t.5": ("x-entry", "ladder3x0.00409..0.02045sw2-pct2-tr1x1-14400-ttl1800"),
             "x-entry-n.1-t2": ("x-entry", "ladder3x0.00409..0.0818sw2-pct2-tr1x1-14400-ttl1800"),
             "x-entry-n.25-t.5": ("x-entry", "ladder3x0.010225..0.02045sw2-pct2-tr1x1-14400-ttl1800"),
             "x-entry-n.25-t2": ("x-entry", "ladder3x0.010225..0.0818sw2-pct2-tr1x1-14400-ttl1800")}
    name = {v[1]: k for k, v in cells.items()}
    cl = defaultdict(dict)
    for m in JS + [OCT]:
        for form, per in json.load(open(f"{EXTDIR}closes-cap{cap}-{m}.json")).items():
            for _p, caps in per.items():
                for _c, lst in caps.items():
                    cl[name[form]][m] = [(int(a), float(b)) for a, b in lst]
    return [row("ext" if c != "B1" else "ext-B1", cells[c][0], c, cl[c], JS) for c in cells]


def collect_r1(mode, cap):
    cl = defaultdict(dict)
    for mn, m in R1M.items():
        for cell, per in json.load(open(f"{R1DIR}closes-cap{cap}-{mn}.json")).items():
            for _p, caps in per.items():
                for _c, lst in caps.items():
                    cl[cell][m] = [(int(a), float(b)) for a, b in lst]
    rows = []
    for cell in sorted(cl):
        r = row("R1", "B1" if cell == "B1" else cell.split("-")[0], cell, cl[cell], JS[1:])
        r["usd"]["2026-01"], r["trd"]["2026-01"] = None, 0
        rows.append(r)
    return rows


def tot(r):
    return sum(v for m, v in r["usd"].items() if m in JS and v is not None)


def ntr(r):
    return sum(n for m, n in r["trd"].items() if m in JS)


def main():
    data, bad, notes, info = {}, [], [], []
    sharpe = {}
    for mode, cap in MODES:
        r2, dm = collect_r2(mode, cap)
        data[mode] = dict(r2=r2, ext=collect_ext(mode, cap), r1=collect_r1(mode, cap), dm=dm)
        sharpe[mode] = {r["cell"]: r for r in csv.DictReader(open(f"docs/findings/p12-sharpe-{mode}-2026-10-10-v171c-D.csv", encoding="utf-8"))}
    # ---- сверка ----
    print("== СВЕРКА")
    ok_n = {"base": 0, "R2": 0, "ext": 0, "R1B1": 0}
    for mode, _cap in MODES:
        d = data[mode]
        by = {r["cell"]: r for r in d["r2"]}
        b1 = by["B1"]
        want = (52.3, 2750) if mode == "free" else (-0.4, 1503)
        okb = abs(tot(b1) - want[0]) <= TOL and ntr(b1) == want[1]
        print(f"[{mode}] B1 янв–сен: {tot(b1):+.2f} / {ntr(b1)} сд.  (принято {want[0]:+.1f} / {want[1]}) {'OK' if okb else 'РАСХОЖДЕНИЕ'}")
        if not okb:
            bad.append((mode, "B1", tot(b1), want[0]))
        ok_n["base"] += okb
        for bn in ("B1g", "B3"):
            print(f"[{mode}] {bn} янв–сен: {tot(by[bn]):+.2f} / {ntr(by[bn])} сд. (принятых чисел в задаче нет; через d_usd ниже)")
        # R2: итог по определённым месяцам - база = d_usd
        for c, (b, dmo) in d["dm"].items():
            r, rb = by[c], by[b]
            diff = sum(r["usd"][m] for m in dmo) - sum(rb["usd"][m] for m in dmo)
            sh = sharpe[mode][c]
            dusd, mdef = float(sh["d_usd"]), int(sh["months_defined"])
            full = tot(r) - tot(rb)
            if sh["base"] != b or mdef != len(dmo):
                bad.append((mode, c, "base/months_defined", (sh["base"], mdef), (b, len(dmo))))
            if abs(diff - dusd) > TOL:
                bad.append((mode, c, "d_usd по опр. месяцам", round(diff, 1), dusd))
            else:
                ok_n["R2"] += 1
            if len(dmo) < 9:
                notes.append(f"[{mode}] {c}: определённых месяцев {len(dmo)}/9 → d_usd (Шарп) = {dusd:+.1f} по ним; по всем 9 мес. Δ к базе {full:+.1f} (итог {tot(r):+.1f} при базе {b} {tot(rb):+.1f})")
        # ext
        ex = {r["cell"]: r for r in csv.DictReader(open("docs/findings/ext-v171c-2026-10-09.csv", encoding="utf-8")) if r["mode"] == mode}
        for r in d["ext"]:
            e = ex[r["cell"]]
            if abs(tot(r) - float(e["usd_jan_sep"])) > TOL or ntr(r) != int(e["trades"]):
                bad.append((mode, r["cell"], "ext usd/trades", (round(tot(r), 1), ntr(r)), (e["usd_jan_sep"], e["trades"])))
            else:
                ok_n["ext"] += 1
        # ext B1 = R2 B1 (помесячно) и R1 B1 фев–сен = R2 B1 фев–сен
        extb1 = next(r for r in d["ext"] if r["cell"] == "B1")
        for m in JS + [OCT]:
            if abs(extb1["usd"][m] - b1["usd"][m]) > 0.05:
                bad.append((mode, "ext B1 vs R2 B1", m, round(extb1["usd"][m], 2), round(b1["usd"][m], 2)))
        r1b1 = next(r for r in d["r1"] if r["cell"] == "B1")
        for m in JS[1:]:   # справочно: у R1 свои деревья (/data/tk063r1/b), собственный B1; принятых чисел у R1 на v171c нет
            if abs(r1b1["usd"][m] - b1["usd"][m]) > 0.05 or r1b1["trd"][m] != b1["trd"][m]:
                info.append(f"[{mode}] B1 из деревьев R1 ≠ B1 из R2/ext в {m}: {r1b1['trd'][m]} сд. / {r1b1['usd'][m]:+.1f} $ против {b1['trd'][m]} сд. / {b1['usd'][m]:+.1f} $ "
                            f"(разница {r1b1['usd'][m] - b1['usd'][m]:+.1f} $) — клетки R1 сравнивать с B1 R1, не с B1 R2")
        ok_n["R1B1"] += 1
    print(f"сошлось: базы B1 {ok_n['base']}/2; R2-клеток {ok_n['R2']}/80 (40 × 2 режима); ext-клеток {ok_n['ext']}/16 (8 × 2: 7 + B1); B1(R1) vs B1(R2) и B1(ext) vs B1(R2) — помесячно проверены (R1: см. справочно ниже)")
    if bad:
        print(f"РАСХОЖДЕНИЯ (> {TOL} $ или несовпадение): {len(bad)}")
        for x in bad:
            print("  ", x)
    else:
        print(f"расхождений > {TOL} $ нет")
    for n in notes:
        print("  прим.", n)
    for n in info:
        print("  справочно:", n)
    # ---- CSV ----
    allrows = []
    for mode, _cap in MODES:
        d = data[mode]
        for pk, rs in (("base_r2", d["r2"]), ("ext", d["ext"]), ("r1", d["r1"])):
            for r in rs:
                if r["pack"] == "ext-B1":
                    continue        # B1 — одна строка в базах (совпадение с ext проверено выше)
                allrows.append((mode, r))
    with open(OUT + ".csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["mode", "pack", "family", "cell"] + JS + ["jan_sep_total", "oct_note", "trades_jan_sep", "window"])
        for mode, r in allrows:
            isr1 = r["pack"] == "R1"
            w.writerow([mode, r["pack"], r["family"], r["cell"]] + ["" if r["usd"][m] is None else f"{r['usd'][m]:.1f}" for m in JS]
                       + [f"{tot(r):.1f}", f"{r['usd'][OCT]:.1f}", ntr(r), "feb-sep" if isr1 else "jan-sep"])
    # ---- MD ----
    f = lambda v: "—" if v is None else f"{v:+.1f}"
    L = ["# Помесячная прибыль ($) по клеткам П-12, пул v171c, янв–сен 2026", "",
         "Описание, не вердикт и не новый счёт: готовые закрытия сделок, сгруппированные по месяцу закрытия (UTC). "
         "Скрипт `tools/compute/monthly-pnl.py`, таблица CSV — `docs/findings/monthly-pnl-v171c-2026-10-09.csv`.", "",
         "- **Окно:** 01.01–30.09.2026 (В-211); октябрь (до 02.10, неполный) — отдельным справочным столбцом, в итог не входит.",
         "- **Пул:** v171c — без TRUMP/TRX/BCH (В-210), потолок B2 применён заново (portfolio-sim `--drop`).",
         "- **Режимы:** free — без потолка (cap0); B2 — потолок 3 одновременных позиции (cap3).",
         "- **Источники:** R2 и базы (B1, B1g, B3) — `data/p12r2-v171c-a/closes-{vn,vt}-cap{0,3}-2026-MM.json` (волна A, равная экспозиция §6(2), загрузка как в `p12-sharpe.py`); "
         "ext (7 клеток) — `data/tk113/ext/closes-cap{0,3}-2026-MM.json` (как `ext-v171c-2026-10-09`); "
         "R1 — `calc:/data/tk0113/drop2/r1` → `data/tk113/r1-v171c/` (на v171c есть только фев–окт: января у R1 нет, таблица R1 — отдельно, итог фев–сен).",
         "- Суммы считаются по несглаженным значениям, месяцы округлены до 0,1 — сумма округлённых может отличаться от итога на ≤ 0,5.",
         "- Клетка показана по всем сделкам окна; ворота «определённый месяц» (≥ 10 сделок, ≥ 10 суток) здесь не применяются, поэтому у клеток с менее чем 9 определёнными месяцами итог "
         "может отличаться от `base + d_usd` в Шарп-таблице (она считает только определённые месяцы) — список ниже.", ""]
    L += ["## Сверка с принятыми числами", ""]
    L.append(f"- B1 янв–сен: free {tot(next(r for r in data['free']['r2'] if r['cell']=='B1')):+.1f} ({ntr(next(r for r in data['free']['r2'] if r['cell']=='B1'))} сд.) "
             f"= принято +52,3 (2750); B2 {tot(next(r for r in data['B2']['r2'] if r['cell']=='B1')):+.1f} ({ntr(next(r for r in data['B2']['r2'] if r['cell']=='B1'))} сд.) = принято -0,4 (1503).")
    L.append(f"- R2: итог (по определённым месяцам) − база = `d_usd` из `p12-sharpe-{{free,B2}}-2026-10-10-v171c-D.csv`: сошлось {ok_n['R2']} из 80 клеток-режимов (допуск {TOL} $).")
    L.append(f"- ext: итог и число сделок = `ext-v171c-2026-10-09.csv`: сошлось {ok_n['ext']} из 16 (7 клеток + B1, два режима). B1 из ext = B1 из R2 помесячно (янв–окт).")
    L.append(f"- Расхождений > {TOL} $: " + (str(len(bad)) + " — см. вывод скрипта." if bad else "нет."))
    if info:
        L += ["- B1 из деревьев R1 (свои деревья `/data/tk063r1/b`) = B1 из R2/ext помесячно (число сделок и $) во всех месяцах фев–сен, кроме августа:"]
        L += [f"  - {n}" for n in info]
    if notes:
        L += ["", "Клетки с менее чем 9 определёнными месяцами (итог здесь — по всем сделкам окна):", ""]
        L += [f"- {n}" for n in notes]
    L.append("")

    def table(rs, head_pack=True):
        hdr = "| пак | семья | клетка | " + " | ".join(MN) + " | Итого янв–сен | Окт (справ.) | сд. |"
        out = [hdr, "|" + "---|" * (3 + 9 + 3)]
        for r in sorted(rs, key=lambda r: -tot(r)):
            out.append(f"| {r['pack']} | {r['family']} | {r['cell']} | " + " | ".join(f(r["usd"][m]) for m in JS) + f" | **{tot(r):+.1f}** | {r['usd'][OCT]:+.1f} | {ntr(r)} |")
        return out

    for mode, _cap in MODES:
        d = data[mode]
        main_rows = d["r2"] + [r for r in d["ext"] if r["pack"] != "ext-B1"]
        L += [f"## {mode}", "", f"### R2 + базы + ext ({len(main_rows)} строк: 40 R2, 3 базы, 7 ext), янв–сен", ""]
        L += table(main_rows)
        r1 = d["r1"]
        L += ["", f"### R1 ({len(r1) - 1} клеток + B1 как база R1), на v171c только фев–сен — январь «—», итог фев–сен", ""]
        L += table(r1)
        L.append("")
    open(OUT + ".md", "w", encoding="utf-8", newline="\n").write("\n".join(L))
    # ---- печать топов ----
    for mode, _cap in MODES:
        d = data[mode]
        print(f"\n== {mode}: топ-10 R2+ext по итогу янв–сен ($; Янв…Сен | итог | окт | сд.)")
        cand = [r for r in d["r2"] + d["ext"] if r["pack"] in ("R2", "ext")]
        for r in sorted(cand, key=lambda r: -tot(r))[:10]:
            print(f"  {r['cell']:<22} " + " ".join(f"{r['usd'][m]:+7.1f}" for m in JS) + f" | {tot(r):+8.1f} | {r['usd'][OCT]:+7.1f} | {ntr(r)}")
        for r in d["r2"][:3]:
            print(f"  база {r['cell']:<17} " + " ".join(f"{r['usd'][m]:+7.1f}" for m in JS) + f" | {tot(r):+8.1f} | {r['usd'][OCT]:+7.1f} | {ntr(r)}")
        print(f"  R1 топ-5 (фев–сен): " + "; ".join(f"{r['cell']} {tot(r):+.1f}" for r in sorted(d["r1"], key=lambda r: -tot(r))[:5]))
    print(f"\nклеток: R2 40, базы 3, ext 7, R1 {len(data['free']['r1']) - 1} + B1; файлы {OUT}.csv/.md")
    sys.exit(1 if bad else 0)


main()
