#!/usr/bin/env python3
# TK-037: корни по участкам одной сетки для монето-месяцев со сменой (tk037-tick-scan.py -> .days.csv).
# <ROOT>/<месяц>-<SYM>-g<N>/: ссылки на суточные файлы участка, verify-маркер, session.json/instruments.csv из --meta.
# Манифест <ROOT>/manifest-after_tick_change.csv: первые сутки участков >= 2 (after_tick_change).
import csv, os, sys, shutil, collections
days_csv, root, meta = sys.argv[1:4]
V = '/data/tk037/vroots'
d = collections.defaultdict(list)
for r in csv.DictReader(open(days_csv)):
    d[(r['month'], r['symbol'])].append((r['day'], r['tick_e9'], r['step_e9']))
os.makedirs(root, exist_ok=True)
man = csv.writer(open(f'{root}/manifest-after_tick_change.csv', 'w', newline=''))
man.writerow(['month', 'symbol', 'root', 'first_day', 'tick_e9', 'step_e9', 'flag'])
for (m, s), L in sorted(d.items()):
    L.sort(); segs = []
    for day, t, st in L:
        if not segs or segs[-1][0] != (t, st): segs.append([(t, st), []])
        segs[-1][1].append(day)
    if len(segs) < 2: continue
    for i, ((t, st), ds) in enumerate(segs, 1):
        r = f'{root}/{m}-{s}-g{i}'; os.makedirs(r, exist_ok=True)
        for day in ds:
            f = f'{s}-{day}.binlog'; p = f'{r}/{f}'
            if not os.path.lexists(p): os.symlink(os.path.realpath(f'{V}/{m}/{f}'), p)
        for f in ('session.json', 'instruments.csv'): shutil.copy(f'{meta}/{f}', r)
        shutil.copy(f'{V}/{m}/verify-{s}.status', r)
        if i > 1: man.writerow([m, s, r, ds[0], t, st, 'after_tick_change'])
