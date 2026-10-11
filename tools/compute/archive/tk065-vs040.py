#!/usr/bin/env python3
"""TK-065 (в): базовая клетка волны R2 против TK-040 (p07b-base, t-bid-btc4h-q1) по всем суткам."""
import csv, os

FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SET = "t-bid-btc4h-q1"
days = [l.split() for l in open("/data/tk065/days/days.tsv")]


def rows(path):
    if not os.path.exists(path):
        return None
    out = {}
    for r in csv.DictReader(l for l in open(path, encoding="utf-8") if not l.startswith("#")):
        if r["form"] == FORM:
            out[r["symbol"]] = r
    return out


tot = dict(days=0, common=0, diff=0, only040=0, only040_sig=0, onlywave=0, onlywave_sig=0, nofile040=0, nofilewave=0)
ex = []
cs, cd = {}, {}
for d, mon in days:
    wv = rows(f"/data/tk065/w-{d}/b5/r2/{d}/{SET}/forms.csv")
    wsv = rows(f"/data/tk065/ws-{d}/b5/r2/{d}/{SET}/forms.csv")
    if wv is not None and wsv:
        wv.update(wsv)
    tk = rows(f"/data/tk048/tk040-b14/{mon}/b5/p07b-base/{d}/{SET}/forms.csv")
    sp = rows(f"/data/tk048/tk040-sup/{mon}/b5/p07b-base/{d}/{SET}/forms.csv")
    if wv is None:
        tot["nofilewave"] += 1; continue
    if tk is None and sp is None:
        tot["nofile040"] += 1; continue
    ref = dict(tk or {})
    ref.update(sp or {})
    tot["days"] += 1
    for s in set(ref) | set(wv):
        a, b = ref.get(s), wv.get(s)
        if a and b:
            tot["common"] += 1
            if a != b:
                tot["diff"] += 1
                if len(ex) < 5:
                    ex.append((d, s, {k: (a[k], b[k]) for k in a if a[k] != b.get(k)}))
        elif a:
            tot["only040"] += 1
            if int(a["n_signals"]) > 0:
                tot["only040_sig"] += 1
                cs[s] = cs.get(s, 0) + 1
                cd[d] = cd.get(d, 0) + 1
        else:
            tot["onlywave"] += 1
            tot["onlywave_sig"] += int(b["n_signals"]) > 0
print(tot)
for e in ex:
    print(str(e)[:400])
print("by_symbol", sorted(cs.items(), key=lambda x: -x[1])[:15])
print("by_day", sorted(cd.items(), key=lambda x: -x[1])[:10])
