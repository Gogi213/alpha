#!/usr/bin/env python3
"""TK-493 S1 (П-37 r3.1 §6.6, Н13, аудит M-07/P-02): σ_форм для МРЭ - стандартное отклонение суточных чистых bps клетки на ФОРМИРОВАНИИ, вторая половина
(08.10-14.10, 7 суток; первая половина - прогрев: задания идут с 01.10, значения суток 01.10-07.10 не используются). Те же клетки, пары и параметры, что в
волнах; панели ~/xdl9/form, фандинг ~/xdl9/funding_form (D2: ряды d формирования). Нормировка: суточное значение = Σ чистых bps сделок, закрытых в сутки,
по заданиям клетки / число АКТИВНЫХ пар-суток (`--job-day-out`, колонка active: z определён хотя бы в одной минуте суток, §10.1 [Н27]); сутки без активных
пар значения не дают. Нужно не меньше 5 суточных значений; σ - выборочное (ddof = 1); нет ни одной закрытой сделки за 7 суток - «не измерена».
  gen   - sigma_cmds.sh (cd ~/xdl9/sel): команды из sel_waves.py в режиме 'form' (sigma_cmds.csv)
  parse - docs/findings/xdl9-selection-s1-2026-11-06/sigma_formation.csv
"""
import csv, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

DAY0 = 2472             # 08.10: первое учитываемое сутки (вторая половина формирования)
DAY1 = 2479             # не включая
BIN = "$HOME/xdl9/bin/statarb-xdl9-4ef45cc8"


def gen():
    cmds = []
    for r in S.read_csv(os.path.join(S.SEL, "sigma_cmds.csv")):
        c = r["cmd"].replace("--cells sigma/", "--cells waves/sigma/").replace("--jobs sigma/", "--jobs waves/sigma/")
        cmds.append(c + " >> sigma_out/run.log 2>&1 || echo FAIL %s >> sigma_out/run.log" % r["stem"])
    with open(os.path.join(S.SEL, "sigma_cmds.sh"), "w", encoding="utf-8", newline=chr(10)) as fh:
        fh.write(chr(10).join(["#!/usr/bin/env bash", "# TK-493 S1: sigma_форм на формировании, ядро 4ef45cc8 (cd ~/xdl9/sel)", "set -u", "THREADS=${THREADS:-4}",
                               "mkdir -p sigma_out; : > sigma_out/run.log"] + cmds + ["echo DONE >> sigma_out/run.log", ""]))
    print("запусков", len(cmds))


def parse():
    od = os.path.join(S.SEL, "sigma_out")
    net, act, trd, jobs = {}, {}, {}, {}
    for f in sorted(os.listdir(od)):
        p = os.path.join(od, f)
        if f.endswith(".jobday.csv"):
            with open(p, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    d = int(r["day"])
                    if d < DAY0 or d >= DAY1:
                        continue
                    c = r["cell"]
                    net.setdefault(c, {}); act.setdefault(c, {}); trd.setdefault(c, {})
                    net[c][d] = net[c].get(d, 0.0) + float(r["net_bps"])
                    act[c][d] = act[c].get(d, 0) + int(float(r["active"]))
                    trd[c][d] = trd[c].get(d, 0) + int(float(r["trades"]))
        elif f.endswith(".cells_out.csv"):
            with open(p, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    jobs[r["cell"]] = jobs.get(r["cell"], 0) + int(float(r["done"]))
    rows = []
    for cell in sorted(jobs):
        vals = [net[cell].get(d, 0.0) / act[cell][d] for d in range(DAY0, DAY1) if act.get(cell, {}).get(d, 0) > 0]
        n_tr = sum(trd.get(cell, {}).values())
        apd = sum(act.get(cell, {}).values())
        if len(vals) >= 5 and n_tr > 0:
            sig = float(np.std(vals, ddof=1))
            sg = "%.4f" % sig
        else:
            sg = "не измерена"
        rows.append([cell, jobs[cell], apd, len(vals), sum(1 for d in trd.get(cell, {}) if trd[cell][d] > 0), n_tr,
                     "%.4f" % (sum(vals) / len(vals)) if vals else "", sg])
    S.write_csv(os.path.join(S.DOCS, "sigma_formation.csv"),
                ["cell", "jobs_done", "active_pair_days", "daily_values", "days_with_trades", "trades", "mean_daily_bps_per_active_pair",
                 "sigma_bps_per_day_per_active_pair"], rows)
    print("клеток-рук", len(rows), "σ измерена", sum(1 for r in rows if r[-1] != "не измерена"))


if __name__ == "__main__":
    {"gen": gen, "parse": parse}[sys.argv[1]]()
