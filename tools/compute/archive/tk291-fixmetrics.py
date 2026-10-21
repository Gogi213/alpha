#!/usr/bin/env python3
"""TK-291: metrics.jsonl хранит канонический хеш (Летопись/панель ищут по нему), не id ячейки: заменить 68 строк П-19 (hash = id ячейки)
на строки с каноническим хешем (порядок add-cells == add-plan_cells). tk291-fixmetrics.py <deploy> <registry>"""
import json, sys
D, R = sys.argv[1:3]
ids = [json.loads(l)["id"] for l in open(f"{D}/add-cells.jsonl", encoding="utf-8") if l.strip()]
hs = [json.loads(l)["cell_hash"] for l in open(f"{D}/add-plan_cells.jsonl", encoding="utf-8") if l.strip()]
assert len(ids) == len(hs) == 68
m = dict(zip(ids, hs))
old = [l.rstrip("\r\n") for l in open(f"{D}/add-metrics.jsonl", encoding="utf-8") if l.strip()]
new = []
for l in old:
    d = json.loads(l); d["hash"] = m[d["hash"]]; new.append(json.dumps(d, ensure_ascii=False))
open(f"{D}/add-metrics-canon.jsonl", "w", encoding="utf-8", newline="\n").write("\n".join(new) + "\n")
raw = open(f"{R}/metrics.jsonl", "rb").read().split(b"\n")
mine = {l.encode() for l in old}
keep = [l for l in raw if l.rstrip(b"\r") not in mine]
if keep and keep[-1] == b"":
    keep.pop()
open(f"{R}/metrics.jsonl", "wb").write(b"\n".join(keep) + b"\n" + "\n".join(new).encode() + b"\n")
print("метрик заменено", len(old) - (len(raw) - len(keep) - 0 if False else 0), "→", len(new))
