"""TK-362: предфильтр §5 банка G-015 по клеткам D20 (13 строк). Необходимое условие кандидата: S на основном горизонте
> порога (11,25 / 12,375 / 13,5 бп) в >= 5 мес при n_days >= 20, n_coins >= 10. C3 нет -> это отсев сверху, не отбор.
Запуск: python -I tools/compute/g015-d20-rank.py <cells.csv> <bank.csv>"""
import csv, sys, statistics as st
cells = [r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")) if r["pop"] == "event"]
bank = {r["id"].rstrip("ab"): r for r in csv.DictReader(open(sys.argv[2], encoding="utf-8"))}
hz = {"s10": "S_m_10s", "m10": "S_m_10m"}
sn = {"s10": None, "m10": ("Snet_sp1h_10m", "Snet_sm1h_10m")}
def f(x):
    try: return float(x)
    except Exception: return float("nan")
out = {}
for r in cells:
    h = r["hyp"]; b = bank.get(h.rstrip("ab")) or bank.get(h)
    ph = b["primary_horizon"] if b else "m10"
    ph = "s10" if ph.startswith("s") else "m10"
    s = f(r[hz[ph]]); k = (h, r["src"], ph)
    ok_n = f(r["n_days"]) >= 20 and f(r["n_coins"]) >= 10
    out.setdefault(k, []).append((r["month"], s, ok_n, f(r["n"])))
print("hyp,market,horizon,months,months_n_ok,S_max,S_median,m>11.25,m>12.375,m>13.5,m>11.25_nok")
for (h, m, ph), v in sorted(out.items()):
    ss = [x[1] for x in v if x[1] == x[1]]
    c = lambda t, nk=False: sum(1 for x in v if x[1] == x[1] and x[1] > t and (x[2] or not nk))
    print(f"{h},{m},{ph},{len(v)},{sum(1 for x in v if x[2])},{max(ss):.2f},{st.median(ss):.2f},{c(11.25)},{c(12.375)},{c(13.5)},{c(11.25,True)}")
