#!/usr/bin/env python3
"""TK-515 G-L1b: сверка fills/exits ядра на полной книге и на L1-файле (оба прогона с P40_L1=1 P40_L1_STAT=1).
Запуск: python3 l1_diff.py <префикс_полной> <префикс_L1>   (префикс — выход ядра: <префикс>.fills.csv, .exits.csv, .l1leave.csv, .l1leave_exits.csv)
Строки двух прогонов совпадают по номеру (один и тот же ряд эпизодов — проход 1 sha равен). Каждая отличающаяся строка получает класс:
  L1-очередь — у заявки (входа или выхода) цена уходила из L1 в одном из прогонов (правило §2.3: очередь заморожена, возврат по видимому размеру);
  ИНОЕ — всё остальное (допускается 0). Печать: таблица расхождений, доля заявок с уходом из L1, сдвиг net_bps (pnl_bps в exits.csv — уже чистый от комиссий)."""
import csv
import sys


def rd(p):
    with open(p, newline="") as f:
        return list(csv.reader(f))


def main(fp, lp):
    fa, fb = rd(fp + ".fills.csv"), rd(lp + ".fills.csv")
    ea, eb = rd(fp + ".exits.csv"), rd(lp + ".exits.csv")
    la, lb = rd(fp + ".l1leave.csv"), rd(lp + ".l1leave.csv")
    xa, xb = rd(fp + ".l1leave_exits.csv"), rd(lp + ".l1leave_exits.csv")
    assert len(fa) == len(fb) == len(la) == len(lb) and len(ea) == len(eb) == len(xa) == len(xb), "разное число строк"
    out = []
    # --- fills
    nd = {"L1-очередь": 0, "ИНОЕ": 0}
    for i in range(1, len(fa)):
        if fa[i] != fb[i]:
            left = "1" in (la[i][3], la[i][4], lb[i][3], lb[i][4])
            c = "L1-очередь" if left else "ИНОЕ"
            nd[c] += 1
            out.append(("fills", i, c, ",".join(fa[i][:3]), ",".join(fa[i][6:]), ",".join(fb[i][6:])))
    print("fills: строк %d, расходятся %d (L1-очередь %d, ИНОЕ %d)" % (len(fa) - 1, sum(nd.values()), nd["L1-очередь"], nd["ИНОЕ"]))
    # --- exits
    ne = {"L1-очередь": 0, "ИНОЕ": 0}
    for i in range(1, len(ea)):
        if ea[i] != eb[i]:
            left = "1" in (la[i][3], la[i][4], lb[i][3], lb[i][4], xa[i][2], xb[i][2])
            c = "L1-очередь" if left else "ИНОЕ"
            ne[c] += 1
            out.append(("exits", i, c, ",".join(ea[i][:3]), ",".join(ea[i][3:]), ",".join(eb[i][3:])))
    print("exits: строк %d, расходятся %d (L1-очередь %d, ИНОЕ %d)" % (len(ea) - 1, sum(ne.values()), ne["L1-очередь"], ne["ИНОЕ"]))
    # --- доля заявок с уходом цены из L1 (по заявкам входа, обе ноги)
    for tag, L, X in (("полная книга", la, xa), ("L1-файл", lb, xb)):
        ord_, left = 0, 0
        for r in L[1:]:
            for v in r[3:5]:
                if v != "-1":
                    ord_ += 1
                    left += v == "1"
        xo = sum(1 for r in X[1:] if r[2] != "-1")
        xl = sum(1 for r in X[1:] if r[2] == "1")
        print("%s: заявок входа %d, цена уходила из L1 %d (%.2f%%); заявок выхода/страховки %d, уходила %d (%.2f%%)" % (tag, ord_, left, 100.0 * left / max(ord_, 1), xo, xl, 100.0 * xl / max(xo, 1)))
    # --- net_bps
    h = ea[0]
    k = h.index("pnl_bps")

    def vals(E):
        return [float(r[k]) for r in E[1:] if r[k] not in ("", "nan", "NaN", "inf", "-inf")]

    va, vb = vals(ea), vals(eb)
    pairs = [(float(a[k]), float(b[k])) for a, b in zip(ea[1:], eb[1:]) if a[k] not in ("", "nan", "NaN") and b[k] not in ("", "nan", "NaN")]
    ma, mb = sum(va) / max(len(va), 1), sum(vb) / max(len(vb), 1)
    print("net_bps: полная n=%d mean=%.4f; L1 n=%d mean=%.4f; сдвиг L1−полная = %.4f bps (общих эпизодов %d, средний сдвиг по ним %.4f)" % (
        len(va), ma, len(vb), mb, mb - ma, len(pairs), sum(b - a for a, b in pairs) / max(len(pairs), 1)))
    print("--- расходящиеся строки (первые 40) ---")
    for r in out[:40]:
        print(" | ".join(str(x) for x in r))
    print("ИТОГО иных классов: %d" % (nd["ИНОЕ"] + ne["ИНОЕ"]))
    return nd["ИНОЕ"] + ne["ИНОЕ"]


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1], sys.argv[2]) else 0)
