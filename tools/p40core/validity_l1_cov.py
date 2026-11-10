# TK-522 (Судья 10.11 §2/§3 п.2): покрытие validity по L1 планом D1 — (нога, сутки, час) плана против <out>/validity_l1.csv; по биржам и статусам; 100 % = код 0.
# Запуск: python3 validity_l1_cov.py <pairs.csv> <validity_l1.csv> <день1,день2,...> [assets]
import collections, csv, sys
pairs, vcsv, days = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
keep = set(filter(None, (sys.argv[4] if len(sys.argv) > 4 else "").split(",")))
VEX = {"okx": "okx_futures", "bitget": "bitget_futures", "aster": "aster_futures", "hyperliquid": "hyperliquid_futures", "binance": "binance_futures"}
plan = {(VEX.get(r["ex" + s], r["ex" + s]), r["sym" + s]) for r in csv.DictReader(open(pairs)) if not keep or r["asset"] in keep for s in "AB"}
seen, st = {}, collections.Counter()
for r in csv.DictReader(open(vcsv)):
    seen.setdefault((r["ex"], r["contract"], r["date"]), set()).add(int(r["hour"]))
    st[(r["ex"], r["status"])] += 1
need = len(plan) * len(days) * 24
miss = sum(24 - len(seen.get((ex, sym, d), ())) for ex, sym in plan for d in days)
print(f"плана (нога, час): {need}, без строки: {miss}, покрытие {100 * (need - miss) / need:.2f} %  (ног {len(plan)}, суток {len(days)})")
for (ex, s), n in sorted(st.items()):
    print(f"  {ex:18s} {s:12s} {n}")
sys.exit(1 if miss else 0)
