#!/usr/bin/env python3
"""TK-416: минуты со сделками (v>0 в свечах TK-411) без единого события книги — дыры архива ob200.
  holes <validate.csv> <корень ob> <свечи> <out.csv> — для файлов с мин_в_разрыве>0 или мин_вне_книги>0.
Событийно: по каждой строке потока берётся только ts (регулярка), посекундных агрегатов нет."""
import sys, csv, re, gzip, zipfile, os, datetime

DAY = 86400000
rx = re.compile(rb'"ts":(\d+)')


def main(vcsv, root, cdir, out):
    rows = []
    for r in csv.DictReader(open(vcsv, encoding='utf-8')):
        if int(r['мин_в_разрыве']) or int(r['мин_вне_книги']):
            rows.append(r)
    res = []
    for r in rows:
        mkt, sym, day = r['рынок'], r['инструмент'], r['сутки']
        d0 = int(datetime.datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc).timestamp()) * 1000
        vm = {}
        with gzip.open(os.path.join(cdir, mkt, sym + '.csv.gz'), 'rt') as f:
            rd = csv.reader(f)
            next(rd)
            for c in rd:
                t = int(c[0])
                if d0 <= t < d0 + DAY:
                    vm[t // 60000] = float(c[5])
        zf = zipfile.ZipFile(os.path.join(root, mkt, sym, '%s_%s_ob200.data.zip' % (day, sym)))
        have = set()
        first = last = None
        for line in zf.open(zf.namelist()[0]):
            m = rx.search(line)
            if m:
                ts = int(m.group(1))
                have.add(ts // 60000)
                if first is None:
                    first = ts
                last = ts
        fm, lm = first // 60000, last // 60000
        inside = [m for m in vm if fm <= m <= lm and m not in have]
        outside = [m for m in vm if m < fm or m > lm]
        res.append({'рынок': mkt, 'инструмент': sym, 'сутки': day, 'мин_свеч': len(vm),
                    'мин_без_событий_внутри': len(inside), 'из_них_v>0': sum(1 for m in inside if vm[m] > 0),
                    'мин_вне_файла': len(outside), 'из_них_v>0_вне': sum(1 for m in outside if vm[m] > 0)})
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, list(res[0]))
        w.writeheader()
        w.writerows(res)
    print('файлов', len(res), 'внутри v>0:', sum(r['из_них_v>0'] for r in res), 'вне v>0:', sum(r['из_них_v>0_вне'] for r in res))


main(*sys.argv[2:6]) if sys.argv[1] == 'holes' else sys.exit(1)
