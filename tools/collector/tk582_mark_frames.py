#!/usr/bin/env python3
"""TK-582 v3: mark00 по кадрам (по сырым записям). stdin — разжатый `mark00-*.tsv`; печатает на stdout JSON: по секунде события E — строк `m`, маска символов (hex по номеру символа).
usage: zstd -dc mark00-*.tsv.zst | python3 tk582_mark_frames.py <E_от_мс> <E_до_мс> > frames.json
Время приёма строки — накопление столбца 2 (дельта, нс) по всем строкам файла; E строки `m` = rx_мс − столбец 3 (задержка)."""
import collections
import json
import sys

lo, hi = int(sys.argv[1]), int(sys.argv[2])
sym_id = {}
frames = {}   # сек E -> [строк m, маска]
kinds = collections.Counter()
rx = 0
for ln in sys.stdin.buffer:
    f = ln.split(b"\t", 4)
    k = f[0]
    kinds[k] += 1
    if len(f) < 3:
        continue
    try:
        d = int(f[1])
    except ValueError:
        continue
    rx = d if d > 10**17 else rx + d
    if k != b"m":
        continue
    e = rx // 1_000_000 - int(f[2])
    if e < lo or e >= hi:
        continue
    s = f[3]
    i = sym_id.get(s)
    if i is None:
        i = sym_id[s] = len(sym_id)
    sec = (e + 500) // 1000
    fr = frames.get(sec)
    if fr is None:
        fr = frames[sec] = [0, 0]
    fr[0] += 1
    fr[1] |= 1 << i
json.dump({"kinds": {k.decode(): v for k, v in kinds.items()}, "syms": [s.decode() for s in sorted(sym_id, key=sym_id.get)],
           "frames": {str(s): [v[0], format(v[1], "x")] for s, v in frames.items()}}, sys.stdout)
