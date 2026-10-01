#!/usr/bin/env python3
# TK-020: обратная привязка. Ссылки, переведённые tk020-relink.py на ~/sb (relink-applied.csv, статус ok), возвращаются на локальную
# копию, если она ещё лежит на деке настоящим файлом; ссылки, у которых копию уже удалили, остаются на ~/sb. Файлы не удаляет и не
# трогает. Сухой прогон по умолчанию (sync/tk020/unrelink-plan.csv); --apply — атомарная подмена (ln в tmp + rename).
import os, sys, csv, collections
OUT = '/home/deck/alpha/sync/tk020/'
apply = '--apply' in sys.argv
rows = []; stat = collections.Counter(); files = {}
for x in csv.DictReader(open(OUT + 'relink-applied.csv')):
    if x['status'] != 'ok': continue
    p, old, new = x['link'], x['old_real'], x['new_target']
    if not os.path.islink(p): st = 'link-gone'
    elif os.readlink(p) != new: st = 'link-changed-skip'
    elif os.path.islink(old) or not os.path.isfile(old): st = 'keep-sb'
    else: st = 'restore'
    if st == 'restore':
        files[old] = os.path.getsize(old)
        if apply:
            tmp = p + '.unrelink'; os.symlink(old, tmp); os.replace(tmp, p)
    stat[st] += 1; rows.append((p, old, st))
with open(OUT + ('unrelink-applied.csv' if apply else 'unrelink-plan.csv'), 'w', newline='') as f:
    csv.writer(f).writerows([('link', 'old_real', 'status')] + rows)
print('apply' if apply else 'dry', dict(stat), 'файлов под restore', len(files), '%.1f ГБ' % (sum(files.values()) / 1e9))
