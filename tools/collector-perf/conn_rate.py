#!/usr/bin/env python3
"""Подключения с IP srv по биржам (боевые коллекторы + руки аудита): сколько сокетов на :443 сейчас и максимум подключений в 1 с / за 5 мин по событиям `connected` за последние N часов, плюс ошибки подключения.
Лимиты: BingX ≤ 60 сокетов на IP (код 100419), OKX 3 подключения/с на IP, Binance — повторные разрывы могут дать бан IP (замеренный максимум 179 за 5 мин), Bybit 500 подключений за 5 мин на домен.
usage: conn_rate.py [часов=1]; читает файлы событий и `ss`, ничего не пишет."""
import bisect
import collections
import glob
import json
import re
import subprocess
import sys
import time

H = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
now = time.time()
roots = {"binance": ["/data/collect/binance/data"], "bybit": ["/data/turbo-probe/soak-bybit/data"], "okx": ["/data/collect/okx/data"], "bingx": ["/data/collect/bingx/data"]}
for ex in roots:
    roots[ex] += glob.glob(f"/data/perf/audit/{ex}-*/data")
LIM = {"binance": "бан при повторных разрывах (в 5 мин было ≤ 179)", "bybit": "500/5 мин на домен", "okx": "3/с на IP", "bingx": "≤ 60 сокетов на IP (100419)"}
# сокеты на :443 по юнитам
socks = collections.Counter()
out = subprocess.run(["ss", "-tnHp", "state", "established"], capture_output=True, text=True).stdout
for ln in out.splitlines():
    p = ln.split()
    if len(p) < 4 or not p[3].endswith(":443"):
        continue
    m = re.search(r'pid=(\d+)', ln)
    if not m:
        continue
    try:
        cg = open(f"/proc/{m.group(1)}/cgroup").read().strip().split("/")[-1]
    except Exception:
        cg = "?"
    socks[cg] += 1
for ex, rs in roots.items():
    key = {"binance": "binance", "bybit": "bybit", "okx": "okx", "bingx": "bingx"}[ex]
    mine = {u: n for u, n in socks.items() if key in u}
    ts, errs, since = [], collections.Counter(), now - H * 3600
    for r in rs:
        for f in glob.glob(r + "/events-*.jsonl"):
            for ln in open(f, errors="replace"):
                if "connected" not in ln and "error" not in ln and "fail" not in ln and "reject" not in ln:
                    continue
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                t = j.get("ts_ms", 0) / 1000
                if t < since:
                    continue
                e = j.get("ev")
                if e == "connected":
                    ts.append(t)
                elif e in ("error", "connect_failed", "connect_error", "connect_fail") or "reject" in (e or ""):
                    errs[(e, str(j.get("err") or j.get("detail") or j.get("reason") or "")[:70])] += 1
    ts.sort()
    m1 = max((bisect.bisect_right(ts, t + 1) - bisect.bisect_left(ts, t) for t in ts), default=0)
    m5 = max((bisect.bisect_right(ts, t + 300) - bisect.bisect_left(ts, t) for t in ts), default=0)
    names = ", ".join("%s %d" % (u.replace(".service", ""), n) for u, n in sorted(mine.items()))
    print(f"{ex}: сокетов {sum(mine.values())} ({names}); лимит: {LIM[ex]}")
    print(f"   за {H:g} ч: подключений {len(ts)}, максимум в 1 с {m1}, за 5 мин {m5}; ошибки: {dict(errs) or 'нет'}")
