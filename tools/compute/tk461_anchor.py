#!/usr/bin/env python3
"""TK-461 / P-TK461 §9 п.3: якорь — эталонный tk461_sim.trade против trade TK-455 на ТЕХ ЖЕ реальных входах (E0, форма A и B, S1/S2, T-S/T-R).
python tk461_anchor.py <csv.gz минуты> <tf> <k> <funding_cache> <out.json> [--grid]
Подмена sancho_l3_sim.trade в sancho_l3_trades на обёртку: считает оба, сверяет причину, минуту и цену выхода; результат TK-455 возвращает как есть.
E1 не сверяется (исполнение лимита вне ядра выходов TK-461). --grid — вся сетка E0 (S1/S2 × T-S/T-R × N), иначе якорные ячейки."""
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sancho_l3_trades as T  # noqa: E402
import tk461_sim as S  # noqa: E402

CNT = dict(compared=0, mismatch=0, e1_skipped=0)
BAD = []
MAP = {T.STOP: S.STOP, T.TARGET: S.TARGET, T.DEADLINE: S.DEADLINE}
orig = T.trade


def wrapped(o, h, l, c, e, last, entry_px, stop, tgt, e1=False):
    r = orig(o, h, l, c, e, last, entry_px, stop, tgt, e1=e1)
    if e1:
        CNT["e1_skipped"] += 1
        return r
    legs, m, reason, _ = S.trade(o, h, l, c, e, last, entry_px, stop, tgt)
    px = legs[-1][1]
    CNT["compared"] += 1
    if not (MAP[r[0]] == reason and r[1] == m and abs(r[2] - px) <= 1e-9 * max(1.0, abs(px))):
        CNT["mismatch"] += 1
        if len(BAD) < 10:
            BAD.append(dict(e=int(e), last=int(last), t455=[int(r[0]), int(r[1]), float(r[2])], t461=[reason, int(m), float(px)]))
    return r


T.trade = wrapped
if __name__ == "__main__":
    path, tf, k, fc, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
    T.run(path, tf, k, fc, "--grid" not in sys.argv)
    json.dump(dict(coin=os.path.basename(path), tf=tf, **CNT, bad=BAD), open(out, "w"))
    print(CNT)
