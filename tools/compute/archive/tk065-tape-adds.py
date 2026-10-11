#!/usr/bin/env python3
"""TK-065 G3б: qty стены на level_px по бинлогу v3 у момента каждой добавки pyre (reinstall_add из R2-трассы).
python3 tk065-tape-adds.py <binlog> <level_tick> <floor_qty> <lookback_s> <add_ns>...
Читатель кадров — как tk065-tape.py (qty дельтами, бегущая сумма внутри кадра). Печать: пересечения порога floor на level
(вниз/вверх) в окне [первая добавка − lookback, последняя + 1 с] и состояние перед каждой добавкой."""
import struct, subprocess, sys

path, level, floor, lb = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
adds = sorted(int(x) for x in sys.argv[5:])
lo_ns, hi_ns = adds[0] - int(lb * 1e9), adds[-1] + 10**9


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


f = open(path, "rb"); f.read(25)
q = 0.0          # текущий qty на level (бид)
ev_log = []      # (ts, qty) при каждой смене qty@level
done = False
while not done:
    h = f.read(4)
    if len(h) < 4:
        break
    raw = f.read(struct.unpack("<I", h)[0])
    pl = subprocess.run(["zstd", "-d", "-c"], input=raw, capture_output=True).stdout
    epoch = struct.unpack("<q", pl[:8])[0]
    p, px, qt = 8, 0, 0
    while p < len(pl):
        ev, p = uvar(pl, p); _ex, p = zz(pl, p); lo, p = zz(pl, p); _at, p = uvar(pl, p); cnt, p = uvar(pl, p)
        lts = epoch + lo; kind = ev & 0xFF
        for _ in range(cnt):
            dp, p = zz(pl, p); dq, p = zz(pl, p); px += dp; qt += dq
            if (kind == 1 or kind == 4) and ev & (1 << 29) and px == level:
                nq = max(qt, 0)
                if nq != q:
                    q = nq
                    if lts >= lo_ns - 3600 * 10**9:
                        ev_log.append((lts, q))
        if kind == 3 and ev & (1 << 29) and px <= level:   # clear бидов >= px: уровень level выше px снимается
            pass
        if lts > hi_ns:
            done = True; break
print(f"level={level} floor={floor:.0f} смен qty@level в журнале {len(ev_log)}")
cross = []
prev_below = None
st = None
for ts, qq in ev_log:
    below = qq < floor
    if st is None:
        st = below
        if ts >= lo_ns:
            cross.append((ts, qq, "старт " + ("ниже" if below else "выше")))
        continue
    if below != st:
        st = below
        if lo_ns <= ts <= hi_ns:
            cross.append((ts, qq, "вниз <floor" if below else "вверх >=floor"))
t0 = adds[0]
print("пересечения порога (t - первая добавка, с):")
for ts, qq, k in cross:
    print(f"  {(ts-t0)/1e9:10.3f}  qty={qq:.0f}  {k}")
for i, a in enumerate(adds, 1):
    before = [(ts, qq) for ts, qq in ev_log if ts <= a]
    last = before[-1] if before else None
    lows = [ts for ts, qq in before if qq < floor]
    print(f"добавка {i} t={(a-t0)/1e9:.3f}c: qty@level перед ней={last[1]:.0f} (смена {(a-last[0])/1e6:.1f} мс назад); "
          f"последний кадр ниже floor: {'t='+format((lows[-1]-t0)/1e9,'.3f')+'c' if lows else 'нет'}; "
          f"min qty за lookback={min((qq for ts,qq in before if ts>=lo_ns), default=last[1]):.0f}")
