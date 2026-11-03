#!/usr/bin/env python3
"""TK-487: сверка панелей .sap с исходными свечами — независимо от Rust-конвертера (чистый python, без numpy).

  verify_panels.py SYM=панель.sap,свечи.csv[.gz] [SYM=…]   код 0 — расхождений 0, иначе 1

Правило (src/statarb/panel.rs convert_csv_stats): строка csv с объёмом ≠ 0 даёт open/close панели на своей минуте;
строка с объёмом 0 и минута без строки — NaN; панель начинается с первой и кончается последней строкой с объёмом ≠ 0.
Сравнение побитовое: python float() и Rust parse::<f64> округляют одинаково (ближайшее), значения должны совпасть ровно."""
import gzip
import math
import sys
from array import array

GRID0_MS = 1_577_836_800_000
MINUTE_MS = 60_000
HEADER = 24


def read_panel(path):
    with open(path, "rb") as f:
        h = f.read(HEADER)
        if len(h) < HEADER or h[:4] != b"SAP1":
            raise ValueError("%s: не панель SAP1" % path)
        first = int.from_bytes(h[8:16], "little", signed=True)
        n = int.from_bytes(h[16:24], "little")
        cols = []
        for _ in range(2):
            a = array("d")
            a.frombytes(f.read(8 * n))
            if len(a) != n:
                raise ValueError("%s: столбец короче заявленных %d минут" % (path, n))
            if sys.byteorder != "little":
                a.byteswap()
            cols.append(a)
        if f.read(1):
            raise ValueError("%s: лишние байты после столбцов" % path)
    return first, n, cols[0], cols[1]


def open_text(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def same(a, b):
    return (math.isnan(a) and math.isnan(b)) or a == b


def verify(sym, panel_path, csv_path):
    """→ (сводка dict, список первых расхождений)."""
    first, n, op, cl = read_panel(panel_path)
    covered = bytearray(n)
    diffs = []
    rows = zero = 0
    nz_min = nz_max = None
    with open_text(csv_path) as f:
        for i, line in enumerate(f):
            parts = line.strip().split(",")
            if len(parts) < 5:
                if line.strip():
                    diffs.append("строка %d: меньше 5 полей" % (i + 1))
                continue
            try:
                t = int(parts[0])
            except ValueError:
                if i == 0:
                    continue  # заголовок
                diffs.append("строка %d: время не число" % (i + 1))
                continue
            rows += 1
            idx = (t - GRID0_MS) // MINUTE_MS
            vol = float(parts[5]) if len(parts) > 5 and parts[5].strip() else None
            if vol == 0.0:
                zero += 1
                pos = idx - first
                if 0 <= pos < n:
                    covered[pos] = 1
                    if not (math.isnan(op[pos]) and math.isnan(cl[pos])):
                        diffs.append("мин %d: объём 0, в панели не NaN" % idx)
                continue
            nz_min = idx if nz_min is None else min(nz_min, idx)
            nz_max = idx if nz_max is None else max(nz_max, idx)
            pos = idx - first
            if not 0 <= pos < n:
                diffs.append("мин %d: вне спана панели" % idx)
                continue
            covered[pos] = 1
            if not (same(op[pos], float(parts[1])) and same(cl[pos], float(parts[4]))):
                diffs.append("мин %d: csv o=%s c=%s, панель o=%r c=%r" % (idx, parts[1], parts[4], op[pos], cl[pos]))
    if nz_min is None:
        if n:
            diffs.append("в csv нет строк с объёмом ≠ 0, а в панели %d минут" % n)
    else:
        if first != nz_min:
            diffs.append("первая минута: csv %d, панель %d" % (nz_min, first))
        if first + n - 1 != nz_max:
            diffs.append("последняя минута: csv %d, панель %d" % (nz_max, first + n - 1))
    for pos in range(n):
        if not covered[pos] and not (math.isnan(op[pos]) and math.isnan(cl[pos])):
            diffs.append("мин %d: строки csv нет, в панели не NaN" % (first + pos))
    nan = sum(1 for v in cl if v != v)
    return {"sym": sym, "csv_rows": rows, "zero_volume": zero, "panel_minutes": n, "panel_nan": nan,
            "diffs": len(diffs)}, diffs[:5]


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for item in argv:
        sym, _, rest = item.partition("=")
        panel, _, src = rest.partition(",")
        if not (sym and panel and src):
            print("ожидается SYM=панель.sap,свечи.csv[.gz]: %r" % item, file=sys.stderr)
            return 2
        s, diffs = verify(sym, panel, src)
        print("verify %(sym)s: строк csv %(csv_rows)d, объём 0 → NaN %(zero_volume)d, минут в панели %(panel_minutes)d, "
              "NaN в панели %(panel_nan)d, расхождений %(diffs)d" % s)
        for d in diffs:
            print("  " + d)
        bad += s["diffs"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
