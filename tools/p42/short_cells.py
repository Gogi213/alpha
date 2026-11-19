"""TK-618 шаг 1б: все клетки сетки TK-599 одной стороны на базе (проход 1, портфельные прогоны) — Σ $, сделок, $/сделку, $/сут. по месяцам. Движок не запускается:
читаются готовые `out/<окно>/<vid>/<side>-trades.csv` TK-599. Описательно (0 испытаний); каждая строка, по которой потом что-то выбирается, — «найдено на этих данных».
  python3 short_cells.py <tk599-grids.json> <out-root TK-599> <выход.md> [S|L]
Клетки оси `F` не читаются (нужны таблицы признаков по свёрткам)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402
import short_autopsy_base as sb  # noqa: E402

MON = rf.MONTH_NAMES
LEN = dict(zip(MON, rf.MONTH_LEN))


def cell_stats(out_root, vid, side_name):
    try:
        rows = sb.spliced_rows(lambda w: os.path.join(out_root, w, vid, side_name + "-trades.csv"))
    except OSError:
        return None
    pnl = np.array([float(r["pnl"]) for r in rows])
    mon = np.array([r["month"] for r in rows])
    return pnl, mon


def main(grids, out_root, outp, side="S"):
    spec = rf.Spec.load(grids)
    side_name = rf.ENGINE_SIDE[side]
    cells = [("base", "", rf.Req(side, rf.cfg_keys(spec, side, {}), False))]
    for ax in spec.axes[side]:
        if ax["axis"] == "F":
            continue
        for c in ax["cells"] + [x for x in ax["ext"].values() if x]:
            cfg = {ax["axis"]: c}
            cells.append((ax["axis"], c["id"], rf.Req(side, rf.cfg_keys(spec, side, cfg), False)))
    res = []
    for axis, cid, req in cells:
        st = cell_stats(out_root, req.vid, side_name)
        res.append((axis, cid, req, st))
    base = res[0][3]
    bS, bN = base[0].sum(), len(base[0])
    out = ["# Клетки сетки TK-599 (%s), портфельные прогоны на базе, 01.07–13.11 (описательно)\n" % side,
           "База: Σ %.2f $, %d сделок, %+.3f $/сделку, %.2f $/сут. (136 сут.). Δ — к базе. Месяцы: Σ $ / сделок.\n" % (bS, bN, bS / bN, bS / rf.NDAYS),
           "| ось | клетка | сделок | Σ $ | ΔΣ | $/сделку | $/сут. | " + " | ".join(MON) + " |", "|---|---|---:|---:|---:|---:|---:|" + "---|" * len(MON)]
    for axis, cid, req, st in res:
        if st is None:
            out.append("| %s | %s | нет данных | | | | | %s |" % (axis, cid or "(база)", " | ".join("" for _ in MON)))
            continue
        pnl, mon = st
        cm = ["%+.1f / %d" % (pnl[mon == m].sum(), int((mon == m).sum())) for m in MON]
        out.append("| %s | %s | %d | %+.2f | %+.2f | %+.3f | %+.2f | %s |" % (axis, cid or "(база)", len(pnl), pnl.sum(), pnl.sum() - bS, pnl.mean(), pnl.sum() / rf.NDAYS, " | ".join(cm)))
    open(outp, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("cells", len(res), "written", outp)


if __name__ == "__main__":
    main(*sys.argv[1:4], *(sys.argv[4:5] or ["S"]))
