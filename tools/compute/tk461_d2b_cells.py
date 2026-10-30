"""TK-461 D2b: ячейки выхода (X<=449) на ключ (ТФ, ev, dir, side) из P-TK461-be-k (P-TK461 §4, §12.2).
python tk461_d2b_cells.py <be-k.csv> <outdir>  -> <outdir>/cells-<tf>-<ev>-<dir>.csv + cells-index.csv (число ячеек на ключ)
Формат ячейки ядра: stop,m,take,par,be_r,ot,otp. Числа k/M/v/x — только из csv «открытия» (не подбираются)."""
import sys, os
import pandas as pd

STOPS = [("SF0.5", 0.5), ("SF1.0", 1.0), ("SF2.0", 2.0), ("SS", "")]
TAKES = [("TN", 0.0), ("TP", 0.5), ("TP", 1.0), ("TP", 2.0), ("TS", 0.0), ("TR", 0.5), ("TR", 1.0), ("TR", 2.0), ("TW", ""), ("PT", 1.0)]
OT_TAKES = [("TN", 0.0), ("TP", 1.0), ("TR", 1.0), ("PT", 1.0)]


def cells_for(bek):
    """bek: строки be-k одного (tf,ev,dir,side). Возвращает список ячеек."""
    mfe = {(r.stop, r.q): r.value for r in bek[bek.kind == "mfe_r"].itertuples()}
    pause = {r.q: r.value for r in bek[bek.kind == "pause"].itertuples()}
    agst = {r.q: r.value for r in bek[bek.kind == "against"].itertuples()}
    stops = [(s, m) for s, m in STOPS if (s, 0.5) in mfe]
    out = [("ST", 0.0, "TN", 0.0, "", "", "")]
    for s, m in stops:
        for t, p in TAKES:
            out.append((s, m, t, p, "", "", ""))
    for s, m in stops:  # БУ: 3 k x 40
        for q in (0.25, 0.5, 0.75):
            for t, p in TAKES:
                out.append((s, m, t, p, mfe[(s, q)], "", ""))
    ots = [("OT1", 0.2), ("OT1", 0.5), ("OT3", pause[0.5]), ("OT3", pause[0.75]), ("OT4", agst[0.9]), ("OT4", agst[0.99])]
    for with_be in (False, True):
        for s, m in stops:
            xs = [mfe[(s, q)] for q in (0.25, 0.5, 0.75)]
            otl = ots + [("OT5", x) for x in xs]
            be = mfe[(s, 0.5)] if with_be else ""
            for ot, v in otl:
                for t, p in OT_TAKES:
                    out.append((s, m, t, p, be, ot, v))
    return out


if __name__ == "__main__":
    d = pd.read_csv(sys.argv[1]); od = sys.argv[2]; os.makedirs(od, exist_ok=True)
    idx = []
    for (tf, ev, dr), g in d.groupby(["tf", "ev", "dir"]):
        c = cells_for(g)
        pd.DataFrame(c, columns="stop,m,take,par,be_r,ot,otp".split(",")).to_csv(f"{od}/cells-{tf}-{ev}-{dr}.csv", index=False, lineterminator="\n")
        idx.append((tf, ev, dr, int(g.side.iloc[0]), len(c)))
    pd.DataFrame(idx, columns=["tf", "ev", "dir", "side", "cells"]).to_csv(f"{od}/cells-index.csv", index=False, lineterminator="\n")
    print(len(idx), "ключей; ячеек на ключ:", sorted(set(i[4] for i in idx)))
