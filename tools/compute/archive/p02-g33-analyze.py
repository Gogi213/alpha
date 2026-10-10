#!/usr/bin/env python3
"""П-02, Г-33 (H2, декоративная цепочка) — причинная версия §12 (`dadd428`, утверждена 26.09). Читает
компактные сутки `p02-g33-causal-join.py` (через обёртку пересчёта `lob levels`):
`symbol,day_utc,side,price_tick,touch_index,ended_by_death,chain_len,chain_stop`.

Режимы:
  precount — ДО исходов (колонка `ended_by_death` не читается): по месяцам — касаний, корзины длины
             цепочки (0 / 1 / 2 / ≥3), чем кончилась цепочка (`none` — кандидатов нет, `nonlink` —
             кандидат торговался, `ambiguous` — неоднозначное звено = разрыв), доля неоднозначных
             звеньев среди попыток продлить цепочку и доля касаний базы (длина 0), у которых цепочка
             оборвалась на первом же неоднозначном звене (длина 0 = «неизвестно», замечание Судьи).
  scan     — доли `bounced` по корзинам по суткам → JSON формата `p02-wave3-analyze.py`: вариант
             `g33_chain2` (основной: длина ≥ 2 против 0) и `g33_chain3` (сосед: ≥ 3 против 0), корзины
             `yes`/`no`; длины 1 (у соседа 1–2) — вне корзин (§12).

Без TRX (В-105), как блок A и вторая/третья очереди.
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import json
import os
import sys
from collections import defaultdict

EXCLUDE_SYMBOLS = {"TRXUSDT"}


def day_files(dirs):
    out = {}
    for d in [x for x in dirs.split(",") if x]:
        for p in sorted(glob.glob(os.path.join(d, "20*.csv.gz"))):
            day = os.path.basename(p)[:10]
            if day in out:
                raise RuntimeError(f"день {day} дважды: {out[day]} и {p}")
            out[day] = p
    return out


def rows(path, read_outcome):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        h = next(r)
        i_sym, i_len, i_stop = h.index("symbol"), h.index("chain_len"), h.index("chain_stop")
        i_end = h.index("ended_by_death") if read_outcome else None
        for row in r:
            try:
                if row[i_sym] in EXCLUDE_SYMBOLS:
                    continue
                n = int(row[i_len])
                stop = row[i_stop]
                end = (row[i_end].strip().lower() == "true") if i_end is not None else None
            except (ValueError, IndexError):
                continue
            yield n, stop, end


def cmd_precount(a):
    days = day_files(a.dirs)
    agg = defaultdict(lambda: {"touches": 0, "len": defaultdict(int), "stop": defaultdict(int),
                               "links": 0, "attempts": 0, "ambiguous": 0, "len0_ambiguous": 0})
    for day, p in sorted(days.items()):
        m = agg[day[:7]]
        for n, stop, _ in rows(p, False):
            m["touches"] += 1
            m["len"]["0" if n == 0 else "1" if n == 1 else "2" if n == 2 else ">=3"] += 1
            m["stop"][stop] += 1
            m["links"] += n
            m["attempts"] += n + 1  # каждое звено — попытка; последняя попытка кончилась stop
            if stop == "ambiguous":
                m["ambiguous"] += 1
                if n == 0:
                    m["len0_ambiguous"] += 1
    res = {}
    for mon, m in sorted(agg.items()):
        len0 = m["len"]["0"]
        res[mon] = {
            "touches": m["touches"], "len_buckets": dict(m["len"]), "chain_stop": dict(m["stop"]),
            "share_ambiguous_attempts": m["ambiguous"] / m["attempts"] if m["attempts"] else None,
            "len0_touches": len0, "len0_ambiguous_first_link": m["len0_ambiguous"],
            "share_len0_ambiguous": m["len0_ambiguous"] / len0 if len0 else None,
        }
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


def cmd_scan(a):
    days = day_files(a.dirs)
    day_counts = {}
    for day, p in sorted(days.items()):
        c2 = {"yes": [0, 0], "no": [0, 0]}
        c3 = {"yes": [0, 0], "no": [0, 0]}
        for n, _stop, end in rows(p, True):
            b = 0 if end else 1
            if n == 0:
                for c in (c2, c3):
                    c["no"][0] += b
                    c["no"][1] += 1
            if n >= 2:
                c2["yes"][0] += b
                c2["yes"][1] += 1
            if n >= 3:
                c3["yes"][0] += b
                c3["yes"][1] += 1
        day_counts[day] = {"month": day[:7], "variants": {"g33_chain2": c2, "g33_chain3": c3}}
        print(f"[scan] {day}", file=sys.stderr)
    json.dump({"day_counts": day_counts}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("precount", cmd_precount), ("scan", cmd_scan)):
        s = sub.add_parser(name)
        s.add_argument("--dirs", required=True, help="каталоги компактных суток g33 через запятую")
        s.add_argument("--out", required=(name == "scan"), default=None)
        s.set_defaults(func=fn)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
