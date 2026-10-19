#!/usr/bin/env python3
"""TK-312: сверка нарушений verify (сделка внутри диапазона книги на цене, которую книга ни разу не держала) с СЫРЫМ ob200 спота.

Независимая от Rust реализация: SYM DAY [N образцов] → по сырым JSON-строкам ob (snapshot/delta) и CSV сделок.
Нарушение = цена сделки в [min, max] уровней книги на момент сделки и ни разу не стояла в книге с начала суток.
Как у verify, ever_held копится с начала МЕСЯЦА (по предыдущим суткам того же месяца), не только суток.
Образцы: по каждой — встречается ли цена в сырых сообщениях ob вообще (в любое время суток, qty>0) и в ±2 с вокруг сделки.
"""
import gzip, json, sys, zipfile

SRC = "/srv/alpha-sda/tk305/spot"
sym, day = sys.argv[1], sys.argv[2]
nsamp = int(sys.argv[3]) if len(sys.argv) > 3 else 20
carry = len(sys.argv) > 4 and sys.argv[4] == "carry"

def load_msgs(dy):  # [(ts, [(side, price, qty)], is_snapshot)]
    out = []
    with zipfile.ZipFile(f"{SRC}/ob/{sym}/{dy}_{sym}_ob200.data.zip") as z:
        for name in z.namelist():
            for line in z.open(name):
                m = json.loads(line)
                d = m["data"]
                ups = [("b", p, q) for p, q in d["b"]] + [("a", p, q) for p, q in d["a"]]
                out.append((m["ts"], ups, m["type"] == "snapshot"))
    out.sort(key=lambda x: x[0])
    return out


msgs = load_msgs(day)

trades = []
with gzip.open(f"{SRC}/tr/{sym}/{sym}_{day}.csv.gz", "rt") as f:
    next(f)
    for ln in f:
        i, ts, p, v, side, rpi = ln.rstrip("\n").split(",")
        trades.append((int(ts), p, side, rpi))

def norm(p):  # цена → float с округлением к 1e-9
    return round(float(p), 9)

appear = {}  # цена -> список ts сообщений, где qty>0
for ts, ups, _ in msgs:
    for _, p, q in ups:
        if float(q) > 0:
            appear.setdefault(norm(p), []).append(ts)

book = {"b": {}, "a": {}}
held = set()
if carry:
    ym, dd = day[:8], int(day[8:])
    for d0 in range(1, dd):
        try:
            for _, ups, _ in load_msgs(f"{ym}{d0:02d}"):
                for _, p, q in ups:
                    if float(q) > 0:
                        held.add(norm(p))
        except (KeyError, FileNotFoundError):
            pass
    print(f"ever_held из {dd - 1} предыдущих суток месяца: {len(held)} цен")
mi = 0
viol = []
n_in_range = n_empty = n_out = 0
for ts, p, side, rpi in trades:
    while mi < len(msgs) and msgs[mi][0] <= ts:
        _, ups, snap = msgs[mi]
        if snap:
            book = {"b": {}, "a": {}}
        for s, pp, q in ups:
            k = norm(pp)
            if float(q) > 0:
                book[s][k] = float(q)
                held.add(k)
            else:
                book[s].pop(k, None)
        mi += 1
    lv = list(book["b"]) + list(book["a"])
    if not lv:
        n_empty += 1
        continue
    pr = norm(p)
    if pr < min(lv) or pr > max(lv):
        n_out += 1
        continue
    n_in_range += 1
    if pr not in held:
        lvls = book["a"] if side == "buy" else book["b"]  # сторона, которую ела сделка
        dist = min(abs(l - pr) for l in lvls) if lvls else None
        viol.append((ts, pr, side, rpi, max(book["b"]) if book["b"] else None, min(book["a"]) if book["a"] else None, dist))

print(f"{sym} {day}: сообщений ob {len(msgs)}, сделок {len(trades)}, в диапазоне {n_in_range}, вне {n_out}, пустая книга {n_empty}")
print(f"нарушений (независимый счёт, held — см. carry): {len(viol)}")
step = max(1, len(viol) // nsamp)
sample = viol[::step][:nsamp]
never = near = 0
for ts, pr, side, rpi, bb, ba, dist in sample:
    ts_list = appear.get(pr, [])
    anywhere = len(ts_list) > 0
    around = any(abs(t - ts) <= 2000 for t in ts_list)
    never += not anywhere
    near += around
    print(f"  ts={ts} p={pr} {side} rpi={rpi} bid1={bb} ask1={ba} ближайший_уровень_стороны={dist} в_сыром_ob_за_сутки={anywhere} ±2с={around}")
print(f"из {len(sample)} образцов: цены нет в сыром ob за все сутки — {never}; есть в ±2 с — {near}")
