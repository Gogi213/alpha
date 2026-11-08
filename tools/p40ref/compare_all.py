"""Эталон П-40 (TK-501): итоговая сверка выходов ядра (out.exits.csv) и признаков (out.features.csv) с эталоном
(maker_<θ>.csv, features_<θ>.csv). Ключ выходов (θ, t0); признаков (θ, t_start с точностью до секунды, dir) — в файле эталона t_start усечён. Допуск признаков 1e-5 отн. / 1e-3 абс.; NaN к числу — расхождение Использование: compare_all.py <ядро.csv> <θ>..."""
import sys, csv, numpy as np
core = sys.argv[1]; D = "/root/p40ref/out/ep/"
ex = list(csv.DictReader(open(core + ".exits.csv"))); ft = list(csv.DictReader(open(core + ".features.csv")))
tot = bad = 0
fbad = {}
for th in sys.argv[2:]:
    mk = {int(r["t_start"]): r for r in csv.DictReader(open(D + f"maker_{th}.csv"))}
    rf = list(csv.DictReader(open(D + f"features_{th}.csv")))  # t_start в файле усечён до 10 знач. цифр — сверка по порядку строк
    rfk = {}
    for x in rf: rfk.setdefault((round(float(x["t_start"]) / 1000), int(float(x["dir"]))), []).append(x)
    for r in ex:
        if abs(float(r["theta"]) - float(th)) > 1e-9: continue
        x = mk[int(r["t0_ms"])]; tot += 1; d = []
        if r["outcome"] != x["outcome"]: d.append("outcome")
        if int(r["exit_ms"]) != int(x["exit_t"]): d.append("exit_t")
        if r["exit_kind"] != (x["exit_kind"] or ""): d.append("kind")
        a, b = float(r["pnl_bps"]), float(x["pnl_bps"])
        if not (np.isnan(a) and np.isnan(b)) and abs(a - b) > 1e-6: d.append("pnl")
        if d: bad += 1; print("  выход", th, r["t0_ms"], d)
    nf = 0
    for r in ft:
        if abs(float(r["theta"]) - float(th)) > 1e-9: continue
        q = rfk.get((round(int(r["t_start"]) / 1000), int(float(r["dir"]))))
        if not q: fbad["нет строки эталона"] = fbad.get("нет строки эталона", 0) + 1; continue
        x = q.pop(0); nf += 1
        for k in ["d_in","depth_slip","spread","vol5m","plevel","flow1m","bookimb","leader","hour"]:
            a, b = float(r[k]), float(x[k])
            if np.isnan(a) != np.isnan(b) or (not np.isnan(a) and abs(a - b) > 1e-3 + 1e-5 * abs(b)): fbad[k] = fbad.get(k, 0) + 1
    fbad["лишних в эталоне"] = fbad.get("лишних в эталоне", 0) + sum(len(v) for v in rfk.values())
    print("признаки θ", th, "сверено строк", nf)
print("выходов", tot, "расхождений", bad, "| признаки, расхождений по столбцам:", {k: v for k, v in fbad.items() if v} or "нет")
