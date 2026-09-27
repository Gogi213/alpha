#!/usr/bin/env python3
"""Проверка дашборда кодом (TK-003, TK-002 п.4, В-85): числа страницы против исходных файлов и между блоками.

    dashboard-check.py --page data/titration-dashboard/index-v29.html \\
        --source data/titration-dashboard/data-merged-v17.json \\
        --source nh=data/titration-dashboard/kpi-dash.json --source p05=data/titration-dashboard/p05-dash.json \\
        [--published <страница, снятая с артефакта>] [--json out.json]

Итог — «ОК / смотреть / сломано» со списком расхождений; код выхода 0 / 1 / 2.
(а) источник: блок данных страницы == исходный файл (`ключ=файл` — один блок; без ключа — все общие ключи);
    между блоками: плитки читают счёт `account`, «Причины выхода» — `kpi.reasons`, гистограмма и «По монетам» —
    `trades`; при наличии счёта сделки и деньги этих блоков обязаны совпасть со счётом (известный случай T-21:
    сен главный — счёт 155/$110,99, блоки 152/$103,46).
(б) `--published`: данные опубликованной страницы == данные проверяемой сборки (иначе проверена старая страница).
Подписи и текст не проверяются (Jev — отдельно, печать «мнение модели»).
"""
import argparse
import hashlib
import json
import re
import sys

OK, LOOK, BROKEN = "ОК", "смотреть", "сломано"
RANK = {OK: 0, LOOK: 1, BROKEN: 2}
R_NET, R_SYM, R_REASON, R_USD = 4, 1, 6, 8  # раскладка строки сделки — шаблон, `const R`
USD_TOL = 0.02  # $ — округление до цента в источнике; больше — расхождение
USD_BROKEN = 1.0  # $ — расхождение, заметное на странице (money() печатает центы)


def page_data(path):
    with open(path, encoding="utf-8") as f:
        s = f.read()
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', s, re.S)
    if not m:
        raise SystemExit(f"{path}: нет блока <script id=\"data\">")
    raw = m.group(1)
    return json.loads(raw.replace("<\\/", "</")), hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def trade_cash(r, pos_usd):
    return r[R_NET] / 1e4 * (r[R_USD] if len(r) > R_USD and r[R_USD] is not None else pos_usd)


def usd_level(a, b, tol=USD_TOL):
    d = abs(a - b)
    return OK if d <= tol + 1e-9 else (LOOK if d < USD_BROKEN else BROKEN)


def check_blocks(D):
    """Сверка блоков между собой по каждому периоду × варианту. Возвращает [(уровень, где, что)]."""
    out = []
    pos = D.get("position_usd", 500.0)
    acc_all = {p: dict(v) for p, v in (D.get("account") or {}).items()}
    for p, v in ((D.get("p05") or {}).get("account") or {}).items():  # страница вливает счёт П-05 в account
        acc_all.setdefault(p, {}).update(v)
    kpi_all, tr_all = D.get("kpi") or {}, D.get("trades") or {}
    # TK-007 v33: вариант со своей пометкой «сделок на странице нет» (страница пишет причину) и число закрытий
    # portfolio-sim (`n_closes`, ps-closes прогона) — плитки обязаны совпасть с закрытиями
    meta = {v["key"]: v for v in D.get("variants") or []}
    for key, m in meta.items():
        for p, nc in (m.get("n_closes") or {}).items():
            a = (acc_all.get(p) or {}).get(key)
            if not a:
                out.append((BROKEN, f"{p}/{key}", f"закрытий portfolio-sim {nc}, а счёта на странице нет"))
            elif a["n"] != nc:
                out.append((BROKEN, f"{p}/{key}", f"плитки {a['n']} сделок (счёт) ≠ закрытий portfolio-sim {nc}"))
    periods = sorted(set(acc_all) | set(kpi_all) | set(tr_all))
    for p in periods:
        variants = sorted(set(acc_all.get(p, {})) | set(kpi_all.get(p, {})) | set(tr_all.get(p, {})))
        for v in variants:
            where = f"{p}/{v}"
            acc, kpi, tr = acc_all.get(p, {}).get(v), kpi_all.get(p, {}).get(v), tr_all.get(p, {}).get(v)
            is_p05 = v.startswith("p05:") or bool((meta.get(v) or {}).get("trades_note"))
            tr_usd = sum(trade_cash(r, pos) for r in tr) if tr else None
            # плитки (счёт) против «Результаты сделок» / «По монетам» (trades)
            if acc and tr:
                if acc["n"] != len(tr):
                    out.append((BROKEN, where, f"плитки {acc['n']} сделок (счёт) ≠ гистограмма/«По монетам» {len(tr)} (trades)"))
                lv = usd_level(acc["net_usd"], tr_usd)
                if lv != OK:
                    out.append((lv, where, f"плитки ${acc['net_usd']:.2f} (счёт) ≠ «По монетам» ${tr_usd:.2f} (trades)"))
            # плитки (счёт) против «Причины выхода» (kpi.reasons)
            reasons = (kpi or {}).get("reasons") or {}
            if reasons:
                rn = sum(r["n"] for r in reasons.values())
                ru = sum(r["usd"] for r in reasons.values())
                if acc and acc["n"] != rn:
                    out.append((BROKEN, where, f"плитки {acc['n']} сделок (счёт) ≠ «Причины выхода» {rn} (kpi запасной)"))
                if acc:
                    lv = usd_level(acc["net_usd"], ru)
                    if lv != OK:
                        out.append((lv, where, f"плитки ${acc['net_usd']:.2f} (счёт) ≠ «Причины выхода» ${ru:.2f} (kpi запасной)"))
                if tr:  # причины против самих сделок
                    by = {}
                    for r in tr:
                        by.setdefault(r[R_REASON], [0, 0.0])
                        by[r[R_REASON]][0] += 1
                        by[r[R_REASON]][1] += trade_cash(r, pos)
                    for k in sorted(set(by) | set(reasons)):
                        n1, u1 = by.get(k, [0, 0.0])
                        n2, u2 = reasons[k]["n"] if k in reasons else 0, reasons[k]["usd"] if k in reasons else 0.0
                        if n1 != n2:
                            out.append((BROKEN, where, f"«Причины выхода» {k}: {n2} сд. ≠ сделок с этой причиной {n1}"))
                        elif usd_level(u1, u2) != OK:
                            out.append((usd_level(u1, u2), where, f"«Причины выхода» {k}: ${u2:.2f} ≠ по сделкам ${u1:.2f}"))
            # kpi против trades (когда счёта нет, плитки читают kpi)
            if kpi and tr and not acc:
                if kpi["n"] != len(tr):
                    out.append((BROKEN, where, f"плитки {kpi['n']} сделок (kpi) ≠ trades {len(tr)}"))
                elif usd_level(kpi["net_usd"], tr_usd) != OK:
                    out.append((usd_level(kpi["net_usd"], tr_usd), where, f"плитки ${kpi['net_usd']:.2f} (kpi) ≠ trades ${tr_usd:.2f}"))
            # дневная кривая — сумма дней == итог
            for name, src in (("счёт", acc), ("kpi", kpi)):
                if src and src.get("daily_usd"):
                    ds = sum(src["daily_usd"].values())  # дни округлены до цента: допуск — полцента на день
                    lv = usd_level(ds, src["net_usd"], max(USD_TOL, 0.005 * len(src["daily_usd"])))
                    if lv != OK and (name == "счёт" or not acc):  # запасной kpi при счёте — уже выше
                        out.append((lv, where, f"календарь ({name}) Σ дней ${ds:.2f} ≠ итог ${src['net_usd']:.2f}"))
            # доходность плитки == $ / депозит
            if acc and "net_pct" in acc:
                pct = acc["net_usd"] / D["deposit_usd"] * 100
                if abs(pct - acc["net_pct"]) > 0.01:
                    out.append((BROKEN, where, f"«Доходность» {acc['net_pct']} % ≠ ${acc['net_usd']} / ${D['deposit_usd']:.0f} = {pct:.2f} %"))
            # блоки пусты, хотя плитки есть сделки (у П-05 сделок не собирали — страница это пишет)
            n_tiles = (acc or kpi or {}).get("n", 0)
            if n_tiles and not tr and not is_p05:
                out.append((LOOK, where, f"плитки {n_tiles} сделок, а гистограмма/«По монетам» пусты (trades нет)"))
    return out


def check_sources(D, sources):
    out = []
    for spec in sources:
        key, path = spec.split("=", 1) if "=" in spec and not spec.split("=", 1)[0].count("/") else (None, spec)
        with open(path, encoding="utf-8") as f:
            S = json.load(f)
        pairs = [(key, S)] if key else [(k, S[k]) for k in S if k in D]
        if not pairs:
            out.append((LOOK, path, "ни одного общего блока со страницей"))
        for k, val in pairs:
            if k not in D:
                out.append((BROKEN, k, f"блока нет на странице (источник {path})"))
            elif D[k] != val:
                out.append((BROKEN, k, f"блок страницы ≠ источник {path}" + diff_hint(D[k], val)))
    return out


def diff_hint(a, b, path=""):
    """Первое расхождение — адресом, чтобы смотреть прицельно, а не всю страницу."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if a.get(k) != b.get(k):
                return diff_hint(a.get(k), b.get(k), f"{path}.{k}")
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return diff_hint(x, y, f"{path}[{i}]")
    return f": {path or '.'} страница {str(a)[:60]} ≠ источник {str(b)[:60]}"


def run(page, sources=(), published=None):
    D, h = page_data(page)
    items = check_sources(D, sources) + check_blocks(D)
    if published:
        _, hp = page_data(published)
        if hp != h:
            items.append((BROKEN, "публикация", f"данные опубликованной страницы {hp} ≠ сборки {h} — проверена не та версия"))
    level = max((i[0] for i in items), key=RANK.get, default=OK)
    return {"page": page, "data_sha": h, "generated_utc": D.get("generated_utc"), "level": level,
            "published_checked": bool(published), "items": [{"level": a, "where": b, "what": c} for a, b, c in items]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", required=True)
    ap.add_argument("--source", action="append", default=[], help="[ключ=]файл.json")
    ap.add_argument("--published", help="страница, снятая с артефакта (Artifact read), — сверка версии")
    ap.add_argument("--json", help="записать итог в файл")
    a = ap.parse_args()
    res = run(a.page, a.source, a.published)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    items = sorted(res["items"], key=lambda i: -RANK[i["level"]])
    print(f"{res['level'].upper()} — {res['page']} (данные {res['data_sha']}, собрано {res['generated_utc']});"
          f" расхождений {len(items)}" + ("" if res["published_checked"] else "; опубликованная не сверена (--published)"))
    for i in items:
        print(f"  [{i['level']}] {i['where']}: {i['what']}")
    sys.exit(RANK[res["level"]])


if __name__ == "__main__":
    main()
