#!/usr/bin/env python3
"""TK-135 (С-64): слияние пересчитанных волн в данные анализа R2 — один скрипт вместо p12-r2a-merge.py / p12-r3a-merge.py (копии с заменой набора).
Набор r2a (TK-063/065): семьи A (pyre/pynw/pyeat/pyfresh; g92/g93/g94/g87) из data/p12r2a в data/p12r2 (v171b) и data/p12r2-v171c -> data/p12r2-a/, data/p12r2-v171c-a/.
Набор r3a (TK-120): волна a TK-115 (e114 f1/f3, g95-sw3; data/p12r3a) в data/p12r2-v171c-a/ -> data/p12r2-v171c-b/.
Запуск: python tools/compute/p12-merge.py {r2a|r3a}"""
import json
import shutil
import sys

A = ("-pyre", "-pynw", "-pyeat", "-pyfresh")


def merge_closes(src, out, new_of, combine):
    for cap in (0, 3):
        for m in range(1, 11):
            mo = f"2026-{m:02d}"
            o = json.load(open(f"{src}closes-vn-cap{cap}-{mo}.json"))
            n = json.load(open(new_of(cap, mo)))
            combine(o, n, cap, mo)
            json.dump(o, open(f"{out}closes-vn-cap{cap}-{mo}.json", "w"))


def r2a():
    FAM = ("g92", "g93", "g94", "g87")
    for src, new_pre, expo_new, out in (("data/p12r2/", "van", "expo.json", "data/p12r2-a/"), ("data/p12r2-v171c/", "vand", "expo-v171c.json", "data/p12r2-v171c-a/")):
        shutil.copytree(src, out, dirs_exist_ok=True)

        def combine(o, n, cap, mo):
            old_a = {k for k in o if any(a in k for a in A)}
            assert old_a == set(n), (cap, mo, old_a ^ set(n))
            for k in old_a:
                del o[k]
            o.update(n)
        merge_closes(src, out, lambda cap, mo: f"data/p12r2a/closes-{new_pre}-cap{cap}-{mo}.json", combine)
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


def r3a():
    SRC, NEW, OUT = "data/p12r2-v171c-a/", "data/p12r3a/", "data/p12r2-v171c-b/"
    FAM = ("e114", "g95")
    shutil.copytree(SRC, OUT, dirs_exist_ok=True)

    def combine(o, n, cap, mo):
        dup = set(o) & set(n)
        assert all('sw3' in k for k in dup), (cap, mo, dup)   # g95-sw3 уже лежал в closes-vn (источник не выяснен) — заменяем волной a с m §6(2)
        o.update(n)
    merge_closes(SRC, OUT, lambda cap, mo: f"{NEW}closes-r3nd-cap{cap}-{mo}.json", combine)
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


SETS = {"r2a": r2a, "r3a": r3a}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SETS:
        sys.exit("usage: p12-merge.py {" + "|".join(SETS) + "}")
    SETS[sys.argv[1]]()
