#!/usr/bin/env python3
"""TK-063: слить пересчитанные семьи A (волна A TK-065, data/p12r2a) в данные анализа R2: замена форм pyre/pynw/pyeat/pyfresh в closes-vn-* и записей g92/g93/g94/g87 в expo.json.
Выход: data/p12r2-a/ (из data/p12r2, v171b) и data/p12r2-v171c-a/ (из data/p12r2-v171c, v171c). Запуск: python tools/compute/p12-r2a-merge.py"""
import json
import os
import shutil

A = ("-pyre", "-pynw", "-pyeat", "-pyfresh")
FAM = ("g92", "g93", "g94", "g87")
for src, new_pre, expo_new, out in (("data/p12r2/", "van", "expo.json", "data/p12r2-a/"), ("data/p12r2-v171c/", "vand", "expo-v171c.json", "data/p12r2-v171c-a/")):
    shutil.copytree(src, out, dirs_exist_ok=True)
    for cap in (0, 3):
        for m in range(1, 11):
            mo = f"2026-{m:02d}"
            o = json.load(open(f"{src}closes-vn-cap{cap}-{mo}.json"))
            n = json.load(open(f"data/p12r2a/closes-{new_pre}-cap{cap}-{mo}.json"))
            old_a = {k for k in o if any(a in k for a in A)}
            assert old_a == set(n), (cap, mo, old_a ^ set(n))
            for k in old_a:
                del o[k]
            o.update(n)
            json.dump(o, open(f"{out}closes-vn-cap{cap}-{mo}.json", "w"))
    e = json.load(open(f"{src}expo.json"))
    ne = json.load(open(f"data/p12r2a/{expo_new}"))
    for k in [k for k in e["fired"] if any(a in k for a in A)]:
        del e["fired"][k]
    e["fired"].update({k: v for k, v in ne["fired"].items() if any(a in k for a in A)})
    for sec in ("m_mean", "m_n"):
        e[sec].update({f: ne[sec][f] for f in FAM if f in ne[sec]})
    e["base_missing"].update({f: ne["base_missing"].get(f, 0) for f in FAM})
    json.dump(e, open(f"{out}expo.json", "w"), ensure_ascii=False, indent=1)
    print("ok", out)
