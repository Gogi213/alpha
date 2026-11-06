#!/usr/bin/env python3
"""TK-493 F7 (П-37 §6.6, Н13): σ_форм для МРЭ - стандартное отклонение суточных чистых bps клетки на ФОРМИРОВАНИИ, вторая половина (08.10-14.10,
7 суток; первая половина - прогрев окна оценки: задания идут с from_day = 2472, окно греется на W минут раньше). Те же клетки, те же пары и параметры,
что в волнах; панели - ~/xdl9/form (обрезаны по 15.10 00:00). Суточное значение = Σ чистых bps сделок, закрытых в сутки, / число выполненных заданий клетки
(приближение «активной пары-суток» §10.1: активность по минутам не считалась - НАЗНАЧЕНО без Судьи, подтвердить); сутки без закрытых сделок = 0.
Нужно не меньше 5 суточных значений (всегда 7 при таком задании); σ - выборочное (ddof = 1).
  gen   - sigma_cmds.sh (cd ~/xdl9/sel) и jobs-файлы sigma/
  parse - docs/findings/xdl9-selection-2026-11-06/sigma_formation.csv
"""
import csv, os, re, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

D0, D1 = 2472, 2479


def gen():
    sd = S.ensure(os.path.join(S.SEL, "sigma"))
    cmds = []
    for m in S.read_csv(os.path.join(S.DOCS, "waves_manifest.csv")):
        w, jf = m["wave"], m["jobs_file"]
        out = jf[:-9]
        with open(os.path.join(S.SEL, "waves", "w" + w, jf), encoding="utf-8", newline="") as fh, \
                open(os.path.join(sd, jf), "w", encoding="utf-8", newline="\n") as fo:
            rd = csv.reader(fh); next(rd)
            fo.write("y,x,from_day,to_day\n")
            for y, x, _, _ in rd:
                fo.write("%s,%s,%d,%d\n" % (y, x, D0, D1))
        c = m["cmd"].replace("$HOME/xdl9/trade/", "$HOME/xdl9/form/")
        c = re.sub(r"--cells (w\d)/(\S+)", r"--cells waves/\1/\2", c)
        c = re.sub(r"--jobs w\d/\S+", "--jobs sigma/" + jf, c)
        c = re.sub(r"--daily-out \S+", "--daily-out sigma_out/%s.daily.csv" % out, c)
        c = re.sub(r"--cells-out \S+", "--cells-out sigma_out/%s.cells_out.csv" % out, c)
        c = re.sub(r" --trades-out \S+", "", c)
        c = re.sub(r" --trade-jobs \S+", "", c)
        c = c.replace(" --mtm-open", "")
        cmds.append(c + " >> sigma_out/run.log 2>&1 || echo FAIL %s >> sigma_out/run.log" % out)
    with open(os.path.join(sd, "sigma_cmds.sh"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nmkdir -p sigma_out; : > sigma_out/run.log\n" + "\n".join(cmds) + "\necho DONE >> sigma_out/run.log\n")
    print("запусков", len(cmds))


def parse():
    od = os.path.join(S.SEL, "sigma_out")
    net, done = {}, {}
    for f in sorted(os.listdir(od)):
        p = os.path.join(od, f)
        if f.endswith(".daily.csv"):
            with open(p, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    net.setdefault(r["cell"], {})
                    d = int(r["day"])
                    net[r["cell"]][d] = net[r["cell"]].get(d, 0.0) + float(r["net_bps"])
        elif f.endswith(".cells_out.csv"):
            with open(p, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    done[r["cell"]] = done.get(r["cell"], 0) + int(float(r["done"]))
    rows = []
    for cell in sorted(done):
        n = done[cell]
        vals = [net.get(cell, {}).get(d, 0.0) / n if n else float("nan") for d in range(D0, D1)]
        dwt = sum(1 for d in range(D0, D1) if d in net.get(cell, {}))
        sig = float(np.std(vals, ddof=1)) if n and len(vals) >= 5 and dwt > 0 else float("nan")   # нет закрытых сделок - σ не измерена
        rows.append([cell, n, len(vals), dwt, "%.4f" % (sum(vals) / len(vals)) if n else "",
                     "%.4f" % sig if sig == sig else "не измерена"])
    S.write_csv(os.path.join(S.DOCS, "sigma_formation.csv"),
                ["cell", "jobs_done", "daily_values", "days_with_trades", "mean_daily_bps_per_pair", "sigma_bps_per_day_per_pair"], rows)
    print("клеток", len(rows))


if __name__ == "__main__":
    {"gen": gen, "parse": parse}[sys.argv[1]]()
