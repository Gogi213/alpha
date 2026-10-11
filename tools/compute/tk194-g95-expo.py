#!/usr/bin/env python3
"""TK-194: равная экспозиция §6(2) для g95-sw4/sw5 на R2-B (как --g95 в p12-r2-expo.py для sw3, та же формула m).
Вход: <raw> — сырые деревья суток (все сигналы), <busy> — busy-replay'нутые; обе с раскладкой <мес>/<сутки>/<стратегия>/rounds.csv.
m = min(1, N_ref/N_peak), N_peak = qty*entry_vwap сделки клетки, N_ref = qty/fill_frac*entry_vwap строки базы B1 (sw2) того же сигнала;
qty сделки умножается на m. Пишет <out>/<мес>/<сутки>/<стратегия>/rounds.csv (только формы sw4/sw5, без DROP) и <out>/expo.json."""
import csv, glob, json, os, sys
from collections import defaultdict

raw, busy, out = sys.argv[1:4]
DROP = set(filter(None, os.environ.get("EXPO_DROP", "").split(",")))
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
FORMS = {f"ladder3x0..0.0409sw{n}-pct2-tr1x1-14400-ttl1800" for n in (4, 5)}


def read(p):
    ls = open(p, encoding="utf-8").readlines()
    return [l for l in ls if l.startswith("#")], list(csv.DictReader(l for l in ls if not l.startswith("#")))


msum, mn, miss, fired = defaultdict(float), defaultdict(int), defaultdict(int), defaultdict(int)
for f in sorted(glob.glob(f"{busy}/20*/20*/*/rounds.csv")):
    mo, day, st = f.split("/")[-4:-1]
    base = {(r["symbol"], r["signal_index"]): r for r in read(f"{raw}/{mo}/{day}/{st}/rounds.csv")[1] if r["form"] == B1}
    head, rows = read(f)
    res = []
    for r in rows:
        if r["form"] not in FORMS or r["symbol"] in DROP:
            continue
        b = base.get((r["symbol"], r["signal_index"]))
        if b is None:
            miss[r["form"]] += 1
            continue   # нет базы B1 того же сигнала (3 досчитанных символо-суток вне вселенной B1) — m не определён, вне счёта
        else:
            npk = float(r["qty"]) * float(r["entry_vwap"])
            nref = float(b["qty"]) / (float(b["fill_frac"]) or 1.0) * float(b["entry_vwap"])
            m = min(1.0, nref / npk) if npk else 1.0
            if abs(float(r["qty"]) - float(b["qty"])) > 1e-9 or abs(float(r["entry_vwap"]) - float(b["entry_vwap"])) > 1e-12:
                fired[f"{r['form']}|{mo}"] += 1
            r = dict(r); r["qty"] = "%.10f" % (float(r["qty"]) * m)
            msum[f"{r['form']}|{mo}"] += m; mn[f"{r['form']}|{mo}"] += 1
        res.append(r)
    od = f"{out}/{mo}/{day}/{st}"; os.makedirs(od, exist_ok=True)
    with open(f"{od}/rounds.csv", "w", encoding="utf-8", newline="") as fh:
        fh.writelines(head)
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [], lineterminator="\n"); w.writeheader(); w.writerows(res)
json.dump({"fired": fired, "m_mean": {k: msum[k] / mn[k] for k in mn}, "m_n": mn, "base_missing": miss}, open(f"{out}/expo.json", "w"), ensure_ascii=False, indent=1)
print("ok", dict(miss))
