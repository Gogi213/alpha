#!/usr/bin/env python3
"""TK-120: слить волну a TK-115 (e114 f1/f3, g95-sw3; /data/p12r3a -> data/p12r3a/) в данные анализа R2 v171c: closes-vn-* и expo.json.
Вход data/p12r2-v171c-a/ + data/p12r3a/{closes-r3nd-cap<N>-<мес>.json, expo-v171c.json}; выход data/p12r2-v171c-b/. Запуск: python tools/compute/p12-r3a-merge.py"""
import json
import shutil

SRC, NEW, OUT = "data/p12r2-v171c-a/", "data/p12r3a/", "data/p12r2-v171c-b/"
FAM = ("e114", "g95")
shutil.copytree(SRC, OUT, dirs_exist_ok=True)
for cap in (0, 3):
    for m in range(1, 11):
        mo = f"2026-{m:02d}"
        o = json.load(open(f"{SRC}closes-vn-cap{cap}-{mo}.json"))
        n = json.load(open(f"{NEW}closes-r3nd-cap{cap}-{mo}.json"))
        dup = set(o) & set(n)
        assert all('sw3' in k for k in dup), (cap, mo, dup)   # g95-sw3 уже лежал в closes-vn (источник не выяснен) — заменяем волной a с m §6(2)
        o.update(n)
        json.dump(o, open(f"{OUT}closes-vn-cap{cap}-{mo}.json", "w"))
e = json.load(open(f"{SRC}expo.json"))
ne = json.load(open(f"{NEW}expo-v171c.json"))
for k, v in ne["fired"].items():
    assert k not in e["fired"], k
    e["fired"][k] = v
for sec in ("m_mean", "m_n"):
    for f in ne[sec]:
        if f in FAM:
            e[sec][f] = ne[sec][f]
e["base_missing"].update({f: ne["base_missing"].get(f, 0) for f in FAM})
json.dump(e, open(f"{OUT}expo.json", "w"), ensure_ascii=False, indent=1)
print("ok", OUT, sorted(ne["fired"]))
