#!/usr/bin/env python3
"""П-42 §13.16: калибровка окна W цены-заменителя (последняя сделка стороны ≤ T) против настоящего bookTicker на 13.11 UTC.

  entrycal.py list <каталог sigs> <файл для awk>   — сигналы 13.11 UTC варианта b0 → «символ<TAB>T_ord» (T_ord = sig_ms + lat + T0)
  entrycal.py calc <каталог sigs> <каталог результата awk> [<json>] — таблица по W, W* по правилу §13.16, описательная сверка (Б)

`<каталог sigs>` — результат `P42_SIGS=73:73 breakout tick` (вариант b0): `<side>-sigs.csv`
(sym,level,sig_ms,level_px_e8,proxy_px_e8,proxy_age_ms,old_entry_ms,old_entry_px_e8). Медианы не пишем.
"""
import csv
import glob
import json
import os
import sys

T0 = 1_788_220_800_000
DAY = 86_400_000
LAT = 4
D13 = 73  # 13.11 = сутки 73 от 01.09
GRID = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
COVER_MS = 10_000  # запись книги не старше 10 с до T
NOTIONAL = 500.0


def sigs(d):
    out = []
    for side in ("long", "short"):
        p = os.path.join(d, f"{side}-sigs.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            ms = int(r["sig_ms"])
            if ms // DAY != D13:
                continue
            r["side"] = side
            r["sig_ms"] = ms
            r["t_ord"] = T0 + ms + LAT
            out.append(r)
    return out


def p90(v):
    s = sorted(v)
    return s[min(len(s) - 1, int(0.9 * len(s)))] if s else float("nan")


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def cmd_list(d, out):
    rows = sigs(d)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"{r['sym']}\t{r['t_ord']}\n")
    print(f"сигналов 13.11 UTC: {len(rows)} (long {sum(r['side']=='long' for r in rows)}, short {sum(r['side']=='short' for r in rows)})")


def load_book(d):
    best = {}
    for p in glob.glob(os.path.join(d, "*.txt")):
        if p.endswith("files.lst"):
            continue
        for line in open(p, encoding="utf-8"):
            a = line.rstrip("\n").split("\t")
            if len(a) < 7:
                continue
            k = (a[0], int(a[1]))
            tb = int(a[2])
            if k not in best or tb >= best[k][0]:
                best[k] = (tb, float(a[3]), float(a[4]), float(a[5]), float(a[6]))
    return best


def cmd_calc(d, bd, jout=None):
    rows = sigs(d)
    book = load_book(bd)
    cal = []
    for r in rows:
        b = book.get((r["sym"], r["t_ord"]))
        if not b or r["t_ord"] - b[0] > COVER_MS:
            continue
        s = 1 if r["side"] == "long" else -1
        tb, bid, bq, ask, aq = b
        real, rq = (ask, aq) if s > 0 else (bid, bq)
        r["real"], r["real_q"], r["t_book"] = real, rq, tb
        r["lvl"] = int(r["level_px_e8"]) / 1e8
        r["proxy"] = int(r["proxy_px_e8"]) / 1e8 if r["proxy_px_e8"] else None
        r["age"] = int(r["proxy_age_ms"]) if r["proxy_age_ms"] else None
        r["old"] = int(r["old_entry_px_e8"]) / 1e8 if r["old_entry_px_e8"] else None
        r["old_ms"] = int(r["old_entry_ms"]) if r["old_entry_ms"] else None
        r["s"] = s
        cal.append(r)
    res = {"signals_13_11": len(rows), "covered": len(cal), "excluded": len(rows) - len(cal)}
    res["covered_long"] = sum(r["side"] == "long" for r in cal)
    res["covered_short"] = sum(r["side"] == "short" for r in cal)
    table = []
    for w in GRID:
        e = []
        for r in cal:
            if r["proxy"] is None or r["age"] > w:
                continue
            e.append(r["s"] * (r["proxy"] - r["real"]) / r["real"] * 1e4)
        ab = [abs(x) for x in e]
        table.append(
            {
                "W": w,
                "n": len(e),
                "coverage": len(e) / len(cal) if cal else float("nan"),
                "mean_e": mean(e),
                "mean_abs": mean(ab),
                "p90_abs": p90(ab),
                "max_abs": max(ab) if ab else float("nan"),
            }
        )
    ok = [t for t in table if t["n"] > 0 and t["mean_abs"] <= 1.0]
    wstar = max(t["W"] for t in ok) if ok else GRID[0]
    res["table"] = table
    res["W_star"] = wstar
    res["rule"] = "largest W with mean|e| <= 1.0 bp (else smallest)"
    # описательная сверка Б при W*
    d_old = [r["s"] * (r["old"] - r["real"]) / r["real"] * 1e4 for r in cal if r["old"] is not None]
    d_prx = [
        r["s"] * (r["proxy"] - r["real"]) / r["real"] * 1e4
        for r in cal
        if r["proxy"] is not None and r["age"] <= wstar
    ]
    delay = [r["old_ms"] - r["sig_ms"] for r in cal if r["old_ms"] is not None]
    conf = {"TP": 0, "FN_missed": 0, "FP": 0, "TN": 0, "no_proxy_truth_beyond": 0, "no_proxy_truth_inside": 0}
    for r in cal:
        truth = r["s"] * (r["real"] - r["lvl"]) > 0
        has = r["proxy"] is not None and r["age"] <= wstar
        if not has:
            conf["no_proxy_truth_beyond" if truth else "no_proxy_truth_inside"] += 1
            continue
        pred = r["s"] * (r["proxy"] - r["lvl"]) > 0
        conf["TP" if truth and pred else "FN_missed" if truth else "FP" if pred else "TN"] += 1
    res["B"] = {
        "old_vs_book_bp": {"n": len(d_old), "mean": mean(d_old), "mean_abs": mean([abs(x) for x in d_old]), "p90_abs": p90([abs(x) for x in d_old])},
        "proxy_vs_book_bp": {"n": len(d_prx), "mean": mean(d_prx), "mean_abs": mean([abs(x) for x in d_prx]), "p90_abs": p90([abs(x) for x in d_prx])},
        "old_delay_ms": {"n": len(delay), "mean": mean(delay), "p90": p90(delay), "max": max(delay) if delay else None, "over_1s": sum(x > 1000 for x in delay)},
        "beyond_confusion_at_Wstar": conf,
        "book_beyond_level_share": sum(r["s"] * (r["real"] - r["lvl"]) > 0 for r in cal) / len(cal) if cal else None,
        "old_entry_beyond_level_n": sum(1 for r in cal if r["old"] is not None and r["s"] * (r["old"] - r["lvl"]) > 0),
        "l1_notional_below_500_share": sum(r["real"] * r["real_q"] < NOTIONAL for r in cal) / len(cal) if cal else None,
    }
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if jout:
        json.dump(res, open(jout, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "list":
        cmd_list(sys.argv[2], sys.argv[3])
    elif c == "calc":
        cmd_calc(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        sys.exit(__doc__)
