#!/usr/bin/env python3
"""TK-493 F7, ИНФОРМАЦИОННАЯ проба (правило НЕ применено): для клеток C1 буквальное W = 4 x полураспад даёт 1-2 минуты (полураспад спреда между биржами
меньше минуты), z-скор по окну из 2 наблюдений не оценка. Проба на ФОРМИРОВАНИИ: те же 13 значений entry_z при W = 60 и 240 минут
(НЕ назначено протоколом; значения - способ показать, существует ли безубыточная точка при невырожденном окне). Решение о W для C1 - за CEO / Судьёй.
  gen   - файлы заданий в data/xdl9/sel/probe/ и probe_cmds.sh (cd ~/xdl9/sel)
  parse - c1_probe.csv (рядом, в этой папке архива)
"""
import csv, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402
import sel_cells as SC  # noqa: E402

WS = (60, 240)
BIN = "$HOME/xdl9/bin/statarb-xdl9-02c6a0df"


def gen():
    pd = S.ensure(os.path.join(S.SEL, "probe"))
    cmds = []
    for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv")):
        if r["kind"] != "C1":
            continue
        cid = r["cell"]
        stem = cid.replace("|", "_").replace("~", "-")
        groups = {}
        with open(os.path.join(S.SEL, "cellpairs", cid.replace("|", "_") + ".csv"), encoding="utf-8", newline="") as fh:
            for p in csv.DictReader(fh):
                if p["phi"] == "":
                    continue
                groups.setdefault((p["ex_y"], p["ex_x"]), []).append((p["y"], p["x"]))
        for W in WS:
            cf = "%s_W%d.cells.csv" % (stem, W)
            with open(os.path.join(pd, cf), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n")
                for z in SC.ENTRY_GRID:
                    fh.write("%s|W%d|z%.2f,%d,%d,,%.2f,0,inf,%d,%d\n" % (cid, W, z, W, W, z, W, W))
            for (exy, exx), prs in sorted(groups.items()):
                jf = "%s__%s__%s.jobs.csv" % (stem, exy, exx)
                jp = os.path.join(pd, jf)
                if not os.path.exists(jp):
                    with open(jp, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write("y,x,from_day,to_day\n")
                        for y, x in prs:
                            fh.write("%s,%s,%d,%d\n" % (y, x, S.D_FORM0, S.D_TRADE0))
                out = "%s_W%d__%s__%s" % (stem, W, exy, exx)
                cmds.append("%s statarb run --kind cross --panels $HOME/xdl9/form/%s --panels-b $HOME/xdl9/form/%s --delay-x 1 --cells probe/%s "
                            "--jobs probe/%s --daily-out probe_out/%s.daily.csv --min-span 1440 --threads 8 >> probe_out/run.log 2>&1 || echo FAIL %s >> probe_out/run.log"
                            % (BIN, exy, exx, cf, jf, out, out))
    with open(os.path.join(pd, "probe_cmds.sh"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\nmkdir -p probe_out; : > probe_out/run.log\n" + "\n".join(cmds) + "\necho DONE >> probe_out/run.log\n")
    print("запусков", len(cmds))


def parse():
    od = os.path.join(S.SEL, "probe_out")
    agg = {}
    for f in sorted(os.listdir(od)):
        if f.endswith(".daily.csv"):
            with open(os.path.join(od, f), encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    a = agg.setdefault(r["cell"], [0.0, 0.0, 0.0])
                    a[0] += float(r["trades"]); a[1] += float(r["gross_bps"]); a[2] += float(r["fee_bps"])
    rows = []
    for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv")):
        if r["kind"] != "C1":
            continue
        for W in WS:
            pick, n1, mg1, best = None, 0, float("nan"), (None, None)
            for z in SC.ENTRY_GRID:
                n, g, fee = agg.get("%s|W%d|z%.2f" % (r["cell"], W, z), [0, 0, 0])
                if z == 1.0 and n:
                    n1, mg1 = int(n), g / n
                if n:
                    d = g / n - 2.0 * fee / n
                    if best[0] is None or d > best[0]:
                        best = (d, z)
                    if pick is None and d >= 0:
                        pick = (z, int(n), g / n, fee / n)
            rows.append([r["cell"], W, n1, "" if n1 == 0 else "%.3f" % mg1,
                         "" if pick is None else "%.2f" % pick[0], "" if pick is None else pick[1],
                         "" if best[0] is None else "%.3f" % best[0], "" if best[1] is None else "%.2f" % best[1]])
    S.write_csv(os.path.join(S.DOCS, "c1_probe.csv"),
                ["cell", "W_probe_min", "trades_z1.00", "mean_gross_z1.00_bps", "entry_z_star", "trades_at_star", "best_gross_minus_2fee_bps", "z_of_best"], rows)
    print("строк", len(rows), "клеток с безубыточной точкой", sum(1 for r in rows if r[4] != ""))


if __name__ == "__main__":
    {"gen": gen, "parse": parse}[sys.argv[1]]()
