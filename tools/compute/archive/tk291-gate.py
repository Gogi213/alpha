#!/usr/bin/env python3
"""TK-291 гейт: проход B1 со столбцом touch_index (--touch-col) = Летопись B1 по месяцам (число сделок, Σ net_bps).

tk291-gate.py <out-каталог прохода> <results.jsonl Летописи> <cell_id B1> [форма]
Старые столбцы rounds.csv не меняются, если по каждому месяцу trades и sum_net_bps (6 знаков) равны Летописи.
Печатает сводку touch_index (пустых, распределение). Код выхода 1 — расхождение."""
import collections
import csv
import glob
import json
import os
import sys

out_root, results, cell_id = sys.argv[1:4]
form = sys.argv[4] if len(sys.argv) > 4 else "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SET = "t-bid-btc4h-q1"

ref = {}
with open(results, encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r.get("cell_id") == cell_id and "month" in r:
            ref[r["month"]] = (int(r["trades"]), r["ext"]["sum_net_bps"], int(r["ext"]["n_signals"]))

n = collections.Counter()
s = collections.Counter()
dist = collections.Counter()
for p in sorted(glob.glob(os.path.join(out_root, "run-*", "out", "*", SET, "rounds.csv"))):
    day = p.split(os.sep)[-3]
    with open(p, newline="", encoding="utf-8") as f:
        lines = [l.rstrip("\r\n") for l in f if not l.startswith("#")]
    h, rows = lines[0].split(","), list(csv.reader(lines[1:]))
    assert h[-1] == "touch_index", (p, h[-3:])
    fi, ni, ti = h.index("form"), h.index("net_bps"), h.index("touch_index")
    for r in rows:
        if r[fi] != form:
            continue
        m = day[:7]
        n[m] += 1
        s[m] += float(r[ni])
        dist[r[ti]] += 1

bad = 0
for m in sorted(set(ref) | set(n)):
    rt = ref.get(m)
    ok = rt is not None and rt[0] == n[m] and abs(float(rt[1]) - s[m]) < 5e-6 * max(1, n[m])
    bad += not ok
    print("%s new n=%d sum=%.6f | Летопись %s %s" % (m, n[m], s[m], rt[0] if rt else None, rt[1] if rt else None),
          "OK" if ok else "РАСХОЖДЕНИЕ")
tot = sum(n.values())
print("всего сделок %d, месяцев %d, расхождений %d" % (tot, len(n), bad))
print("touch_index: пустых %d из %d; распределение %s" % (dist.get("", 0), tot, sorted(dist.items(), key=lambda kv: -kv[1])[:12]))
sys.exit(1 if bad else 0)
