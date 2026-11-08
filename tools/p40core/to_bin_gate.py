"""TK-500: эталон-Python для `pq2bin gate`: сутки Gate futures (csv.gz по часам) -> 24 книжных .bin (33 Б). Запуск: to_bin_gate.py <dir> <контракт> <ГГГГММДД> <outdir> [множитель]"""
import sys, os, gzip, struct
d, c, day, out = sys.argv[1:5]; mult = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
os.makedirs(out, exist_ok=True)
for h in range(24):
    p = f"{d}/{c}-{day}{h:02d}.csv.gz"
    if not os.path.exists(p): continue
    L = {}; b = bytearray(); grp = -1; last = None
    for line in gzip.open(p, "rt"):
        t, ev, px, sz, _, _ = line.rstrip("\n").split(",")
        ts = float(t); px = float(px); sz = float(sz); et = round(ts * 1000)
        if ev == "set":
            L[px] = sz; b += struct.pack("<qqddB", et, -1, px, abs(sz) * mult, 1 | (2 if sz > 0 else 0)); continue
        if ts != last: grp += 1; last = ts
        old = L.get(px, 0.0); new = old + sz if ev == "make" else old - sz
        if abs(new) < 1e-12: new = 0.0
        if old != 0.0 and new != 0.0 and (old > 0) != (new > 0): b += struct.pack("<qqddB", et, grp, px, 0.0, 2 if old > 0 else 0)
        if new == 0.0:
            L.pop(px, None)
            if old != 0.0: b += struct.pack("<qqddB", et, grp, px, 0.0, 2 if old > 0 else 0)
        else:
            L[px] = new; b += struct.pack("<qqddB", et, grp, px, abs(new) * mult, 2 if new > 0 else 0)
    open(f"{out}/{h:02d}.bin", "wb").write(b)
