#!/usr/bin/env python3
"""TK-278 п.2: сверка closes старых клеток М (без монеты, m применён) с готовыми: те же сделки (время) и разница $ (A — m меняет $, B — tk194 m тот же → ждём 0).
  tk278-expo-old-cmp.py   читает /data/tk0278/expo-old/chk-<A|B>-<мес>.json; A — /data/p12r2a/closes-va-cap0-<мес>.json, B — /data/tk0194/closes-r2bg95-cap0-<мес>.json"""
import json, os
W = "/data/tk0278/expo-old"
REF = {"A": "/data/p12r2a/closes-va-cap0-2026-%s.json", "B": "/data/tk0194/closes-r2bg95-cap0-2026-%s.json"}
bad = 0; tot = 0
for g in "AB":
    n = same_ts = 0; dmax = 0.0; miss = 0
    for mo in "01 02 03 04 05 06 07 08 09".split():
        p = f"{W}/chk-{g}-{mo}.json"
        if not os.path.exists(p):
            continue
        new = json.load(open(p)); ref = json.load(open(REF[g] % mo))
        for k, v in new.items():
            rv = ref.get(k)
            if rv is None:
                miss += 1; continue
            a = [x for m in v.values() for c in m.values() for x in c]; b = [x for m in rv.values() for c in m.values() for x in c]
            n += 1
            if [x[0] for x in a] == [x[0] for x in b]:
                same_ts += 1; dmax = max([dmax] + [abs(x[1] - y[1]) for x, y in zip(a, b)])
            else:
                bad += 1
    tot += n
    print(f"{g}: набор-месяцев {n}, без готового {miss}, времена сделок равны {same_ts}, макс |Δ$| {dmax:.4f}")
print("ИТОГО расхождений времени", bad, "; наборов всего", tot)
raise SystemExit(1 if bad or not tot else 0)
