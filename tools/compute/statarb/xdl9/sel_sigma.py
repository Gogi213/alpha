#!/usr/bin/env python3
"""TK-493 S1/S2 (П-37 r3.2 §6.6, Н13, аудит M-07/P-02; D5 iii, D10): σ_форм для МРЭ - стандартное отклонение суточных чистых bps клетки на ФОРМИРОВАНИИ, вторая половина
(08.10-14.10, 7 суток; первая половина - прогрев: задания идут с 01.10, значения суток 01.10-07.10 не используются). Те же клетки, пары и параметры, что в
волнах; панели ~/xdl9/form, фандинг ~/xdl9/funding_form (D2: ряды d формирования). Нормировка: суточное значение = Σ чистых bps сделок, закрытых в сутки,
по заданиям клетки / число АКТИВНЫХ пар-суток (`--job-day-out`, колонка active: z определён хотя бы в одной минуте суток, §10.1 [Н27]); сутки без активных
пар значения не дают. Нужно не меньше 5 суточных значений; σ - выборочное (ddof = 1); нет ни одной закрытой сделки за 7 суток - «не измерена».
S2 (r3.2): (D10) основной ряд клетки - закрытые сделки + MTM позиций, открытых на конце окна: запуск с --mtm-open, оценка (cells_out.mtm_net_bps) входит в последние сутки
формирования (14.10) числителем; (D5 iii) знаменатель D2 - активные пары-сутки ПО ПАНЕЛЯМ (d2_active_form.csv, sel_d2.py/d2_active.py), а не `active` ядра (окно z, правило
funding z не использует); kernel-активность печатается рядом.
  gen   - sigma_cmds.sh (cd ~/xdl9/sel): команды из sel_waves.py в режиме 'form' (sigma_cmds.csv)
  parse - docs/findings/xdl9-selection-2026-11-06/sigma_formation.csv
"""
import csv, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

DAY0 = 2472             # 08.10: первое учитываемое сутки (вторая половина формирования)
DAY1 = 2479             # не включая
LAST = DAY1 - 1           # последние сутки формирования (14.10): к ним относится MTM


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
    mtm_net = {}
    d2cnt = {}                              # (клетка D2 без руки, сутки) -> число активных пар по панелям
    p_act = os.path.join(S.SEL, "d2_active_form.csv")
    if os.path.exists(p_act):
        for r in S.read_csv(p_act):
            if r["active"] == "1":
                k = (r["cell"], int(r["day"]))
                d2cnt[k] = d2cnt.get(k, 0) + 1
    act_kernel = {}
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
                    m = float(r["mtm_net_bps"])
                    if m != m:
                        raise SystemExit("в %s нет оценки MTM (nan): запуск без --mtm-open" % f)
                    mtm_net[r["cell"]] = mtm_net.get(r["cell"], 0.0) + m
    for c in list(act):                      # D2: знаменатель по панелям (D5 iii); kernel-активность - справка
        if not c.startswith("D2|"):
            continue
        base = c.rsplit("|", 1)[0]
        act_kernel[c] = dict(act[c])
        act[c] = {}
        for d in range(DAY0, DAY1):
            if d2cnt.get((base, d)):
                act[c][d] = d2cnt[(base, d)]
    rows = []
    for cell in sorted(jobs):
        nt = dict(net.get(cell, {}))
        if mtm_net.get(cell):                                   # D10: MTM открытых позиций - в последние сутки формирования
            nt[LAST] = nt.get(LAST, 0.0) + mtm_net[cell]
        vals = [nt.get(d, 0.0) / act[cell][d] for d in range(DAY0, DAY1) if act.get(cell, {}).get(d, 0) > 0]
        n_tr = sum(trd.get(cell, {}).values())
        apd = sum(act.get(cell, {}).values())
        if len(vals) >= 5 and n_tr > 0:
            sig = float(np.std(vals, ddof=1))
            sg = "%.4f" % sig
        else:
            sg = "не измерена"
        apk = sum(act_kernel[cell].values()) if cell in act_kernel else ""
        rows.append([cell, jobs[cell], apd, len(vals), sum(1 for d in trd.get(cell, {}) if trd[cell][d] > 0), n_tr,
                     "%.4f" % (sum(vals) / len(vals)) if vals else "", sg, "%.4f" % mtm_net.get(cell, 0.0), apk])
    S.write_csv(os.path.join(S.DOCS, "sigma_formation.csv"),
                ["cell", "jobs_done", "active_pair_days", "daily_values", "days_with_trades", "trades", "mean_daily_bps_per_active_pair",
                 "sigma_bps_per_day_per_active_pair", "mtm_net_bps_last_day", "active_pair_days_kernel_d2"], rows)
    print("клеток-рук", len(rows), "σ измерена", sum(1 for r in rows if r[-1] != "не измерена"))


if __name__ == "__main__":
    {"gen": gen, "parse": parse}[sys.argv[1]]()
