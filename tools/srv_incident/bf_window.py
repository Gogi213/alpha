#!/usr/bin/env python3
"""TK-581: итог дозапроса сделок (`backfill-<биржа>-<запуск>.tsv.zst`, строки `rg`) за окно по времени ПРОПУСКА (последний столбец, мс приёма последней сделки до пропуска).
Печатает по бирже и минуте пропуска: пропусков, номеров получено REST'ом, нет на бирже, потеряно, статусы; потерянные — построчно.
rg: rg <мс> <символ> <от> <до> <найден,мс> <получено номеров> <статус> <попыток> <шард> <нет на бирже> <время пропуска,мс>
python3 bf_window.py <HH:MM> <HH:MM> [день]"""
import sys, glob, subprocess, collections, datetime as dt
a, b = sys.argv[1], sys.argv[2]
day = sys.argv[3] if len(sys.argv) > 3 else "2026-11-16"
d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
f = lambda s: (d0 + int(s[:2]) * 3600 + int(s[3:5]) * 60) * 1000
t0, t1 = f(a), f(b)
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data",
         "bingx": "/data/collect/bingx/data", "gate": "/data/collect/gate/data"}
for ex, root in ROOTS.items():
    files = sorted(glob.glob(f"{root}/{day}/backfill-*.tsv.zst"))
    per = collections.defaultdict(lambda: collections.Counter())
    lost_rows = []
    nrg = 0
    for p in files:
        out = subprocess.run(["zstd", "-dcq", p], capture_output=True).stdout.decode("utf-8", "replace")
        for ln in out.split("\n"):
            q = ln.split("\t")
            if len(q) < 12 or q[0] != "rg":
                continue
            try:
                gap_ms = int(q[11]); lo = int(q[3]); hi = int(q[4]); got = int(q[6]); absent = int(q[10])
            except ValueError:
                continue
            if not (t0 <= gap_ms < t1):
                continue
            nrg += 1
            n = hi - lo + 1
            lost = max(0, n - got - absent)
            m = dt.datetime.fromtimestamp(gap_ms / 1000, dt.timezone.utc).strftime("%H:%M")
            c = per[m]
            c["gaps"] += 1; c["ids"] += n; c["got"] += got; c["absent"] += absent; c["lost"] += lost; c["st_" + q[7]] += 1
            if lost:
                lost_rows.append((m, q[2], lo, hi, got, absent, lost, q[7], q[9]))
    print(f"### {ex}: файлов {len(files)}, пропусков в окне {nrg}")
    tot = collections.Counter()
    for m in sorted(per):
        print(" ", m, dict(per[m])); tot.update(per[m])
    print("  ИТОГО", dict(tot))
    for r in lost_rows[:25]:
        print("   lost:", r)
