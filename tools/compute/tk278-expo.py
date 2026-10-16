#!/usr/bin/env python3
"""TK-278 п.2: равная экспозиция §6(2) П-12 для семей М (Г-87/92/93/95) по ГОТОВЫМ деревьям — обобщение tk194-g95-expo.py (без бэктеста, В-213).
  tk278-expo.py <raw-b1> <busy> <out> <spec>   spec: строки "стратегия<TAB>форма"; <raw-b1> — сырые деревья B1 (<мес>/<сутки>/<стр>/rounds.csv),
  <busy> — деревья с клетками (та же раскладка). Пишет <out>/<мес>/<сутки>/<стр>/rounds.csv (только формы spec, qty*m) и <out>/expo.json.
m по семье (P-12 §6(2)): -pyre/-pynw (g92/g93) m = N_B1/N_peak; остальные (g87/g95) m = min(1, N_ref/N_peak);
N_peak = qty*entry_vwap сделки клетки, N_B1 = qty*entry_vwap строки B1 того же (symbol, signal_index), N_ref = qty/fill_frac*entry_vwap B1.
База B1 — той же стратегии (EXPO_BASE_ST — другая). Нет базы B1 того же сигнала -> сделка вне счёта (как в tk194), счётчик base_missing."""
import csv, glob, json, os, sys
from collections import defaultdict

raw, busy, out, spec = sys.argv[1:5]
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
BASE_ST = os.environ.get("EXPO_BASE_ST")      # по умолчанию база — строка B1 той же стратегии (как p12-r2-expo.py)
DROP = set(filter(None, os.environ.get("EXPO_DROP", "TRUMPUSDT,TRXUSDT,BCHUSDT").split(",")))
KEEP_NOBASE = os.environ.get("EXPO_KEEP_NOBASE") == "1"      # 1 — как p12-r2-expo.py (волны A); умолчание — как tk194 (сделка без базы вне счёта)
want = defaultdict(set)
for ln in open(spec, encoding="utf-8"):
    if ln.strip():
        st, form = ln.rstrip("\n").split("\t"); want[st].add(form)


def read(p):
    ls = open(p, encoding="utf-8").readlines()
    return [l for l in ls if l.startswith("#")], list(csv.DictReader(l for l in ls if not l.startswith("#")))


def mode(form):
    return "ratio" if ("-pyre" in form or "-pynw" in form) else "plan"


msum, mn, miss, fired, mmax = defaultdict(float), defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(float)
for f in sorted(glob.glob(f"{busy}/20*/20*/*/rounds.csv")):
    mo, day, st = f.split("/")[-4:-1]
    if st not in want:
        continue
    bp = f"{raw}/{mo}/{day}/{BASE_ST or st}/rounds.csv"
    base = {(r["symbol"], r["signal_index"]): r for r in read(bp)[1] if r["form"] == B1} if os.path.exists(bp) else {}
    head, rows = read(f)
    res = []
    for r in rows:
        if r["form"] not in want[st] or r["symbol"] in DROP:
            continue
        key = f"{st}@{r['form']}|{mo}"
        b = base.get((r["symbol"], r["signal_index"]))
        if b is None:
            miss[f"{st}@{r['form']}"] += 1
            if KEEP_NOBASE:      # p12-r2-expo.py (A): сделка без базы остаётся с m=1
                res.append(r)
            continue
        npk = float(r["qty"]) * float(r["entry_vwap"])
        if mode(r["form"]) == "ratio":
            nref = float(b["qty"]) * float(b["entry_vwap"])
            m = nref / npk if npk else 1.0
        else:
            nref = float(b["qty"]) / (float(b["fill_frac"]) or 1.0) * float(b["entry_vwap"])
            m = min(1.0, nref / npk) if npk else 1.0
        if abs(m - 1.0) > 1e-9:
            fired[key] += 1
        mmax[key] = max(mmax[key], m)
        r = dict(r); r["qty"] = "%.10f" % (float(r["qty"]) * m)
        msum[key] += m; mn[key] += 1
        res.append(r)
    od = f"{out}/{mo}/{day}/{st}"; os.makedirs(od, exist_ok=True)
    with open(f"{od}/rounds.csv", "w", encoding="utf-8", newline="") as fh:
        fh.writelines(head)
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [], lineterminator="\n"); w.writeheader(); w.writerows(res)
json.dump({"m_active": fired, "m_mean": {k: msum[k] / mn[k] for k in mn}, "m_max": mmax, "m_n": mn, "base_missing": miss},
          open(f"{out}/expo.json", "w"), ensure_ascii=False, indent=1)
print("ok", len(mn), "cell-months; base_missing", sum(miss.values()))
