#!/usr/bin/env python3
"""TK-048 п.1 CEO 06.10: байты D20 (approaches, читаются целиком) и бинлогов по дискам и месяцам, без обхода /data (os.scandir по ссылкам)."""
import os, sys, collections
tot = collections.defaultdict(int)
for mon in ("jan", "feb"):
    E = f"/data/tk046/{mon}/home/alpha/epochs/e-{mon}/study"
    for d in sorted(x for x in os.listdir(E + "/approaches/D20")):
        pass
    for dd in sorted(os.listdir(E + "/approaches/D20")):
        for e in os.scandir(f"{E}/approaches/D20/{dd}"):
            t = os.path.realpath(e.path) if e.is_symlink() else e.path
            disk = "sda" if t.startswith("/alpha-sda") else "sdb"
            sz = os.stat(t).st_size
            tot[(mon, "appr", disk)] += sz; tot[(mon, "appr_n", disk)] += 1
            if e.name.endswith(".csv"): tot[(mon, "appr_csv", disk)] += sz
    for r in sorted(x for x in os.listdir(E) if x.startswith("root-")):
        for e in os.scandir(f"{E}/{r}"):
            if not e.name.endswith(".binlog"): continue
            t = os.path.realpath(e.path)
            disk = "sda" if t.startswith("/alpha-sda") else "sdb"
            tot[(mon, "binlog", disk)] += os.stat(t).st_size; tot[(mon, "binlog_n", disk)] += 1
for k in sorted(tot): print(*k, round(tot[k] / 1e9, 2) if "_n" not in k[1] else tot[k])
