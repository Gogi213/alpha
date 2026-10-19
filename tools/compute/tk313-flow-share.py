#!/usr/bin/env python3
"""TK-313, П-21 §10 п.3: доля flow_1h_lots > 0 на touches замера u1-u3 (t.csv) по рынкам/монетам. Выход: JSON в stdout."""
import csv, glob, json, os, sys
W = sys.argv[1]
res = {}
for mkt in ("spot", "fut"):
    for p in sorted(glob.glob(f"{W}/{mkt}-*/t.csv")):
        sym = os.path.basename(os.path.dirname(p)).split("-")[2]
        n = pos = 0
        with open(p, newline="") as fh:
            for row in csv.DictReader(fh):
                if row["side"] != "bid":
                    continue
                n += 1
                pos += float(row["flow_1h_lots"]) > 0
        a = res.setdefault(mkt, {}).setdefault(sym, [0, 0])
        a[0] += n
        a[1] += pos
out = {m: {"coins": len(d), "touches": sum(v[0] for v in d.values()),
           "share_pos": sum(v[1] for v in d.values()) / max(1, sum(v[0] for v in d.values())),
           "min_coin_share": min((v[1] / v[0], k) for k, v in d.items() if v[0]),
           "zero_coins": [k for k, v in d.items() if v[0] and v[1] == 0]} for m, d in res.items()}
print(json.dumps(out, ensure_ascii=False, indent=1))
