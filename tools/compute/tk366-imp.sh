#!/usr/bin/env bash
# TK-366 (G-016), фаза 2: импорт всего скачанного (журнал dl.tsv status=dl) в бинлоги на NX потоков (слово владельца: 16 ядер). tk366-imp.sh [NX=16]
# Сырьё на sda5 идёт первым (импорт освобождает больше, чем пишет), потом /data. Готовое и уже импортированное пропускается — повторный запуск безопасен.
D=/srv/alpha-sda/tk366; NX=${1:-${NX:-16}}; mkdir -p "$D/log" "$D/tmp/i"; touch "$D/log/import2.tsv"
while [ -f "$D/tr-phase.pending" ]; do sleep 60; done   # дотяжка сделок (tk366-tr.sh) идёт — список строим после неё
python3 - "$D" > "$D/log/imp-list.txt" <<'PY'
import sys, glob
D = sys.argv[1]; done = set()
for f in glob.glob(D + '/log/import-*.tsv') + [D + '/log/import2.tsv']:
    for l in open(f, errors='replace'):
        p = l.split('\t')
        if len(p) > 3 and (p[3] in ('ok', 'skip') or p[3].startswith('fail')): done.add((p[0], p[1], p[2]))
rows = []
for l in open(D + '/log/dl.tsv'):
    p = l.rstrip('\n').split('\t')
    if len(p) > 7 and p[3] == 'dl' and (p[0], p[1], p[2]) not in done: rows.append((p[7] != D + '/raw', p[0], p[1], p[2], p[7]))
for r in sorted(set(rows)): print(*r[1:])
PY
wc -l < "$D/log/imp-list.txt" | sed 's/^/к импорту: /'
xargs -a "$D/log/imp-list.txt" -r -P "$NX" -n 4 bash "$D/tk366-imp-one.sh"
echo "импорт завершён"
