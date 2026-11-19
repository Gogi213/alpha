#!/usr/bin/env python3
"""П-42 §13.24 (TK-599), шаг 0: перечень суток по источникам для окна 01.07–13.11 (+ прогрев с 17.06) и реальные дыры. Без P&L.

  python3 tk599_inventory.py <каталог вывода> [--d0 2026-06-17] [--d1 2026-11-13]
Запуск на srv под systemd-run (каталоги большие): Nice=19, IOSchedulingClass=idle.
Источники (первый найденный по порядку — «источник дня»): минутки `<SYM>/<день>.csv` (jul, aug, klines74), сделки `<SYM>/<SYM>-aggTrades-<день>.zip`
(jul, aug, /data/aggtrades/binance), метрики OI `/data/p42-metrics/<SYM>/<SYM>-metrics-<день>.zip`, книга `/data/p42-book/zip`, фандинг.
Вывод: inventory.json (по источникам: число монет на сутки; по монетам: первый день, дыры после первого дня, 0-байтные файлы) + inventory.txt (сводка).
Дыра монеты = сутки без файла после первого имеющегося дня монеты (листинг — не дыра). Список монет — /data/p42-t566/symbols.txt (566, без перевода строки в конце).
"""
import argparse
import datetime
import json
import os
import sys

SYMS = "/data/p42-t566/symbols.txt"
SRC = {
    "klines": [("jul", "/data/p42-tk596/jul/klines/{s}/{d}.csv"), ("aug", "/data/p42-tk596/aug/klines/{s}/{d}.csv"),
               ("main", "/data/p42-t566/klines74/{s}/{d}.csv")],
    "trades": [("jul", "/data/p42-tk596/jul/aggtrades/{s}/{s}-aggTrades-{d}.zip"), ("aug", "/data/p42-tk596/aug/aggtrades/{s}/{s}-aggTrades-{d}.zip"),
               ("main", "/data/aggtrades/binance/{s}/{s}-aggTrades-{d}.zip")],
    "metrics": [("metrics", "/data/p42-metrics/{s}/{s}-metrics-{d}.zip")],
    "book": [("book", "/data/p42-book/zip/{s}/{s}-bookDepth-{d}.zip")],
}


def days(d0, d1):
    a, b = datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1)
    return [(a + datetime.timedelta(i)).isoformat() for i in range((b - a).days + 1)]


def have(tpl, s, d):
    p = tpl.format(s=s, d=d)
    try:
        return os.stat(p).st_size
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--d0", default="2026-06-17")
    ap.add_argument("--d1", default="2026-11-13")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    syms = [x for x in open(SYMS, encoding="utf-8").read().replace("\n", ",").split(",") if x]
    ds = days(a.d0, a.d1)
    res = {"d0": a.d0, "d1": a.d1, "n_syms": len(syms), "sources": {}}
    for kind, srcs in SRC.items():
        per_day = {d: 0 for d in ds}
        per_src = {n: 0 for n, _ in srcs}
        coins = {}
        for s in syms:
            got = {}
            zero = []
            for d in ds:
                for n, tpl in srcs:
                    sz = have(tpl, s, d)
                    if sz is not None:
                        if sz == 0:
                            zero.append(d)
                        else:
                            got[d] = n
                        break
            for d in got:
                per_day[d] += 1
                per_src[got[d]] += 1
            if not got:
                coins[s] = {"first": None, "n": 0, "gaps": [], "zero": zero}
                continue
            first = min(got)
            gaps = [d for d in ds if d > first and d not in got]
            coins[s] = {"first": first, "n": len(got), "gaps": gaps, "zero": zero}
        res["sources"][kind] = {"per_day": per_day, "per_src": per_src, "coins": coins}
    json.dump(res, open(os.path.join(a.out, "inventory.json"), "w"), ensure_ascii=False)
    L = ["П-42 §13.24 шаг 0: перечень суток %s…%s, монет в списке %d" % (a.d0, a.d1, len(syms))]
    for kind, r in res["sources"].items():
        coins = r["coins"]
        with_data = [s for s, c in coins.items() if c["n"]]
        L.append("")
        L.append("== %s: монет с данными %d из %d; файлов по источникам %s" % (kind, len(with_data), len(syms), r["per_src"]))
        pd = r["per_day"]
        mons = {}
        for d, n in pd.items():
            mons.setdefault(d[:7], []).append(n)
        for m, v in sorted(mons.items()):
            L.append("   %s: суток %d, монет на сутки min %d / max %d, всего монето-суток %d" % (m, len(v), min(v), max(v), sum(v)))
        gl = [(s, c) for s, c in coins.items() if c["gaps"]]
        tot = sum(len(c["gaps"]) for _, c in gl)
        L.append("   дыры внутри листинга: монет %d, монето-суток %d; 0-байтных файлов %d" % (len(gl), tot, sum(len(c["zero"]) for c in coins.values())))
        for s, c in sorted(gl, key=lambda x: -len(x[1]["gaps"]))[:40]:
            g = c["gaps"]
            L.append("   - %s: первый день %s, дыр %d: %s%s" % (s, c["first"], len(g), ",".join(g[:6]), "…" if len(g) > 6 else ""))
        nod = [s for s in syms if not coins[s]["n"]]
        if nod:
            L.append("   без данных в окне: %d: %s" % (len(nod), ",".join(nod[:20])))
    open(os.path.join(a.out, "inventory.txt"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:6]))


if __name__ == "__main__":
    sys.exit(main())
