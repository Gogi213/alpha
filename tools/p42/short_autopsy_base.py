"""TK-618 шаг 1: склейка сделок базы (по зачётным диапазонам §13.24.4) в одну таблицу trades.csv: месяц, абсолютное время входа/выхода, $ сделки.
  python short_autopsy_base.py <каталог с short-trades-w{a,b,c}.csv> <выход.csv> [side]
Без P&L-решений: только склейка; Σ(gross−fees), число сделок печатаются для сверки с отчётом TK-599 (шорт: 431 / +259.14)."""
import csv
import datetime
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_full as rf  # noqa: E402


def spliced_rows(path_of):
    """path_of(окно) -> trades.csv окна; строки зачётных диапазонов, по времени входа (то же правило, что `retitr_full.splice`)."""
    rows = []
    for w in rf.WINDOWS:
        t = rf.read_trades_csv(path_of(w.name))
        raw = list(csv.DictReader(open(path_of(w.name), encoding="utf-8")))
        d = rf.abs_day(w, t.entry_ms)
        lo_i, hi_i = (w.lod - rf.D0).days, (w.hid - rf.D0).days
        t0 = rf._t0_ms(w)
        for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]:
            r = raw[i]
            ent = int(r["entry_ms"]) + t0
            ex = int(r["exit_ms"]) + t0
            dt = datetime.datetime.fromtimestamp(ent / 1000, datetime.timezone.utc)
            m = int(d[i]) // 1
            mon = rf.MONTH_NAMES[int(rf.MONTH_OF_DAY[int(d[i])])]
            rows.append(dict(win=w.name, sym=r["sym"], level=r["level"], kind=r["kind"], takes=r["takes"], entry_px_e8=r["entry_px_e8"],
                             gross=r["gross"], fees=r["fees"], funding=r["funding"], net=r["net"], pnl="%.6f" % (float(r["gross"]) - float(r["fees"])),
                             day=int(d[i]), month=mon, entry_ms=ent, exit_ms=ex, sig_ms=int(r["sig_ms"]) + t0,
                             hold_min="%.2f" % ((ex - ent) / 60000), date=dt.strftime("%Y-%m-%d"), hour_utc=dt.hour))
    rows.sort(key=lambda r: r["entry_ms"])
    return rows


def main(src, out, side="short"):
    rows = spliced_rows(lambda w: os.path.join(src, "%s-trades-%s.csv" % (side, w)))
    with open(out, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    s = sum(float(r["pnl"]) for r in rows)
    print("trades %d  sum(gross-fees) %+.2f" % (len(rows), s))
    for m in rf.MONTH_NAMES:
        x = [float(r["pnl"]) for r in rows if r["month"] == m]
        print(m, len(x), "%+.2f" % sum(x))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(sys.argv[3:4] or ["short"]))
