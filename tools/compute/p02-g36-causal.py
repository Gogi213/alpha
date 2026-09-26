#!/usr/bin/env python3
"""П-02, Г-36 (H6, айсберг) — причинная версия по поправке §12 (`dadd428`, утверждена 26.09, запись
Судьи `docs/research/reviews/P-02-2026-09-26-owner-delegated-decisions.md`).

Флаг касания k уровня L — только по **прошлым** касаниям того же уровня (одни сутки кэша D20):
сумма `traded_during` касаний с `touch_index < k` ≥ `size_max_before` касания k (порог 1,0 —
определение «проторговал не меньше пикового видимого размера»). Сравнение — только среди касаний с
`touch_index ≥ 1`: флаг 1 против флага 0; первые касания (k = 0) ни в одну корзину не входят. Уровень —
ключ (`side`, `price_tick`, `birth_ms`). Край суток: касания уровня за прошлые сутки не видны — флаг
занижен (записано в §12).

Режимы:
  precount — число касаний `touch_index ≥ 1` и с флагом по месяцам; колонка исхода НЕ читается
             (печатается в отчёт до счёта долей).
  scan     — доли `bounced` по корзинам по суткам -> JSON формата `p02-wave3-analyze.py`
             (вариант `g36_iceberg`, корзины `yes`/`no`), дальше `p02-wave3-analyze.py analyze`.

Использование (на Steam Deck, пути — как у `p02-wall.py`):
    python3 p02-g36-causal.py precount --dirs epochs/e-aug/study/approaches/D20,...
    python3 p02-g36-causal.py scan --dirs ... --out p02-g36-counts.json
Каталоги — корни кэша D20 эпох; сутки берутся `YYYY-MM-DD` в диапазоне `--from`/`--to`.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

# В-105: TRX вне торгового пула — исключается по имени, как в блоке A и второй очереди
EXCLUDE_SYMBOLS = {"TRXUSDT"}
RATIO = 1.0  # определение, §12


def day_dirs(roots: List[str], day_from: str, day_to: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for root in roots:
        for d in sorted(glob.glob(os.path.join(root, "20??-??-??"))):
            day = os.path.basename(d)
            if day_from <= day <= day_to:
                if day in out:
                    raise RuntimeError(f"день {day} в двух корнях: {out[day]} и {d}")
                out[day] = d
    return out


def flags_of_file(path: str, read_outcome: bool):
    """Список (flag, bounced|None) для касаний touch_index >= 1 одного файла touches-<SYM>.csv."""
    with open(path, encoding="utf-8", newline="") as f:
        first = f.readline()
        if not first.startswith("#"):
            f.seek(0)
        r = csv.reader(f)
        h = next(r)
        ix = {name: h.index(name) for name in
              ("side", "price_tick", "birth_ms", "touch_index", "size_max_before", "traded_during")}
        i_end = h.index("ended_by_death") if read_outcome else None
        levels: Dict[Tuple[str, str, str], List[tuple]] = defaultdict(list)
        for row in r:
            try:
                key = (row[ix["side"]], row[ix["price_tick"]], row[ix["birth_ms"]])
                k = int(row[ix["touch_index"]])
                smb = float(row[ix["size_max_before"]])
                td = float(row[ix["traded_during"]])
            except (ValueError, IndexError):
                continue
            end = None
            if i_end is not None:
                end = row[i_end].strip().lower() == "true"
            levels[key].append((k, smb, td, end))
    out = []
    for touches in levels.values():
        touches.sort(key=lambda t: t[0])
        cum = 0.0
        for k, smb, td, end in touches:
            if k >= 1:
                flag = cum >= RATIO * max(smb, 1.0)
                out.append((flag, None if end is None else (0 if end else 1)))
            cum += td
    return out


def load_failed(dirs: str) -> Dict[str, set]:
    """{день: {символ}} — символо-сутки без `verify ok` (К1), выпавшие при пересчёте второй очереди
    (`<день>.failed.txt`); исключаются, чтобы выборка совпадала со второй и третьей очередью."""
    out: Dict[str, set] = defaultdict(set)
    for d in [x for x in (dirs or "").split(",") if x]:
        for path in glob.glob(os.path.join(d, "20??-??-??.failed.txt")):
            day = os.path.basename(path)[:10]
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        out[day].add(line.split()[0])
    return out


FAILED: Dict[str, set] = {}


def iter_files(ddir: str):
    day = os.path.basename(ddir.rstrip("/"))
    for path in sorted(glob.glob(os.path.join(ddir, "touches-*.csv"))):
        sym = os.path.basename(path)[len("touches-"):-len(".csv")]
        if sym in EXCLUDE_SYMBOLS or sym in FAILED.get(day, ()):
            continue
        yield sym, path


def cmd_precount(args) -> int:
    days = day_dirs(args.dirs.split(","), args.day_from, args.day_to)
    per_month = defaultdict(lambda: [0, 0])  # [касаний k>=1, из них с флагом]
    for day, ddir in sorted(days.items()):
        for _sym, path in iter_files(ddir):
            for flag, _ in flags_of_file(path, read_outcome=False):
                c = per_month[day[:7]]
                c[0] += 1
                c[1] += int(flag)
        print(f"[precount] {day}", file=sys.stderr)
    res = {m: {"touches_k_ge_1": n, "flagged": f, "share_flagged": (f / n if n else None)}
           for m, (n, f) in sorted(per_month.items())}
    print(json.dumps(res, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
    return 0


def cmd_scan(args) -> int:
    days = day_dirs(args.dirs.split(","), args.day_from, args.day_to)
    day_counts = {}
    for day, ddir in sorted(days.items()):
        counts = {"yes": [0, 0], "no": [0, 0]}
        for _sym, path in iter_files(ddir):
            for flag, bounced in flags_of_file(path, read_outcome=True):
                c = counts["yes" if flag else "no"]
                c[0] += bounced
                c[1] += 1
        day_counts[day] = {"month": day[:7], "variants": {"g36_iceberg": counts}}
        print(f"[scan] {day}: {counts}", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"day_counts": day_counts}, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("precount", cmd_precount), ("scan", cmd_scan)):
        s = sub.add_parser(name)
        s.add_argument("--dirs", required=True, help="корни кэша D20 через запятую")
        s.add_argument("--from", dest="day_from", default="2026-08-01")
        s.add_argument("--to", dest="day_to", default="2026-09-23")
        s.add_argument("--out", required=(name == "scan"), default=None)
        s.add_argument("--exclude-failed-dirs", default="",
                       help="каталоги с <день>.failed.txt второй очереди (К1), через запятую")
        s.set_defaults(func=fn)
    args = p.parse_args(argv)
    FAILED.update(load_failed(args.exclude_failed_dirs))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
