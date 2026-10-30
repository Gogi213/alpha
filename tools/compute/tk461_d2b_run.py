#!/usr/bin/env python3
"""TK-461 D2b, Санчо: одна пара (монета, ТФ) -> ядро tk464-pair-sim по ключам (ev,dir) со своими ячейками (d2b-cells) и слоями дублей.
python tk461_d2b_run.py <SYM> <tf> <out.csv.gz>    (PYTHONPATH=/data/tk0455:/data/tk0461; окружение BIN, SRC, EV, CELLS, FUND, TMP)
Контроли C-X — как tk461_ctrl.controls (seed 461, тот же расход ГСЧ по строкам событий), но каждая строка контроля несёт ss_bps/ot1_bps/
ot1_fallback СВОЕГО события (выход тот же) и номер слоя rk (дубли (ev,dir,tt0) -> слои 0,1,..; событие сравнивается только с контролями своей строки).
Сложение групп слоёв: c,d,b,k,d2 аддитивны."""
import os
import subprocess
import sys

import numpy as np
import pandas as pd

import tk461_ctrl as C
from sancho import bars, atr14

SUMS = ["c", "d", "b", "k", "d2"]


def controls(path, tf, ev):
    a = bars(path, tf)
    ok = a.ok.values
    h, l, c = (a[x].values.astype(float) for x in "hlc")
    atr = atr14(h, l, c)
    ab = 1e4 * np.r_[np.nan, atr[:-1]] / np.abs(c)
    good = ok & np.r_[False, ok[:-1]] & np.isfinite(ab)
    ts = a.t.values
    slot = ts % C.DAY
    day = ts // C.DAY
    rng = np.random.default_rng(C.SEED)
    out = []
    for s in ev.to_dict("records"):
        d0 = s["tt0"] // C.DAY
        m = good & (slot == s["tt0"] % C.DAY) & (np.abs(day - d0) <= 30) & (day != d0) & (ab >= s["atr_bps"] / C.RATIO) & (ab <= s["atr_bps"] * C.RATIO)
        idx = np.flatnonzero(m)
        if len(idx) > C.NC:
            idx = np.sort(rng.choice(idx, C.NC, replace=False))
        for cj, j in enumerate(idx):
            out.append(dict(sym=s["sym"], tf=tf, ev="C-" + s["ev"], dir=s["dir"], side=s["side"], t0=int(j), tt0=int(ts[j]), atr_bps=float(ab[j]),
                            evk=int(s["tt0"]), cj=cj, rk=s["rk"], ss_bps=s["ss_bps"], ot1_bps=s["ot1_bps"], ot1_fallback=s["ot1_fallback"]))
    return pd.DataFrame(out)


def main():
    sym, tf, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    e = os.environ
    tmp = f"{e['TMP']}/{sym}-{tf}"
    os.makedirs(tmp, exist_ok=True)
    ev = pd.read_csv(f"{e['EV']}/{sym}-{tf}.csv.gz")
    ev["rk"] = ev.groupby(["ev", "dir", "tt0"]).cumcount()
    ct = controls(f"{e['SRC']}/{sym}.csv.gz", tf, ev)
    parts = []
    for (k_ev, k_dir), g in ev.groupby(["ev", "dir"]):
        cf = f"{e['CELLS']}/cells-{tf}-{k_ev}-{k_dir}.csv"
        if not os.path.exists(cf):
            continue
        for rk, ge in g.groupby("rk"):
            gc = ct[(ct.ev == "C-" + k_ev) & (ct.dir == k_dir) & (ct.rk == rk)] if len(ct) else ct
            if len(gc) == 0:
                continue
            pe, pc, po = f"{tmp}/{k_ev}-{k_dir}-{rk}.ev.csv", f"{tmp}/{k_ev}-{k_dir}-{rk}.ct.csv", f"{tmp}/{k_ev}-{k_dir}-{rk}.out.csv"
            ge.drop(columns=["rk"]).to_csv(pe, index=False, lineterminator="\n")
            gc.drop(columns=["rk"]).to_csv(pc, index=False, lineterminator="\n")
            subprocess.run([e["BIN"], f"{e['SRC']}/{sym}.csv.gz", str(tf), pe, e["FUND"], cf, po, "--threads", "1", "--pair", pc],
                           check=True, stderr=subprocess.DEVNULL)
            parts.append(pd.read_csv(po))
            for f in (pe, pc, po):
                os.remove(f)
    if not parts:
        pd.DataFrame().to_csv(outp, index=False, compression="gzip")
        return
    r = pd.concat(parts)
    keys = [c for c in r.columns if c not in SUMS]
    r.groupby(keys, dropna=False, sort=False)[SUMS].sum().reset_index().to_csv(outp, index=False, compression="gzip")


if __name__ == "__main__":
    main()
