#!/usr/bin/env python3
"""TK-433 v10 §4: признаки К4–К8 по сделкам Bybit (TK-417, сентябрь) у пика ноги и контролей.
  tk433-trades.py <end-start-th{T}.csv> <legs-th5.csv> <корень /data/tk417> <выход.csv>
Окна: до = минуты [p-60, p], после = [p+1, p+60] (p = peak_min, минута эпохи). Признаки (знак s = ±1, «памп» s=+1, дамп зеркально):
  K5_fade  = -s*(доля покупок после - до)            (линейный фьючерс)
  K6_delta = s*(sell-buy)/сумма, после               K6_int = ln(сделок/мин после / до)
  K7_retest = минут «после» с close в пределах θ(T=5, рынок, знак) от пика
  K4_fade  = как K5 по споту;  K8_diff = delta_спот - delta_фьюч после (оба с s)
Нет данных (инструмент вне 50+17 или нет дня) -> пусто. К2 (OI Bybit): сентябрьского OI нет -> «не оценено», в этот файл не входит."""
import csv, gzip, os, sys
from collections import defaultdict
import numpy as np

csv_in, legs5, root, out = sys.argv[1:5]
th = {}
for r in csv.DictReader(open(legs5, encoding="utf-8")):
    try:
        th[(r["биржа"], r["рынок"], r["знак"])] = float(r["θ"]) / 100.0
    except (KeyError, ValueError):
        pass


def load_lin(sym):
    d = f"{root}/bybit-linear/{sym}"
    A = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with gzip.open(f"{d}/{fn}", "rt") as f:
            f.readline()
            for ln in f:
                p = ln.split(",", 10)
                A.append((float(p[0]), float(p[4]), float(p[9]), p[2] == "Buy"))
    return np.array(A, dtype=float).reshape(-1, 4) if A else None


def load_spot(sym):
    fn = f"{root}/bybit-spot/{sym}/{sym}-2026-09.csv.gz"
    if not os.path.exists(fn):
        return None
    A = []
    with gzip.open(fn, "rt") as f:
        f.readline()
        for ln in f:
            p = ln.split(",", 5)
            pr = float(p[2])
            A.append((int(p[1]) / 1000.0, pr, pr * float(p[3]), p[4].strip() == "buy"))
    return np.array(A, dtype=float).reshape(-1, 4) if A else None


def win(A, lo, hi):
    t = A[:, 0]
    i, j = np.searchsorted(t, lo * 60.0), np.searchsorted(t, (hi + 1) * 60.0)
    return A[i:j]


def stats(w):
    if len(w) == 0:
        return None
    tot = w[:, 2].sum()
    buy = w[w[:, 3] == 1, 2].sum()
    return dict(bs=buy / tot if tot > 0 else np.nan, n=len(w), dl=(tot - 2 * buy) / tot if tot > 0 else np.nan)


rows = list(csv.DictReader(open(csv_in, encoding="utf-8")))
bysym = defaultdict(list)
for r in rows:
    if r["биржа"] == "bybit":
        bysym[(r["рынок"], r["инструмент"])].append(r)
res = {}
for (mk, sym), rs in bysym.items():
    A = load_lin(sym) if mk == "linear" else load_spot(sym)
    if A is None:
        continue
    L = A if mk == "linear" else (load_lin(sym) if os.path.isdir(f"{root}/bybit-linear/{sym}") else None)
    for r in rs:
        p, s = int(r["peak_min"]), int(r["знак"])
        b, a = stats(win(A, p - 60, p)), stats(win(A, p + 1, p + 60))
        if b is None or a is None or a["n"] < 1 or b["n"] < 1:
            continue
        o = {}
        fade = -s * (a["bs"] - b["bs"])
        o["K5_fade" if mk == "linear" else "K4_fade"] = fade
        if mk == "linear":
            o["K6_delta"] = s * a["dl"]
            o["K6_int"] = float(np.log(a["n"] / b["n"]))
            w = win(A, p, p)
            if len(w):
                pk = w[:, 1].max() if s > 0 else w[:, 1].min()
                band = th.get(("bybit", "linear", "+" if s > 0 else "-"))
                aft = win(A, p + 1, p + 60)
                mins = (aft[:, 0] // 60).astype(int)
                if band and len(aft):
                    last = {}
                    for m, pr in zip(mins, aft[:, 1]):
                        last[m] = pr
                    o["K7_retest"] = float(sum(1 for pr in last.values() if s * (pk - pr) / pk <= band))
        elif L is not None:
            la = stats(win(L, p + 1, p + 60))
            if la is not None:
                o["K8_diff"] = s * a["dl"] - s * la["dl"]
        res[r["id"] + "|" + r["роль"] + "|" + r["event_id"]] = (r["id"], o)
cols = ["K5_fade", "K6_delta", "K6_int", "K7_retest", "K4_fade", "K8_diff"]
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id"] + cols)
    for _, (i, o) in res.items():
        w.writerow([i] + [("%.6g" % o[c]) if c in o else "" for c in cols])
print("строк с признаками:", len(res), {c: sum(1 for _, (_, o) in res.items() if c in o) for c in cols})
