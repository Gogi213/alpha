"""Полнота входных книг: минуты со сменой лучшей цены по независимой выгрузке проверяющего (/root/rev534/my_books.pkl — все строки `b` 22 монет, mawk, сортировка по E) против разобранных кусков (b2 — v3, b3 — v4).

Минута «сырья» = минута, где у символа есть запись, у которой (bid, ask) отличается от предыдущей записи по E. Минута «разобранных» = минута, где есть кусок. Доля пропущенных = минуты сырья без разобранных / минуты сырья (внутри окна v3).
Запуск: python3 hold_cover_check.py <книги b2/parsed> <книги b3/parsed> <t0_мс> <t1_мс> <выход.md>
"""
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import maker_run  # noqa: E402
from hold_common import DAYS, build_coins, unit_of  # noqa: E402


def minutes(e):
    return np.unique(e // 60000)


def main(p2, p3, t0, t1, out):
    B = pickle.load(open("/root/rev534/my_books.pkl", "rb"))["books"]
    coins, _ = {}, None
    c3, _ = build_coins(p3, DAYS)
    c2, _ = build_coins(p2, DAYS)
    rows = []
    for (c, ex), (e, bid, ask, _, _) in B.items():
        w = (e >= t0) & (e < t1)
        e, bid, ask = e[w], bid[w], ask[w]
        if not len(e):
            continue
        ch = np.r_[True, (bid[1:] != bid[:-1]) | (ask[1:] != ask[:-1])]
        rm = minutes(e[ch])
        r = {"coin": c, "ex": ex, "raw_min": len(rm)}
        for tag, cs, pp in (("b2", c2, p2), ("b3", c3, p3)):
            if c not in cs or ex not in cs[c]:
                r[tag] = None
                continue
            sym, mult, meta = cs[c][ex]
            a = maker_run.load_book(pp, ex, sym, mult, unit_of(ex, meta))
            if a is None:
                r[tag] = None
                continue
            ae = a["e"][(a["e"] >= t0) & (a["e"] < t1)]
            pm = minutes(ae)
            r[tag] = len(np.setdiff1d(rm, pm)) / max(len(rm), 1)
        rows.append(r)
    L = ["| монета | биржа | минут сырья | пропущено в b2 (v3), % | пропущено в b3 (v4), % |", "|---|---|---|---|---|"]
    tot = {"b2": [0, 0], "b3": [0, 0]}
    for r in sorted(rows, key=lambda r: -(r["b2"] or 0)):
        L.append(f"| {r['coin']} | {r['ex']} | {r['raw_min']} | {'' if r['b2'] is None else round(100 * r['b2'], 1)} | {'' if r['b3'] is None else round(100 * r['b3'], 1)} |")
        for tag in ("b2", "b3"):
            if r[tag] is not None:
                tot[tag][0] += r[tag] * r["raw_min"]
                tot[tag][1] += r["raw_min"]
    L.append("")
    L.append(f"Итого пропущено: b2 {100 * tot['b2'][0] / max(tot['b2'][1], 1):.2f} %, b3 {100 * tot['b3'][0] / max(tot['b3'][1], 1):.2f} % минут (символов: {len(rows)}; символов с > 5 %: b2 {sum(1 for r in rows if (r['b2'] or 0) > 0.05)}, b3 {sum(1 for r in rows if (r['b3'] or 0) > 0.05)}).")
    Path(out).write_text("\n".join(L), encoding="utf-8")
    print(L[-1])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
