"""Эталон П-40 (TK-501): итоговая сверка выходов ядра (out.exits.csv) и признаков (out.features.csv) с эталоном
(maker_<θ>.csv, features_<θ>.csv). Ключ (θ, t0). Допуск признаков 1e-5 отн. / 1e-3 абс. Использование: compare_all.py <ядро.csv> <θ>..."""
import sys, csv, numpy as np
core = sys.argv[1]; D = "/root/p40ref/out/ep/"
ex = list(csv.DictReader(open(core + ".exits.csv"))); ft = list(csv.DictReader(open(core + ".features.csv")))
tot = bad = 0
fbad = {}
for th in sys.argv[2:]:
    mk = {int(r["t_start"]): r for r in csv.DictReader(open(D + f"maker_{th}.csv"))}
    rf = list(csv.DictReader(open(D + f"features_{th}.csv")))  # t_start в файле усечён до 10 знач. цифр — сверка по порядку строк
    fi = 0
    for r in ex:
        if abs(float(r["theta"]) - float(th)) > 1e-9: continue
        x = mk[int(r["t0_ms"])]; tot += 1; d = []
        if r["outcome"] != x["outcome"]: d.append("outcome")
        if int(r["exit_ms"]) != int(x["exit_t"]): d.append("exit_t")
        if r["exit_kind"] != (x["exit_kind"] or ""): d.append("kind")
        a, b = float(r["pnl_bps"]), float(x["pnl_bps"])
        if not (np.isnan(a) and np.isnan(b)) and abs(a - b) > 1e-6: d.append("pnl")
        if d: bad += 1; print("  выход", th, r["t0_ms"], d)
    for r in ft:
        if abs(float(r["theta"]) - float(th)) > 1e-9: continue
        x = rf[fi]; fi += 1
        for k in ["d_in","depth_slip","spread","vol5m","plevel","flow1m","bookimb","leader","hour"]:
            a, b = float(r[k]), float(x[k])
            if abs(a - b) > 1e-3 + 1e-5 * abs(b): fbad[k] = fbad.get(k, 0) + 1
print("выходов", tot, "расхождений", bad, "| признаки, расхождений по столбцам:", fbad or "нет")
