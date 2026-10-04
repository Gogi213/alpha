#!/usr/bin/env python3
"""Свод lob validate по месяцам (TK-038): hours.csv/minutes.csv/files.csv -> текст.
Использование: tk038-summary.py <каталог вывода или родитель каталогов вывода> [--top 20]
ts_backward в счёт НЕ входит (порог — по опорному набору, Судья п.D)."""
import csv, sys, collections, glob

d = sys.argv[1]
top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 20
INV = ["crossed", "nonpositive_size", "unordered_levels", "price_not_on_tick", "qty_not_on_step",
       "delta_before_snapshot"]
mon = collections.defaultdict(lambda: collections.Counter())
cd = collections.defaultdict(lambda: collections.Counter())  # (sym, day) -> счётчики

def rows(name):
    for p in sorted(glob.glob(f"{d}/{name}")) + sorted(glob.glob(f"{d}/*/{name}")):
        with open(p, newline="") as f:
            yield from csv.DictReader(f)

seen = set()
if True:
    for r in rows("hours.csv"):
        key = (r["symbol"], r["day"], r["hour"])
        if key in seen:
            continue
        seen.add(key)
        m = r["day"][:7]
        c = mon[m]
        c["coin_hours"] += 1
        flags = []
        if int(r["silent_minutes"]) > 0: flags.append("silent")
        if int(r["book_broken_updates"]) > 0: flags.append("book_broken")
        if any(int(r[k]) > 0 for k in INV): flags.append("invariant")
        if int(r["snapshot_drift"]) > 0: flags.append("snap_drift")
        if int(r["dup_groups"]) > 0: flags.append("dup_group")
        if int(r["trades_violations"]) > 0: flags.append("trade_viol")
        if int(r["trades_while_broken"]) > 0: flags.append("trade_in_broken")
        if flags: c["coin_hours_bad"] += 1
        for k in flags: c["h_" + k] += 1
        c["silent_minutes"] += int(r["silent_minutes"])
        c["broken_ms"] += int(float(r["book_broken_ms"]))
        k = (r["symbol"], r["day"])
        cd[k]["silent_minutes"] += int(r["silent_minutes"])
        cd[k]["broken_ms"] += int(float(r["book_broken_ms"]))
        cd[k]["bad_hours"] += 1 if flags else 0

a1 = collections.Counter()
seen_m = set()
if True:
    for r in rows("minutes.csv"):
        ks = set(r["kinds"].split("|"))
        if ks <= {"ts_backward"}:
            continue
        mk = (r["symbol"], r["day"], r["hour"], r["minute"])
        if mk in seen_m:
            continue
        seen_m.add(mk)
        a1[(r["day"][:7], "rate_anomaly")] += "rate_anomaly" in ks
        a1[(r["day"][:7], "silent_only")] += ("silent" in ks and "rate_anomaly" not in ks)

print("месяц; монето-часов; с проблемами; silent; book_broken; invariant; snap_drift; dup_group; "
      "trade_viol; trade_in_broken; потерянных минут (silent); A1 rate_anomaly; битой книги, ч")
for m in sorted(mon):
    c = mon[m]
    print("; ".join(str(x) for x in [m, c["coin_hours"], c["coin_hours_bad"], c["h_silent"],
          c["h_book_broken"], c["h_invariant"], c["h_snap_drift"], c["h_dup_group"], c["h_trade_viol"],
          c["h_trade_in_broken"], c["silent_minutes"], a1[(m, "rate_anomaly")],
          round(c["broken_ms"] / 3.6e6, 1)]))
print(f"\nТоп-{top} монето-суток по битой книге + тишине:")
for (s, day), c in sorted(cd.items(), key=lambda kv: (-(kv[1]["broken_ms"] / 6e4 + kv[1]["silent_minutes"]), -kv[1]["bad_hours"]))[:top]:
    print(f"{s} {day} битая книга {c['broken_ms']/6e4:.0f} мин, тишина {c['silent_minutes']} мин, часов с проблемой {c['bad_hours']}")
