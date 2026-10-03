#!/usr/bin/env python3
# TK-037: сетки (tick_e9, step_e9) по заголовкам суточных файлов vroots; монето-месяцы со сменой сетки.
# Выход: <out>.days.csv (все сутки) и <out>.csv (монета, месяц, день смены, сетка до/после, участки).
import os, re, struct, sys, csv, collections
V = '/data/tk037/vroots'; out = sys.argv[1]
rx = re.compile(r'^(.+USDT)-(\d{4}-\d{2}-\d{2})\.binlog$')
days = collections.defaultdict(list)
for m in sorted(os.listdir(V)):
    for f in os.listdir(f'{V}/{m}'):
        g = rx.match(f)
        if not g: continue
        with open(f'{V}/{m}/{f}', 'rb') as fh: h = fh.read(21)
        t, s = struct.unpack('<qq', h[5:21])
        days[(m, g.group(1))].append((g.group(2), t, s, h[4]))
with open(out + '.days.csv', 'w', newline='') as fd, open(out + '.csv', 'w', newline='') as fc:
    wd = csv.writer(fd); wc = csv.writer(fc)
    wd.writerow(['month', 'symbol', 'day', 'tick_e9', 'step_e9', 'version'])
    wc.writerow(['month', 'symbol', 'n_days', 'n_grids', 'change_days', 'grids_in_order', 'days_per_segment'])
    n = 0
    for (m, s), L in sorted(days.items()):
        L.sort()
        for d, t, st, v in L: wd.writerow([m, s, d, t, st, v])
        segs = []
        for d, t, st, v in L:
            if not segs or segs[-1][0] != (t, st): segs.append([(t, st), d, 0])
            segs[-1][2] += 1
        if len(segs) > 1:
            n += 1
            wc.writerow([m, s, len(L), len(segs), ' '.join(x[1] for x in segs[1:]),
                         ' > '.join(f'{x[0][0]}/{x[0][1]}' for x in segs), '+'.join(str(x[2]) for x in segs)])
    print('coin-months', len(days), 'with change', n)
