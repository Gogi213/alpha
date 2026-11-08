"""TK-500: сверка прохода 2 ядра (<out>.fills.csv) с эталоном TK-501 (maker_<θ>.csv): время и признак сквозного исполнения каждой ноги.
Запуск: cmp_maker.py <fills.csv> <maker_5.04.csv> <maker_12.6.csv>"""
import csv, sys

mine = {}
for r in csv.DictReader(open(sys.argv[1])):
    mine[(r["theta"], int(r["t0_ms"]))] = r
n = bad = 0
for th, path in (("5.04", sys.argv[2]), ("12.6", sys.argv[3])):
    for r in csv.DictReader(open(path)):
        t0 = int(r["t_start"])
        m = mine.get((th, t0))
        if m is None:
            print("нет в ядре", th, t0)
            bad += 1
            continue
        for leg in ("sell", "buy"):
            ref_t = int(r["t_fill_" + leg])
            ref_t = ref_t - t0 if ref_t >= 0 else -1
            ref_i = int(r["imm_" + leg] == "True")
            my_t, my_i = int(m["fill_%s_ms" % leg]), int(m["imm_%s" % leg])
            n += 1
            if (ref_t, ref_i) != (my_t, my_i):
                bad += 1
                print("расхождение", th, t0, leg, "эталон", (ref_t, ref_i), "ядро", (my_t, my_i), "front", m["front_" + leg])
print("ног сверено", n, "расхождений", bad)

# выход/страховка (<fills.csv без .fills> .exits.csv): сверка с эталоном, если задан четвёртый аргумент
if len(sys.argv) > 4:
    ex = {(r["theta"], int(r["t0_ms"])): r for r in csv.DictReader(open(sys.argv[4]))}
    n = bad = 0
    for th, path in (("5.04", sys.argv[2]), ("12.6", sys.argv[3])):
        for r in csv.DictReader(open(path)):
            m = ex[(th, int(r["t_start"]))]
            n += 1
            rp, mp = float(r["pnl_bps"]), float(m["pnl_bps"])
            same_pnl = (rp != rp and mp != mp) or abs(rp - mp) < 1e-6
            same = r["outcome"] == m["outcome"] and r["exit_kind"] == m["exit_kind"] and same_pnl and int(float(r["exit_t"])) == int(m["exit_ms"])
            if not same:
                bad += 1
                print("выход расхождение", th, r["t_start"], "эталон", r["outcome"], r["exit_kind"], r["exit_t"], r["pnl_bps"], "ядро", m["outcome"], m["exit_kind"], m["exit_ms"], m["pnl_bps"])
    print("эпизодов (выход/страховка) сверено", n, "расхождений", bad)
