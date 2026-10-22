#!/usr/bin/env python3
"""TK-289: на calc в панель — plan_cells F3 П-18 (90 строк) + config.protocols P-18 -> [1,2,3]. Бэкап /data/launch/prev-tk289f3. Дописью, без дублей.
Запуск на calc: tk289-deploy-plan-f3.py <add-plan_cells.jsonl>"""
import json, os, shutil, sys
L = "/opt/alpha-launch/tools/launch"; R = "/data/launch/registry"; B = "/data/launch/prev-tk289f3"
os.makedirs(B, exist_ok=True)
for src in (R + "/plan_cells.jsonl", L + "/config.json"):
    dst = B + "/" + os.path.basename(src)
    if not os.path.exists(dst): shutil.copy2(src, dst)
lines = lambda p: [l.rstrip("\r\n") for l in open(p, encoding="utf-8", newline="") if l.strip()]
k = lambda d: (d["protocol"], d["fraction"], d["cell_hash"])
have = {k(json.loads(l)) for l in lines(R + "/plan_cells.jsonl")}
new = [l for l in lines(sys.argv[1]) if k(json.loads(l)) not in have]
with open(R + "/plan_cells.jsonl", "a", encoding="utf-8", newline="") as f: f.writelines(l + "\n" for l in new)
print("plan_cells +", len(new))
c = json.load(open(L + "/config.json", encoding="utf-8"))
for p in c["protocols"]:
    if p[0] == "P-18" and p[1] != [1, 2, 3]: p[1] = [1, 2, 3]
open(L + "/config.json.new", "w", encoding="utf-8", newline="\n").write(json.dumps(c, ensure_ascii=False, indent=2) + "\n")
os.replace(L + "/config.json.new", L + "/config.json")
print("config.protocols", c["protocols"])
