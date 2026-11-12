#!/usr/bin/env python3
"""TK-531: окна лестницы нагрузки Binance (json со srv /data/bnws-ladder) -> windows.csv (одно окно — строка) и таблица ступеней (markdown на stdout).
Запуск: table.py <каталог с json> <windows.csv>
Ступень = (вид, группа, потоков); окна 12.11 00:36–00:45 UTC повтора ступени 522 книги (суффикс -tk531b) — отдельная ступень, окна без сообщений видны в столбце «окон».
"""
import collections
import csv
import datetime
import glob
import json
import os
import re
import sys

src, out = sys.argv[1], sys.argv[2]
steps = collections.defaultdict(list)
rows = []
for f in sorted(glob.glob(os.path.join(src, "*.json"))):
    r = json.load(open(f))
    kind, group, n, rep = re.match(r"(\w+)-(\w+)-n(\d+)-r(\d)", os.path.basename(f)).groups()
    if "tk531b" in f:
        group = "all-повтор"
    steps[(kind, group, int(n))].append(r)
    t = datetime.datetime.fromtimestamp(r["t_start"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows.append([kind, group, n, rep, t, r["dur"], r.get("rtt_ms"), r.get("msgs"), r.get("msg_s"), r.get("kb_s"), r.get("peak_msg_s"), r.get("lag_p50"), r.get("lag_p90"), r.get("lag_p99"), r.get("lag_max"),
                 r.get("disconnects"), r.get("silent_s"), r.get("cpu_s"), os.path.basename(f)])
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["kind", "group", "streams", "repeat", "t_start_utc", "dur_s", "rtt_connect_ms", "msgs", "msg_s", "kb_s", "peak_msg_s", "lag_p50_ms", "lag_p90_ms", "lag_p99_ms", "lag_max_ms", "disconnects", "silent_s", "cpu_s", "file"])
    for r in sorted(rows, key=lambda x: (x[0], x[1], int(x[2]), x[3], x[4])):
        w.writerow([round(v, 1) if isinstance(v, float) else v for v in r])


def rng(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    lo, hi = round(min(xs)), round(max(xs))
    return str(lo) if lo == hi else f"{lo}–{hi}"


print("| вид | группа | потоков | окон | сообщ/с | КБ/с | пик сообщ/с | обрывов | тишина >5 с | лаг p50, мс | лаг p99 по окнам, мс | CPU, % ядра |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for (kind, group, n), L in sorted(steps.items()):
    ok = [r for r in L if r.get("msgs")]
    empty = len(L) - len(ok)
    print(f"| {kind} | {group} | {n} | {len(L)}{f' ({empty} без данных)' if empty else ''} | {rng([r['msg_s'] for r in ok])} | {rng([r['kb_s'] for r in ok])} | {rng([r['peak_msg_s'] for r in ok])} | {sum(r['disconnects'] for r in L)} | "
          f"{max((r['silent_s'] for r in ok), default='-')} | {rng([r['lag_p50'] for r in ok])} | {rng([r['lag_p99'] for r in ok])} | {rng([r['cpu_s'] / r['dur'] * 100 for r in ok])} |")
