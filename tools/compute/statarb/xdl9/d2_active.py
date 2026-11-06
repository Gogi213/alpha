#!/usr/bin/env python3
"""TK-493 S2 (numpy; Steam Deck, ~/xdl9/venv): активные пары-сутки D2 ПО ПАНЕЛЯМ (П-37 r3.2 §10.1, D5 iii, аудит M3-01 / M-07 / P-02).

Зачем. Правило funding решает вход по кольцу из k выплат ряда d и z не использует, поэтому колонка `active` ядра (z определён) у D2 меряет «60 минут подряд без дыр
у обеих ног», а не «ядро могло открыть сделку»: у разреженных бирж (Kraken, Aster, Bitget) она занижена в разы, и значение D2 на активную пару завышается.
Определение (D5 iii): СУТКИ ПАРЫ АКТИВНЫ, если (а) open ОБЕИХ ног конечен хотя бы в одну минуту суток И (б) к концу суток накоплено >= k выплат ряда d
(меток Y, у которых минута < конец суток). Знаменатель D2 в σ_форм и в отчёте F8 — число таких пар-суток; числитель — сделки тех же пар.
Функции: pair_active (одна пара), group_active (группа клетки: манифест d2_diff_funding.py + k). CLI — для торгового окна/формирования по готовому манифесту.
Торгового окна здесь не читают сами функции: какие панели и сутки подставить, решает вызывающий (формирование: ~/xdl9/form, торговля: ~/xdl9/trade).
"""
import csv, gzip, os, sys
import numpy as np

GRID0_MS = 1_577_836_800_000
MIN_DAY = 1440


def read_open(path):
    """-> (first_min, open[n]) из .sap (SAP1, u32 резерв, i64 first_min, u64 n, open[n], close[n])."""
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first_min = int(np.frombuffer(h[8:16], dtype="<i8")[0])
    n = int(np.frombuffer(h[16:24], dtype="<u8")[0])
    return first_min, np.fromfile(path, dtype="<f8", count=n, offset=24)


def label_minutes(series_path):
    """Минуты меток ряда d (выплаты Y) из файла time_ms,rate,interval_h."""
    out = []
    with gzip.open(series_path, "rt", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd, None)
        for r in rd:
            out.append((int(r[0]) - GRID0_MS) // 60_000)
    out.sort()
    return np.asarray(out, dtype=np.int64)


def pair_active(path_y, path_x, series_path, k, day0, day1):
    """-> [0/1] по суткам day0..day1-1 (сутки от 2020-01-01 UTC)."""
    fy, oy = read_open(path_y)
    fx, ox = read_open(path_x)
    marks = label_minutes(series_path)
    out = []
    for d in range(day0, day1):
        a, b = d * MIN_DAY, (d + 1) * MIN_DAY
        okc = False
        lo, hi = max(a, fy, fx), min(b, fy + len(oy), fx + len(ox))
        if hi > lo:
            okc = bool(np.any(np.isfinite(oy[lo - fy:hi - fy]) & np.isfinite(ox[lo - fx:hi - fx])))
        out.append(int(okc and int(np.searchsorted(marks, b, side="left")) >= k))
    return out


def group_active(panels_y, panels_x, group_dir, k, day0, day1):
    """Группа клетки (Y-биржа, X-биржа): манифест group_dir/manifest.csv (d2_diff_funding.py) -> [(y, x, [активность по суткам])] для пар status = ok."""
    res = []
    with open(os.path.join(group_dir, "manifest.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["status"] != "ok":
                continue
            ser = os.path.join(group_dir, "shard%s" % r["shard"], r["y"] + ".csv.gz")
            res.append((r["y"], r["x"], pair_active(os.path.join(panels_y, r["y"] + ".sap"), os.path.join(panels_x, r["x"] + ".sap"), ser, k, day0, day1)))
    return res


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) != 7:
        raise SystemExit("d2_active.py <панели Y> <панели X> <каталог группы d2form/…> <k> <day0> <day1> <out.csv>")
    rows = group_active(a[0], a[1], a[2], int(a[3]), int(a[4]), int(a[5]))
    with open(a[6], "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["y", "x", "day", "active"])
        for y, x, act in rows:
            for d, v in zip(range(int(a[4]), int(a[5])), act):
                w.writerow([y, x, d, v])
    print("пар", len(rows))
