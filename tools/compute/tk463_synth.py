#!/usr/bin/env python3
"""TK-463: синтетика P-TK461 §9 п.2 для ядра tk463-sim: рукотворная серия минут (гэпы по open, дыры в минутах и в барах, широкие тени)
и входы под все режимы; эталон — tk461_scan.run (D2a) и tk463_d2b_ref (D2b), ядро — tk463-sim; сравнение по битам f64 — tk463_gate.py cmp.
python tk463_synth.py gen <каталог> <ТФ>   — SYNTHUSDT.csv.gz, ev-<ТФ>.csv (D2a), in-<ТФ>.csv (D2b), fund/SYNTHUSDT-fundingRate-2024-01.zip
python tk463_synth.py ref-d2a <каталог> <ТФ> — эталон D2a: ref-d2a-<ТФ>.csv.gz
python tk463_synth.py cover <ref.csv.gz>   — какие причины выхода покрыты (по stop/take/ot)"""
import os
import sys
import zipfile
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

T0 = 1704067200000  # 2024-01-01 00:00 UTC
NMIN = 4000


def gen(d, tf):
    os.makedirs(os.path.join(d, "fund"), exist_ok=True)
    rg = np.random.default_rng(463)
    r = rg.normal(0, 15e-4, NMIN)  # ~15 bps/мин
    jump = rg.random(NMIN) < 0.04  # гэпы open: ±60..250 bps
    gap = np.where(jump, rg.choice([-1, 1], NMIN) * rg.uniform(60e-4, 250e-4, NMIN), 0.0)
    c = 100.0 * np.exp(np.cumsum(r + gap))
    o = np.r_[100.0, c[:-1]] * np.exp(gap)
    hi = np.maximum(o, c) * (1 + np.abs(rg.normal(0, 10e-4, NMIN)))
    lo = np.minimum(o, c) * (1 - np.abs(rg.normal(0, 10e-4, NMIN)))
    tm = T0 + 60000 * np.arange(NMIN)
    keep = rg.random(NMIN) > 0.01  # дыры в минутах
    keep[1500:1500 + 3 * tf] = False  # дыра в несколько баров
    keep[:2] = True
    df = pd.DataFrame(dict(open_time=tm, open=o, high=hi, low=lo, close=c, volume=1.0))[keep]
    df.to_csv(os.path.join(d, "SYNTHUSDT.csv.gz"), index=False, compression="gzip")
    zf = zipfile.ZipFile(os.path.join(d, "fund", "SYNTHUSDT-fundingRate-2024-01.zip"), "w")
    ft = T0 + np.arange(0, NMIN * 60000, 8 * 3600000)
    zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n" +
                "".join(f"{t},8,{x:.8f}\n" for t, x in zip(ft, rg.normal(1e-4, 3e-4, len(ft)))))
    zf.close()
    nb = NMIN // tf - 14
    rows = []
    for i in range(nb):
        for dr in (1, -1):
            if rg.random() < 0.5:
                a = float(rg.uniform(15, 80))
                rows.append(dict(ev="EV%d" % int(rg.integers(3)), dir=dr, side=dr, t0=i + 2, tt0=T0 + (i + 1) * tf * 60000 + int(rg.integers(2)) * tf * 60000,
                                 atr_bps=a, ss_bps=float(a * rg.uniform(0.6, 4)) if rg.random() > 0.15 else np.nan, ot1_bps=a * rg.uniform(0.3, 1.5)))
    ev = pd.DataFrame(rows)
    ev[["ev", "dir", "side", "t0", "tt0", "atr_bps"]].to_csv(os.path.join(d, f"ev-{tf}.csv"), index=False)
    ev.to_csv(os.path.join(d, f"in-{tf}.csv"), index=False)
    print("events", len(ev), "минут", int(keep.sum()))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        gen(sys.argv[2], int(sys.argv[3]))
    elif cmd == "ref-d2a":
        import tk461_scan as K
        d, tf = sys.argv[2], int(sys.argv[3])
        K.run(os.path.join(d, "SYNTHUSDT.csv.gz"), tf, os.path.join(d, f"ev-{tf}.csv"), os.path.join(d, "fund")).to_csv(
            os.path.join(d, f"ref-d2a-{tf}.csv.gz"), index=False, compression="gzip")
    elif cmd == "cells":  # ячейки D2b гейта + широкие стопы (SF10/SS·большие ATR), чтобы ОТ срабатывали раньше стопа
        import tk463_d2b_ref as R
        extra = [("SF10", 10.0, "TN", 0.0, "", "OT1", 0.2), ("SF10", 10.0, "TN", 0.0, "", "OT4", 1.0), ("SF10", 10.0, "TR", 1.0, 0.5, "OT4", 0.5),
                 ("SF10", 10.0, "PT", 1.0, "", "OT3", 2), ("SF10", 10.0, "TP", 3.0, 0.5, "OT5", 0.5), ("SF10", 10.0, "TW", "", "", "OT1", 0.5),
                 ("SF10", 10.0, "TN", 0.0, 0.5, "OT3", 3), ("SF10", 10.0, "TN", 0.0, "", "OT5", 0.25), ("SS", "", "TN", 0.0, "", "OT4", 1.0)]
        pd.DataFrame(R.CELLS + extra, columns=["stop", "m", "take", "par", "be_r", "ot", "otp"]).to_csv(sys.argv[2], index=False)
    elif cmd == "cover":
        x = pd.read_csv(sys.argv[2])
        print(x.groupby(["stop", "take", "ot"] if "ot" in x else ["stop", "take"], dropna=False).reason.value_counts().unstack(fill_value=0).to_string())
    else:
        sys.exit(__doc__)
