#!/usr/bin/env python3
"""TK-345: B1/П-17/П-18 (signal=approach): независимая верхняя граница n по кэшу подходов D20/<сутки>/approaches-*.csv.

tk345-n-bound.py <approaches-dir> <instruments.csv> <каталог-вывода-суток (out/<сутки>)> [<регулярка-имён-клеток>]
Фильтры клетки (min_age_secs, usd_min, behind_min, side=bid) берутся из шапки её forms.csv. По символам: (а) n_signals +
n_skipped == все подходы кэша (обе стороны); (б) n_signals <= число подходов, прошедших age/usd/behind/side (без h3 по касанию
и btc4h — они только режут дальше). Печатает нарушения и итог по клеткам.
HDR_ROOT=<каталог бинлогов>: тик и шаг лота — из заголовка бинлога суток (как у движка, `read_tick_step`), а не из instruments.csv
(в споте csv тик в 10 раз мельче заголовка — §10 аудита)."""
import csv, glob, os, re, struct, sys
adir, inst, odir = sys.argv[1:4]
pat = re.compile(sys.argv[4]) if len(sys.argv) > 4 else None
ins = {r["symbol"]: (float(r["tick_size"]), float(r["qty_step"])) for r in csv.DictReader(open(inst))}
hdr_root = os.environ.get("HDR_ROOT")
day = os.path.basename(os.path.normpath(odir))
def grid(sym):
    if hdr_root:
        fs = sorted(glob.glob(f"{hdr_root}/{sym}-{day}*.binlog"))
        if fs:
            b = open(fs[0], "rb").read(21)
            t, l = struct.unpack("<qq", b[5:21])
            return t / 1e9, l / 1e9
    return ins[sym]
cache = {}
def appr(sym):
    if sym not in cache:
        p = os.path.join(adir, f"approaches-{sym}.csv")
        cache[sym] = list(csv.DictReader(open(p))) if os.path.exists(p) else None
    return cache[sym]
def num(h, key):
    m = re.search(key + r"=(?:Some\()?(-?[0-9.]+)", h)
    return float(m.group(1)) if m else None
cells = bad = nocache_t = 0
for c in sorted(os.listdir(odir)):
    f = os.path.join(odir, c, "forms.csv")
    if not os.path.isfile(f) or (pat and not pat.search(c)):
        continue
    lines = open(f).read().splitlines()
    head = lines[0]
    age, usd, behind = num(head, "min_age_secs"), num(head, "usd_min"), num(head, "behind_min")
    rows = list(csv.DictReader(l for l in lines if not l.startswith("#")))
    cells += 1; viol = nocache = 0; ns = ub_t = 0
    for r in rows:
        a = appr(r["symbol"])
        if a is None:
            nocache += 1; continue
        tick, step = grid(r["symbol"])
        ub = 0
        for x in a:
            if x["side"] != "bid":
                continue
            if age is not None and int(x["age_ms"]) < age * 1000:
                continue
            if behind is not None and int(x["depth_behind_lots"]) * 100 < behind * int(x["size_at_arm"]):
                continue
            if usd is not None and int(x["price_tick"]) * tick * int(x["size_at_arm"]) * step < usd:
                continue
            ub += 1
        s, k = int(r["n_signals"]), int(r["n_skipped"])
        ns += s; ub_t += ub
        if s + k != len(a) or s > ub:
            viol += 1
            print(f"DIFF {c} {r['symbol']}: signals={s} skipped={k} подходов={len(a)} граница={ub}")
    bad += viol; nocache_t += nocache
    print(f"{c}: age={age} usd={usd} behind={behind} символов={len(rows)} без_кэша={nocache} нарушений={viol} Σn={ns} Σграница={ub_t}")
print(f"ИТОГО клеток {cells}, нарушений суммарно {bad}, строк без кэша подходов {nocache_t}")
