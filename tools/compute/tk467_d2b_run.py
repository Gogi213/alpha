#!/usr/bin/env python3
"""TK-467, D2b Санчо: пара (монета, ТФ) -> ОДИН вызов ядра tk467-pair-sim (вместо вызова на каждую группу (ev, dir, слой) у tk461_d2b_run.py).
python tk467_d2b_run.py <SYM> <tf> <out.csv.gz>    (PYTHONPATH=/data/tk0455:/data/tk0461; окружение BIN, SRC, EV, CELLS, FUND, TMP)
Расчёт тот же, что у tk461_d2b_run: контроли — его controls(); слой дублей rk кодируется в имени ev ("A1~2", слой 0 без суффикса) у событий,
контролей и ячеек, поэтому событие сравнивается только с контролями своего слоя; ячейки каждого ключа (ev, dir) берутся из d2b-cells и
несут колонки ev, dir (область ячейки — ядро считает ячейку только для событий своего ключа). Ядро (--merge-layers 1) снимает суффикс слоя
и складывает слои (c,d,b,k,d2 аддитивны) и пишет .csv.gz само. Фазы со временем — в stderr."""
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from tk461_d2b_run import controls


def lay(df):
    return df.ev.values.astype(str) + np.where(df.rk.values > 0, "~" + df.rk.values.astype(str), "")


def main():
    t0 = time.time()
    sym, tf, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    e = os.environ
    tmp = f"{e['TMP']}/{sym}-{tf}"
    os.makedirs(tmp, exist_ok=True)
    ev = pd.read_csv(f"{e['EV']}/{sym}-{tf}.csv.gz")
    assert not ev.ev.str.contains("~").any()
    ev["rk"] = ev.groupby(["ev", "dir", "tt0"]).cumcount()
    ct = controls(f"{e['SRC']}/{sym}.csv.gz", tf, ev)
    t_ctl = time.time()
    keys = {}  # (ev, dir) -> файл ячеек
    for k_ev, k_dir in ev[["ev", "dir"]].drop_duplicates().itertuples(index=False):
        cf = f"{e['CELLS']}/cells-{tf}-{k_ev}-{k_dir}.csv"
        if os.path.exists(cf):
            keys[(k_ev, k_dir)] = cf
    if len(ct) == 0 or not keys:
        pd.DataFrame().to_csv(outp, index=False, compression="gzip")
        return
    ev = ev[[(a, b) in keys for a, b in zip(ev.ev, ev.dir)]]
    ct = ct[[(a[2:], b) in keys for a, b in zip(ct.ev, ct.dir)]]
    ct = ct.assign(ev=lay(ct))  # ct.ev уже "C-"+ev
    ev = ev.assign(ev=lay(ev))
    pe, pc, pcell, po = (f"{tmp}/{n}" for n in ("ev.csv", "ct.csv", "cells.csv", "out.csv.gz"))
    ev.drop(columns=["rk"]).to_csv(pe, index=False, lineterminator="\n")
    ct.drop(columns=["rk"]).to_csv(pc, index=False, lineterminator="\n")
    layers = ev.assign(base=ev.ev.str.split("~").str[0]).groupby(["base", "dir"]).ev.unique()
    with open(pcell, "w") as w:
        for i, ((k_ev, k_dir), names) in enumerate(sorted(layers.items())):
            lines = open(keys[(k_ev, k_dir)]).read().splitlines()
            if i == 0:
                w.write(lines[0] + ",ev,dir\n")
            for nm in names:
                w.writelines(f"{ln},{nm},{k_dir}\n" for ln in lines[1:])
    t_prep = time.time()
    subprocess.run([e["BIN"], f"{e['SRC']}/{sym}.csv.gz", str(tf), pe, e["FUND"], pcell, po, "--threads", "1", "--pair", pc, "--merge-layers", "1"]
                   + (["--stream", "1"] if e.get("STREAM") == "1" else []),  # TK-469: потоковый режим ядра, умолчание = прежний
                   check=True, stderr=subprocess.DEVNULL)
    t_ker = time.time()
    os.replace(po, outp)
    for f in (pe, pc, pcell):
        os.remove(f)
    print(f"{sym}-{tf}: события {len(ev)} контроли {len(ct)}; controls {t_ctl - t0:.1f} с, подготовка {t_prep - t_ctl:.1f} с, ядро {t_ker - t_prep:.1f} с",
          file=sys.stderr)


if __name__ == "__main__":
    main()
