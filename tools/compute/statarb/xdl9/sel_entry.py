#!/usr/bin/env python3
"""TK-493 S1 шаг 5: калибровка entry_z на ФОРМИРОВАНИИ ядром 4ef45cc8 с --rearm (П-37 r3.1 §6.2). Две стадии:

  gen    - (ПК) по cells_hl.csv и cellpairs/ пишет в data/xdl9/sel/entry/: на клетку cells-файл из ДОСТИЖИМЫХ значений сетки (z <= z_max(W) =
           sqrt(W-1); entry_z = 1,00 ... 4,00 шаг 0,25; window = min_obs = refresh = max_hold = W, exit_z = 0, stop_z = inf), на (клетка, пара бирж
           Y/X) jobs-файл `y,x,2465,2479` (формирование целиком) и entry_cmds.sh для Deck. Панели - ~/xdl9/form/<биржа> (обрезаны по 15.10 00:00,
           для US-контрактов с маской кассы): торговое окно в файлах физически отсутствует. Флаги ядра: --rearm (основная рука r3.1, Н35),
           C1 - ещё --delay-x 1 (клетки идут одноклеточным проходом), без фандинга, nice 19, 4 потока.
  parse  - (ПК) суточные csv `run` -> по клетке и z: сделок N, средний валовый и средняя комиссия на сделку, 90 %-интервал среднего валового
           (сутки как кластеры: отношение сумм, t-квантиль, df = число суток со сделками - 1). entry_z* = наименьшее ДОСТИЖИМОЕ z сетки, при котором
           (1) N >= N_форм,min = 20 сделок в пуле клетки, (2) средний валовый >= m x средняя комиссия сделки клетки (m = 2; для двух перп-ног при
           b = 1 это 25,2 bps, для OLS-клеток комиссия по фактической b каждой сделки, как round_trip_cost_bps ядра). Статусы:
             торгуется;
             не торгуется: мало сделок формирования - условие (2) выполняется, но только при N < 20 (число не подставляется);
             не торгуется: безубыточной точки нет на достижимой части сетки - (2) не выполняется ни при каком достижимом z.
Внутривыборочная оценка (Н28): оптимистична по построению; число сделок и интервал на выбранном z пишутся рядом.
"""
import csv, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sel_lib as S  # noqa: E402
import sel_cells as SC  # noqa: E402
import portfolio as PF  # noqa: E402  (t_ppf)

M_FACTOR = 2.0
N_FORM_MIN = 20           # ⌈min_N · 14/21⌉ = ⌈30 · 14/21⌉ (П-37 §6.2 (б), Н39)
BIN = "$HOME/xdl9/bin/statarb-xdl9-4ef45cc8"


def stem_of(cid):
    return cid.replace("|", "_").replace("~", "-")


def gen():
    cells_hl = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    ed = S.ensure(os.path.join(S.SEL, "entry"))
    cmds = []
    for cell, r in sorted(cells_hl.items()):
        if r["kind"] == "D2" or not r["W_min"]:
            continue
        W = int(r["W_min"])
        stem = stem_of(cell)
        base = cell.rsplit("|w", 1)[0] if r["kind"] == "C1" else cell
        with open(os.path.join(ed, stem + ".cells.csv"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n")
            for z in SC.reachable_grid(W):
                fh.write("%s|z%.2f,%d,%d,,%.2f,0,inf,%d,%d\n" % (cell, z, W, W, z, W, W))
        groups = {}
        with open(os.path.join(S.SEL, "cellpairs", base.replace("|", "_") + ".csv"), encoding="utf-8", newline="") as fh:
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
            cmds.append("nice -n 19 %s statarb run %s --rearm --cells entry/%s.cells.csv --jobs entry/%s --daily-out entry_out/%s.daily.csv "
                        "--min-span 1440 --threads 4 >> entry_out/run.log 2>&1 || echo FAIL %s >> entry_out/run.log"
                        % (BIN, k, stem, jf, jf[:-9], jf))
    with open(os.path.join(ed, "entry_cmds.sh"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\n# TK-493 S1: калибровка entry_z на формировании, ядро 4ef45cc8 --rearm (cd ~/xdl9/sel)\nmkdir -p entry_out; : > entry_out/run.log\n")
        fh.write("\n".join(cmds) + "\necho DONE >> entry_out/run.log\n")
    print("запусков", len(cmds))


def day_cluster_ci(days, alpha=0.10):
    """days: {сутки: [сделок, Σвалового]} -> (N, среднее, нижняя, верхняя) 90 %-интервала среднего валового на сделку."""
    d = [(n, g) for n, g in days.values() if n > 0]
    N = sum(n for n, _ in d)
    if N == 0:
        return 0, float("nan"), float("nan"), float("nan")
    mean = sum(g for _, g in d) / N
    D = len(d)
    if D < 2:
        return N, mean, float("nan"), float("nan")
    ss = sum((g - mean * n) ** 2 for n, g in d)
    se = math.sqrt(D / (D - 1) * ss) / N
    t = PF.t_ppf(1.0 - alpha / 2.0, D - 1)
    return N, mean, mean - t * se, mean + t * se


def parse():
    cells_hl = {r["cell"]: r for r in S.read_csv(os.path.join(S.DOCS, "cells_hl.csv"))}
    od = os.path.join(S.SEL, "entry_out")
    agg = {}        # cell|zX -> {сутки: [сделок, Σвалового]}; fee: cell|zX -> [Σкомиссии, сделок]
    fee = {}
    for f in sorted(os.listdir(od)):
        if not f.endswith(".daily.csv"):
            continue
        with open(os.path.join(od, f), encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                a = agg.setdefault(r["cell"], {}).setdefault(int(r["day"]), [0.0, 0.0])
                a[0] += float(r["trades"]); a[1] += float(r["gross_bps"])
                b = fee.setdefault(r["cell"], [0.0, 0.0])
                b[0] += float(r["fee_bps"]); b[1] += float(r["trades"])
    grid, res = [], []
    for cell, r in sorted(cells_hl.items()):
        if r["kind"] == "D2":
            continue
        if not r["W_min"]:
            res.append([cell, r["kind"], "", "", "", "", "", "", "", "", "", "полураспад не измерен: нет пар с phi in (0, 1)"])
            continue
        W = int(r["W_min"])
        zr = SC.reachable_grid(W)
        pick = None
        any_cost_ok = False
        for z in SC.ENTRY_GRID:
            key = "%s|z%.2f" % (cell, z)
            if z not in zr:
                grid.append([cell, "%.2f" % z, 0, "", "", "", "", 0, 0, 0])      # недостижимо: z > sqrt(W-1)
                continue
            N, mg, lo, hi = day_cluster_ci(agg.get(key, {}))
            fs, fn = fee.get(key, [0.0, 0.0])
            mf = fs / fn if fn else float("nan")
            cost_ok = bool(N) and mg >= M_FACTOR * mf
            n_ok = N >= N_FORM_MIN
            any_cost_ok = any_cost_ok or cost_ok
            grid.append([cell, "%.2f" % z, int(N), "" if not N else "%.3f" % mg, "" if not N else "%.3f" % mf,
                         "" if lo != lo else "%.3f" % lo, "" if hi != hi else "%.3f" % hi, int(cost_ok), int(n_ok), 1])
            if cost_ok and n_ok and pick is None:
                pick = (z, int(N), mg, mf, lo, hi)
        ur = r["grid_unreachable_share"]
        if pick:
            res.append([cell, r["kind"], W, r["z_max"], "%.2f" % pick[0], pick[1], "%.3f" % pick[2], "%.3f" % pick[3],
                        "" if pick[4] != pick[4] else "%.3f" % pick[4], "" if pick[5] != pick[5] else "%.3f" % pick[5], ur, "торгуется"])
        elif any_cost_ok:
            res.append([cell, r["kind"], W, r["z_max"], "", "", "", "", "", "", ur, "не торгуется: мало сделок формирования"])
        else:
            res.append([cell, r["kind"], W, r["z_max"], "", "", "", "", "", "", ur,
                        "не торгуется: безубыточной точки нет на достижимой части сетки"])
    S.write_csv(os.path.join(S.DOCS, "entry_grid.csv"),
                ["cell", "entry_z", "trades", "mean_gross_bps", "mean_fee_bps", "gross_lo90", "gross_hi90", "gross_ge_2fee", "trades_ge_min", "reachable"], grid)
    S.write_csv(os.path.join(S.DOCS, "entry_z.csv"),
                ["cell", "kind", "W_min", "z_max", "entry_z", "trades_at_z", "mean_gross_bps", "mean_fee_bps", "gross_lo90", "gross_hi90",
                 "grid_unreachable_share", "status"], res)
    from collections import Counter
    print("клеток", len(res), dict(Counter(r[-1] for r in res)))


if __name__ == "__main__":
    {"gen": gen, "parse": parse}[sys.argv[1]]()
