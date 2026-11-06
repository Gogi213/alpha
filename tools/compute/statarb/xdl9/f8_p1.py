#!/usr/bin/env python3
"""TK-493 w5 F (П-37 r3.2 D11, аудит L6-01 / L6-02): панели «+1 минута на обеих ногах» для условия кандидата (ж) и замер «семантики open» по биржам.

Зачем. Ядро исполняет вход и выход по open минуты t+1 после сигнала по close минуты t. На биржах «цепочки» (Bybit, BingX, Bitget, Kraken, Lighter)
open[t+1] = close[t]: исполнение по цене последней сделки сигнальной минуты, то есть до реакции рынка на сам сигнал, - не граница исполнимого. Поэтому
условие (ж): net остаётся > 0 при задержке на ещё одну минуту на ОБЕИХ ногах. Задержка +1 минута реализована ПАНЕЛЯМИ, без правки ядра:

    open'[i] = open[i+1]     (последняя минута панели - NaN; close не меняется)

Сигналы (z, окно, β) строятся по close и не меняются; цена исполнения на минуте i+1 становится open[i+2]. Минута, после которой следующая отсутствует
(NaN), получает NaN - ядро отложит исполнение так же, как при дыре в данных. C1 с `--delay-x 1` получает на ноге X суммарно +2 минуты (+1 штатная задержка
и +1 панелью), на ноге Y +1: требование (ж) - +1 на обеих ногах относительно основного прогона клетки.

  python f8_p1.py mk --needs p1_needs.tsv --src ~/xdl9/trade --out ~/xdl9/trade1 [--lo-day D --hi-day D]
      needs - tsv `exchange<TAB>symbol` (только символы заданий торгуемых клеток); пишет `<out>/<биржа>/<SYM>.sap`. Читает всю панель символа целиком:
      на боевом запуске --src - панели торгового окна (только по команде CEO); панели формирования получаются через --src ~/xdl9/form.
  python f8_p1.py semantics --src ~/xdl9/form --out semantics.csv
      Измерение (не назначение): по биржам доля пар соседних минут, где обе цены конечны и open[t+1] == close[t] точно («цепочка»). Это обоснование
      колонки «семантика open» отчёта (cell_status.OPEN_SEMANTICS задаёт таблицу по аудиту L6-02; здесь она сверяется с данными).
  python f8_p1.py selftest
"""
import csv, os, struct, sys

import numpy as np

MIN_DAY = 1440


def read_sap(path):
    with open(path, "rb") as f:
        h = f.read(24)
    if h[:4] != b"SAP1":
        raise ValueError("не SAP1: " + path)
    first_min = int(np.frombuffer(h[8:16], dtype="<i8")[0])
    n = int(np.frombuffer(h[16:24], dtype="<u8")[0])
    a = np.fromfile(path, dtype="<f8", count=2 * n, offset=24)
    if len(a) != 2 * n:
        raise ValueError("обрезанная панель: " + path)
    return first_min, a[:n].copy(), a[n:].copy()


def write_sap(path, first_min, op, cl):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first_min, len(cl)))
        np.ascontiguousarray(op, dtype="<f8").tofile(f)
        np.ascontiguousarray(cl, dtype="<f8").tofile(f)
    os.replace(tmp, path)


def shift_open(op):
    """open'[i] = open[i+1], последний = NaN."""
    out = np.empty_like(op)
    out[:-1] = op[1:]
    out[-1:] = np.nan
    return out


def read_needs(path):
    out = set()
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if row and row[0] != "exchange":
                out.add((row[0], row[1]))
    return sorted(out)


def cmd_mk(a):
    needs = read_needs(a["needs"])
    n = miss = 0
    for ex, sym in needs:
        sp = os.path.join(a["src"], ex, sym + ".sap")
        if not os.path.exists(sp):
            miss += 1
            continue
        fm, op, cl = read_sap(os.path.realpath(sp))
        d = os.path.join(a["out"], ex)
        os.makedirs(d, exist_ok=True)
        write_sap(os.path.join(d, sym + ".sap"), fm, shift_open(op), cl)
        n += 1
    print("p1 mk: панелей %d, нет панели-источника %d, из %d символов" % (n, miss, len(needs)))


def cmd_semantics(a):
    rows = []
    for ex in sorted(os.listdir(a["src"])):
        d = os.path.join(a["src"], ex)
        if not os.path.isdir(d):
            continue
        pairs = chain = files = 0
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".sap"):
                continue
            _, op, cl = read_sap(os.path.realpath(os.path.join(d, fn)))
            ok = np.isfinite(op[1:]) & np.isfinite(cl[:-1])
            pairs += int(ok.sum())
            chain += int((ok & (op[1:] == cl[:-1])).sum())
            files += 1
        rows.append((ex, files, pairs, chain, (chain / pairs) if pairs else float("nan")))
    with open(a["out"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("exchange,symbols,minute_pairs,open_equals_prev_close,share\n")
        for r in rows:
            fh.write("%s,%d,%d,%d,%.6f\n" % r)
    for r in rows:
        print("%-8s символов %4d, пар минут %9d, open[t+1]==close[t] %.4f" % (r[0], r[1], r[2], r[4]))


def selftest():
    import tempfile
    op = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
    s = shift_open(op)
    assert s[0] == 2.0 and np.isnan(s[1]) and s[2] == 4.0 and s[3] == 5.0 and np.isnan(s[4])
    cl = np.array([1.5, 2.5, 3.5, 4.5, 5.5])
    with tempfile.TemporaryDirectory() as td:
        sd, od = os.path.join(td, "src", "ex"), os.path.join(td, "out")
        os.makedirs(sd)
        write_sap(os.path.join(sd, "A.sap"), 123456, op, cl)
        nd = os.path.join(td, "needs.tsv")
        open(nd, "w", encoding="utf-8", newline="\n").write("exchange\tsymbol\nex\tA\nex\tB\n")
        cmd_mk(dict(needs=nd, src=os.path.join(td, "src"), out=od))
        fm, o2, c2 = read_sap(os.path.join(od, "ex", "A.sap"))
        assert fm == 123456 and np.array_equal(c2, cl) and np.array_equal(o2[:-1], op[1:], equal_nan=True) and np.isnan(o2[-1])
        assert not os.path.exists(os.path.join(od, "ex", "B.sap"))
    # семантика: цепочка там, где open[t+1] == close[t]
    assert float((np.array([2.0, 3.0]) == np.array([2.0, 9.0])).mean()) == 0.5
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "selftest":
        selftest()
    else:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd", choices=["mk", "semantics"])
        ap.add_argument("--needs")
        ap.add_argument("--src", required=True)
        ap.add_argument("--out", required=True)
        a = vars(ap.parse_args())
        {"mk": cmd_mk, "semantics": cmd_semantics}[a["cmd"]](a)
