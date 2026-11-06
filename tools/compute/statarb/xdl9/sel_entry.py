#!/usr/bin/env python3
"""TK-493 F7 шаг 5: калибровка entry_z на ФОРМИРОВАНИИ ядром (П-37 §6.2). Две стадии:

  gen    - (ПК) по cells_hl.csv и cellpairs/ пишет в data/xdl9/sel/entry/: на клетку cells-файл из 13 строк (entry_z = 1,00 ... 4,00 шаг 0,25;
           window = min_obs = refresh = W, exit_z = 0, stop_z = inf, max_hold = W), на (клетка, пара бирж Y/X) jobs-файл
           `y,x,2465,2479` (формирование целиком), и entry_cmds.sh для Deck. Панели - ~/xdl9/form/<биржа> (обрезаны по 15.10 00:00,
           для US-контрактов с маской кассы): торговое окно в файлах физически отсутствует.
  parse  - (ПК) суточные csv `run` -> по клетке и z: сделок, средний валовый и средняя комиссия на сделку; entry_z* = наименьшее z сетки, при
           котором средний валовый результат сделки >= m x средняя комиссия сделки клетки (m = 2; для двух перп-ног с b = 1 это 25,2 bps,
           для OLS-клеток комиссия - по фактической b каждой сделки, как round_trip_cost_bps ядра) и есть хотя бы одна сделка.
Внутривыборочная оценка (Н28): оптимистична по построению; число сделок на выбранном z пишется рядом.
"""
import csv, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402
import sel_cells as SC  # noqa: E402

M_FACTOR = 2.0
BIN = "$HOME/xdl9/bin/statarb-xdl9-02c6a0df"


def gen():
    cells_hl = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    ed = S.ensure(os.path.join(S.SEL, "entry"))
    cmds = []
    for cid, r in sorted(cells_hl.items()):
        if r["kind"] == "D2" or not r["W_min"]:
            continue
        W = int(r["W_min"])
        stem = cid.replace("|", "_").replace("~", "-")
        with open(os.path.join(ed, stem + ".cells.csv"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n")
            for z in SC.ENTRY_GRID:
                fh.write("%s|z%.2f,%d,%d,,%.2f,0,inf,%d,%d\n" % (cid, z, W, W, z, W, W))
        groups = {}
        with open(os.path.join(S.SEL, "cellpairs", cid.replace("|", "_") + ".csv"), encoding="utf-8", newline="") as fh:
            for p in csv.DictReader(fh):
                if p["hl_min"] == "" and p["phi"] == "":
                    continue          # пара без оценки полураспада (мало наблюдений) в калибровку не входит
                groups.setdefault((p["ex_y"], p["ex_x"]), []).append((p["y"], p["x"]))
        for (exy, exx), prs in sorted(groups.items()):
            jf = "%s__%s__%s.jobs.csv" % (stem, exy, exx)
            with open(os.path.join(ed, jf), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("y,x,from_day,to_day\n")
                for y, x in prs:
                    fh.write("%s,%s,%d,%d\n" % (y, x, S.D_FORM0, S.D_TRADE0))
            kind = r["kind"]
            if kind in ("A1", "A2"):
                k = "--kind pair --panels $HOME/xdl9/form/%s" % exy
            elif kind == "B1":
                k = "--kind basket --panels $HOME/xdl9/form/%s" % exy
            else:   # C1: две биржи, задержка ноги X +1 минута
                k = "--kind cross --panels $HOME/xdl9/form/%s --panels-b $HOME/xdl9/form/%s --delay-x 1" % (exy, exx)
            cmds.append("%s statarb run %s --cells entry/%s.cells.csv --jobs entry/%s --daily-out entry_out/%s.daily.csv "
                        "--cells-out entry_out/%s.cells_out.csv --min-span 1440 --threads 8 >> entry_out/run.log 2>&1 || echo FAIL %s >> entry_out/run.log"
                        % (BIN, k, stem, jf, jf[:-9], jf[:-9], jf))
    with open(os.path.join(ed, "entry_cmds.sh"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\n# TK-493 F7: калибровка entry_z на формировании (cd ~/xdl9/sel)\nmkdir -p entry_out; : > entry_out/run.log\n")
        fh.write("\n".join(cmds) + "\necho DONE >> entry_out/run.log\n")
    print("запусков", len(cmds))


def parse():
    cells_hl = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    od = os.path.join(S.SEL, "entry_out")
    agg = {}
    for f in sorted(os.listdir(od)):
        if not f.endswith(".daily.csv"):
            continue
        with open(os.path.join(od, f), encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                a = agg.setdefault(r["cell"], [0.0, 0.0, 0.0, 0.0])
                a[0] += float(r["trades"]); a[1] += float(r["gross_bps"]); a[2] += float(r["fee_bps"]); a[3] += float(r["funding_bps"])
    grid, res = [], []
    for cid, r in sorted(cells_hl.items()):
        if r["kind"] == "D2":
            continue
        if not r["W_min"]:
            res.append([cid, r["kind"], "", "", "", "", "", "полураспад не измерен: нет пар с phi in (0, 1)"])
            continue
        pick = None
        for z in SC.ENTRY_GRID:
            n, g, fee, _ = agg.get("%s|z%.2f" % (cid, z), [0, 0, 0, 0])
            mg = g / n if n else float("nan")
            mf = fee / n if n else float("nan")
            ok = bool(n) and mg >= M_FACTOR * mf
            grid.append([cid, "%.2f" % z, int(n), "" if not n else "%.3f" % mg, "" if not n else "%.3f" % mf, int(ok)])
            if ok and pick is None:
                pick = (z, int(n), mg, mf)
        if pick:
            res.append([cid, r["kind"], r["W_min"], "%.2f" % pick[0], pick[1], "%.3f" % pick[2], "%.3f" % pick[3], "торгуется"])
        else:
            res.append([cid, r["kind"], r["W_min"], "", "", "", "", "не торгуется: безубыточной точки на формировании нет"])
    S.write_csv(os.path.join(S.DOCS, "entry_grid.csv"), ["cell", "entry_z", "trades", "mean_gross_bps", "mean_fee_bps", "gross_ge_2fee"], grid)
    S.write_csv(os.path.join(S.DOCS, "entry_z.csv"),
                ["cell", "kind", "W_min", "entry_z", "trades_at_z", "mean_gross_bps", "mean_fee_bps", "status"], res)
    print("клеток", len(res), "торгуется", sum(1 for r in res if r[-1] == "торгуется"))


if __name__ == "__main__":
    {"gen": gen, "parse": parse}[sys.argv[1]]()
