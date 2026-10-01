#!/usr/bin/env python3
# TK-026 (только чтение): какая доля записей бинлога суток лежит дальше X bps от цены последней сделки.
#   python3 tk026-depth-probe.py <SYM-день.binlog> [--every 20] [--ref-bps 5,10,20,50,100,200,500]
# Кадры v3 независимы (эпоха и дельты цены/размера — внутри кадра), поэтому читается каждый --every-й кадр + кадр 0 (снимок).
# Сжатие кадра — внешним `zstd -d -c` (модуля zstandard на деке нет). Доля записей ≈ доля байт (по 2–3 байта varint на запись).
import argparse, struct, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument('path')
ap.add_argument('--every', type=int, default=20)
ap.add_argument('--bands', default='2,5,10,20,50,100,200,500')
a = ap.parse_args()
bands = [float(x) for x in a.bands.split(',')]


def uvar(b, p):
    r = s = 0
    while True:
        c = b[p]; p += 1
        r |= (c & 0x7f) << s
        if c < 0x80:
            return r, p
        s += 7


def zz(b, p):
    v, p = uvar(b, p)
    return (v >> 1) ^ -(v & 1), p


f = open(a.path, 'rb')
hdr = f.read(25)
assert hdr[:4] != b'\x28\xb5\x2f\xfd', 'контейнер .zst не поддержан'
tick = struct.unpack('<d', hdr[5:13])[0] if False else struct.unpack('<q', hdr[5:13])[0] / 1e9
frames = []
while True:
    h = f.read(4)
    if len(h) < 4:
        break
    n = struct.unpack('<I', h)[0]
    off = f.tell()
    frames.append((off, n))
    f.seek(n, 1)
print(f'{a.path}: кадров {len(frames)}, tick={tick}')

last_trade = None
tot = [0] * (len(bands) + 1)   # дельты (кадры >0): записи depth по полосам расстояния
snap = [0] * (len(bands) + 1)  # кадр 0: уровни снимка
trades = 0
sampled = 0
nrec_depth = 0


def bucket(d):
    for i, b in enumerate(bands):
        if d <= b:
            return i
    return len(bands)


for idx, (off, n) in enumerate(frames):
    if not (idx == 0 or idx % a.every == 0):
        continue
    f.seek(off)
    raw = f.read(n)
    pl = subprocess.run(['zstd', '-d', '-c'], input=raw, capture_output=True).stdout
    p = 8
    recs = []
    px = 0
    qt = 0
    while p < len(pl):
        ev, p = uvar(pl, p)
        _, p = zz(pl, p)
        _, p = zz(pl, p)
        _, p = uvar(pl, p)
        cnt, p = uvar(pl, p)
        for _ in range(cnt):
            dp, p = zz(pl, p)
            dq, p = zz(pl, p)
            px += dp
            qt += dq
            recs.append((ev & 0xff, px, qt))
    if idx == 0:
        # мид снимка: между лучшей бид/аск неизвестна → берём цену последней сделки позже; пока медиана цен снимка
        ps = sorted(r[1] for r in recs)
        ref = ps[len(ps) // 2] if ps else None
    else:
        ref = last_trade
    for t, pxx, q in recs:
        if t == 2:
            last_trade = pxx
            trades += 1
            if idx == 0 or ref is None:
                ref = pxx
            continue
        if ref is None:
            continue
        d = abs(pxx - ref) / ref * 1e4
        (snap if idx == 0 else tot)[bucket(d)] += 1
        if idx != 0:
            nrec_depth += 1
    sampled += 1


def show(name, arr):
    s = sum(arr)
    if not s:
        return
    cum = 0
    print(name, f'всего {s}')
    for i, b in enumerate(bands + [None]):
        cum += arr[i]
        lab = f'≤{b:g} bps' if b is not None else f'>{bands[-1]:g} bps'
        print(f'  {lab:>10}: {arr[i]:>9} {arr[i]/s*100:6.2f} %   дальше: {(1-cum/s)*100:6.2f} %')


print(f'прочитано кадров {sampled}, сделок {trades}, depth-записей дельт {nrec_depth}')
show('кадр 0 (снимок)', snap)
show('дельты', tot)
