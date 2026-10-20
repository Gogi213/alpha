#!/usr/bin/env python3
"""TK-291: откат дельты П-19 из /data/launch/registry — удаляет только строки, побайтно равные строкам add-*.jsonl (чужие дописи сохраняются)."""
import os, sys
D, R = sys.argv[1], sys.argv[2]
for b in ("cells", "run_cells", "results", "metrics", "runs", "plan_cells", "metrics_why"):
    if not os.path.exists(f"{D}/add-{b}.jsonl") or not os.path.exists(f"{R}/{b}.jsonl"):
        continue
    mine = {l.rstrip(b"\r\n") for l in open(f"{D}/add-{b}.jsonl", "rb") if l.strip()}
    raw = open(f"{R}/{b}.jsonl", "rb").read()
    out, dropped = [], 0
    for l in raw.split(b"\n"):
        if l.rstrip(b"\r") in mine:
            dropped += 1
        else:
            out.append(l)
    open(f"{R}/{b}.jsonl", "wb").write(b"\n".join(out))
    print(b, "удалено", dropped, "из", len(mine))
