#!/usr/bin/env python3
"""TK-345: B1 (signal=approach): независимая верхняя граница n по кэшу подходов D20/<сутки>/approaches-*.csv.

tk345-n-bound.py <approaches-dir> <forms.csv> <форма> <age_s> <behind_pct>
Проверки по символам: (а) n_signals + n_skipped == все подходы кэша (обе стороны); (б) n_signals <= число bid-подходов с
age_ms >= age_s*1000 и depth_behind_lots*100 >= behind_pct*size_at_arm (без h3-порога и без btc4h — они только режут дальше)."""
import csv, os, sys
adir, forms, form, age_s, behind = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
bad = tot_s = tot_b = 0
rows = [r for r in csv.DictReader(l for l in open(forms) if not l.startswith("#")) if r["form"] == form]
for r in rows:
    p = os.path.join(adir, f"approaches-{r['symbol']}.csv")
    if not os.path.exists(p):
        print(f"НЕТ кэша {r['symbol']}"); bad += 1; continue
    allr = ub = 0
    for a in csv.DictReader(open(p)):
        allr += 1
        if a["side"] == "bid" and int(a["age_ms"]) >= age_s * 1000 and int(a["depth_behind_lots"]) * 100 >= behind * int(a["size_at_arm"]):
            ub += 1
    s, k = int(r["n_signals"]), int(r["n_skipped"])
    tot_s += s; tot_b += ub
    if s + k != allr or s > ub:
        bad += 1; print(f"DIFF {r['symbol']}: signals={s} skipped={k} подходов={allr} верхняя_граница={ub}")
print(f"форма {form}: символов {len(rows)}, нарушений {bad}, sum n_signals={tot_s}, sum верхней границы={tot_b}")
