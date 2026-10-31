#!/usr/bin/env python3
"""TK-470 (хвост TK-461): контроли C1/C2 (P455 §4 = P-TK451 §5) для событий Санчо A1/A4–A8 на ядре TK-463, по паре (монета, ТФ).
python tk470_c12_run.py <SYM> <tf> <outdir>      (PYTHONPATH=/data/tk0455:/data/tk0461:/data/tk0467; окружение BIN, SRC, EV, CELLS, FUND, TMP, STREAM)
Реальные события — d2b-ev (те же, что у D2b); контроли — sancho_l3_sig.sig_table(ctl=True) (те же выборки и seed, что слой 2). Контроль C1/C2
привязан к реальному событию по (ev, dir, evi): evk = tt0 реального, слой дублей rk — тот же, что у реального (суффикс «~k» в ev). Ядро
в парном режиме (--pair --merge-layers 1) выдаёт те же агрегаты c,d,b,k,d2, что для C-X (d = событие − контроль), поэтому C1 и C2 считаются
двумя вызовами (отдельные файлы <outdir>/c1|c2/<SYM>-<tf>.csv.gz): вердикт требует знак против каждого. Ядро и ячейки — без правок;
префикс "C-" у ev контроля — как у C-X (ядро срезает 2 знака). Колонки ss_bps/ot1_bps у контроля — от псевдо-уровня (add_cols TK-461)."""
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from sancho import bars
from sancho_l3_sig import sig_table
from sancho_l3_trades import side_of
from tk461_d2b_cols import add_cols
from tk467_d2b_run import lay


def ctl_frames(src, tf, ev):
    """ev — реальные события пары (с rk). -> {"C1": df, "C2": df} в формате pair-файла ядра."""
    a = bars(src, tf)
    d = sig_table(src, tf, 2, ctl=True)
    c = d[d.role.isin(["C1", "C2"])].copy()
    if len(c) == 0:
        return {}
    c.insert(0, "sym", os.path.basename(src).split(".")[0])
    c.insert(1, "tf", tf)
    c["side"] = [side_of(e, n) for e, n in zip(c.ev, c.dir)]
    m = pd.read_csv(src, usecols=[0, 1])
    c = add_cols(c, m.iloc[:, 0].values, m.iloc[:, 1].values.astype(float), tf, a)
    key = ev[["ev", "dir", "evi", "tt0", "rk"]].rename(columns={"tt0": "evk"})
    c = c.merge(key, on=["ev", "dir", "evi"], how="inner")  # контроль без реального события того же класса не нужен
    c["cj"] = c.groupby(["role", "ev", "dir", "evk", "rk"]).cumcount()
    c["ev"] = "C-" + lay(c)
    return {r: c[c.role == r].drop(columns=["rk", "role"]) for r in ("C1", "C2")}


def main():
    t0 = time.time()
    sym, tf, outd = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    e = os.environ
    src = f"{e['SRC']}/{sym}.csv.gz"
    tmp = f"{e['TMP']}/{sym}-{tf}"
    os.makedirs(tmp, exist_ok=True)
    ev = pd.read_csv(f"{e['EV']}/{sym}-{tf}.csv.gz")
    ev["rk"] = ev.groupby(["ev", "dir", "tt0"]).cumcount()
    frames = ctl_frames(src, tf, ev)
    keys = {}
    for k_ev, k_dir in ev[["ev", "dir"]].drop_duplicates().itertuples(index=False):
        cf = f"{e['CELLS']}/cells-{tf}-{k_ev}-{k_dir}.csv"
        if os.path.exists(cf):
            keys[(k_ev, k_dir)] = cf
    ev = ev[[(a, b) in keys for a, b in zip(ev.ev, ev.dir)]]
    ev = ev.assign(ev=lay(ev))
    pe, pcell = f"{tmp}/ev.csv", f"{tmp}/cells.csv"
    ev.drop(columns=["rk"]).to_csv(pe, index=False, lineterminator="\n")
    layers = ev.assign(base=ev.ev.str.split("~").str[0]).groupby(["base", "dir"]).ev.unique()
    with open(pcell, "w") as w:
        for i, ((k_ev, k_dir), names) in enumerate(sorted(layers.items())):
            lines = open(keys[(k_ev, k_dir)]).read().splitlines()
            if i == 0:
                w.write(lines[0] + ",ev,dir\n")
            for nm in names:
                w.writelines(f"{ln},{nm},{k_dir}\n" for ln in lines[1:])
    for r, ct in frames.items():
        od = f"{outd}/{r.lower()}"
        os.makedirs(od, exist_ok=True)
        ct = ct[[(a[2:].split("~")[0], b) in keys for a, b in zip(ct.ev, ct.dir)]]
        outp, po, pc = f"{od}/{sym}-{tf}.csv.gz", f"{tmp}/{r}-out.csv.gz", f"{tmp}/{r}-ct.csv"
        if len(ct) == 0:
            pd.DataFrame().to_csv(outp, index=False, compression="gzip")
            continue
        ct.to_csv(pc, index=False, lineterminator="\n")
        subprocess.run([e["BIN"], src, str(tf), pe, e["FUND"], pcell, po, "--threads", "1", "--pair", pc, "--merge-layers", "1"]
                       + (["--stream", "1"] if e.get("STREAM") == "1" else []), check=True, stderr=subprocess.DEVNULL)
        os.replace(po, outp)
        os.remove(pc)
    for f in (pe, pcell):
        os.remove(f)
    print(f"{sym}-{tf}: контроли {({r: len(c) for r, c in frames.items()})}; {time.time() - t0:.1f} с", file=sys.stderr)


if __name__ == "__main__":
    main()
