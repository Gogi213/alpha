#!/usr/bin/env python3
"""TK-065: книга по бинлогу v3 в окне сделки — сверка момента выхода (converge / wall_gone) с лентой.
python3 tk065-tape.py <binlog> <level_tick> <t0_ns> <exit_ns> [--a-ticks N] [--tail-s S]
Цена в бинлоге — целое число тиков; qty — дельтами (бегущая сумма внутри кадра). Время — local_ts."""
import argparse, struct, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("path")
ap.add_argument("level", type=int)
ap.add_argument("t0", type=int)
ap.add_argument("exit", type=int)
ap.add_argument("--a-ticks", type=int, default=8)
ap.add_argument("--dbg", action="store_true")
ap.add_argument("--tail-s", type=float, default=5.0)
a = ap.parse_args()


def uvar(b, p):
    r = s = 0
    while True:
        c = b[p]; p += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, p
        s += 7


def zz(b, p):
    v, p = uvar(b, p)
    return (v >> 1) ^ -(v & 1), p


f = open(a.path, "rb")
hdr = f.read(25)
bid, ask = {}, {}
end = a.exit + int(a.tail_s * 1e9)
series = []  # (local_ts, best_bid, best_ask, qty_at_level_bid)
cur = (None, None, None)
bb = ba = None
nfr = 0
dbg = [] if a.dbg else None
kinds = {}
first = None
done = False
while not done:
    h = f.read(4)
    if len(h) < 4:
        break
    n = struct.unpack("<I", h)[0]
    raw = f.read(n)
    pl = subprocess.run(["zstd", "-d", "-c"], input=raw, capture_output=True).stdout
    nfr += 1
    epoch = struct.unpack("<q", pl[:8])[0]
    p, px, qt = 8, 0, 0
    while p < len(pl):
        ev, p = uvar(pl, p)
        _ex, p = zz(pl, p)
        lo, p = zz(pl, p)
        _at, p = uvar(pl, p)
        cnt, p = uvar(pl, p)
        lts = epoch + lo
        kind = ev & 0xFF
        for _ in range(cnt):
            dp, p = zz(pl, p)
            dq, p = zz(pl, p)
            px += dp
            qt += dq
            if kind == 1 or kind == 4:
                side = bid if ev & (1 << 29) else ask if ev & (1 << 28) else None
                if side is None:
                    continue
                isbid = side is bid
                if qt <= 0:
                    side.pop(px, None)
                    if isbid and px == bb:
                        bb = max((k for k in bid if k <= px), default=None)
                    elif not isbid and px == ba:
                        ba = min((k for k in ask if k >= px), default=None)
                else:
                    side[px] = qt
                    # как fast_depth.rs: пересечение сдвигает противоположный указатель, а не удаляет уровни
                    if isbid and (bb is None or px > bb):
                        bb = px
                        if ba is not None and bb >= ba:
                            ba = min((k for k in ask if k > bb), default=None)
                    elif not isbid and (ba is None or px < ba):
                        ba = px
                        if bb is not None and ba <= bb:
                            bb = max((k for k in bid if k < ba), default=None)
        if kind == 3:  # DEPTH_CLEAR: как clear_depth в бэктесте — бид: цены >= px, аск: цены <= px
            if dbg is not None and len(dbg) < 12:
                dbg.append((lts - a.t0, "bid" if ev & (1 << 29) else "ask", px, cnt, len(bid), len(ask)))
            if ev & (1 << 29):
                for k in [k for k in bid if k >= px]:
                    del bid[k]
                bb = max(bid, default=None)
            else:
                for k in [k for k in ask if k <= px]:
                    del ask[k]
                ba = min(ask, default=None)
        kinds[kind] = kinds.get(kind, 0) + 1
        if lts >= a.t0 - 120 * 10**9 and bb is not None and ba is not None:
            now = (bb, ba, bid.get(a.level, 0))
            if now != cur:
                cur = now
                series.append((lts, *now))
        if lts > end:
            done = True
            break

if a.dbg:
    print("kinds", kinds, "clears(t-t0,side,px,cnt,nbid,nask)", dbg)
print(f"кадров прочитано {nfr}; точек {len(series)}; level={a.level} A={a.a_ticks} тиков")
dep = None
exp_ns = None
mx = (-10**9, None)
for ts, bb, ba, q in series:
    if ts < a.t0 or ts > a.exit + 10**9:
        continue
    d = bb - a.level
    if d > mx[0]:
        mx = (d, ts)
    if dep is None and d >= a.a_ticks:
        dep = ts
    if dep is not None and exp_ns is None and d <= 1:
        exp_ns = ts
print(f"макс. отход best_bid от уровня: {mx[0]} тиков в t0+{(mx[1]-a.t0)/1e9 if mx[1] else 0:.3f}c")
print("первое d>=A: " + (f"t0+{(dep-a.t0)/1e9:.3f}c" if dep else "нет"))
print("после него первое d<=1 (ожидаемый converge): " + (f"t0+{(exp_ns-a.t0)/1e9:.6f}c, разница с exit_ns {(a.exit-exp_ns)/1e6:.3f} мс" if exp_ns else "нет"))
print(f"exit_ns = t0+{(a.exit-a.t0)/1e9:.6f}c; ряд вокруг выхода (t-exit мс, best_bid-level, best_ask-level, qty@level):")
for ts, bb, ba, q in series:
    if a.exit - int(a.tail_s * 1e9) <= ts <= a.exit + 10**9:
        print(f"  {(ts-a.exit)/1e6:12.3f}  {bb-a.level:4d}  {ba-a.level:4d}  {q}")
print("переходы d=best_bid-level (t-t0 с): смена состояния hi(d>=A) / lo(d<=1) / mid:")
st = None
for ts, bb, ba, q in series:
    if ts < a.t0 or ts > a.exit + 10**9:
        continue
    d = bb - a.level
    k = "hi" if d >= a.a_ticks else "lo" if d <= 1 else "mid"
    if k != st:
        st = k
        print(f"  t0+{(ts-a.t0)/1e9:9.3f}  d={d:3d}  {k}")
print("qty@level: перед выходом (последние значения по возрастанию времени, до 12 смен):")
ql = [(ts, q) for ts, bb, ba, q in series if ts <= a.exit]
prev = None
out = []
for ts, q in ql:
    if q != prev:
        out.append((ts, q)); prev = q
for ts, q in out[-12:]:
    print(f"  {(ts-a.exit)/1e6:12.3f} мс  {q}")
