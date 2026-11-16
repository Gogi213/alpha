#!/usr/bin/env python3
"""Записи одного символа за интервал из файлов коллектора: время приёма (UTC), тип, поля. Для разбора дыр.
python3 symslice.py <файл.tsv.zst> <символ> <от YYYY-MM-DDTHH:MM> <до YYYY-MM-DDTHH:MM> [макс. строк]"""
import calendar, subprocess, sys, time
path, sym, t0s, t1s = sys.argv[1:5]
lim = int(sys.argv[5]) if len(sys.argv) > 5 else 400
t0 = calendar.timegm(time.strptime(t0s, '%Y-%m-%dT%H:%M')) * 10**9
t1 = calendar.timegm(time.strptime(t1s, '%Y-%m-%dT%H:%M')) * 10**9
p = subprocess.Popen(['zstd', '-dcq', path], stdout=subprocess.PIPE)
t = 0
s = sym.encode()
n = 0
for line in p.stdout:
    f = line.rstrip(b'\n').split(b'\t')
    try:
        t += int(f[1])
    except Exception:
        continue
    if t > t1:
        break
    if t < t0:
        continue
    ty = f[0]
    if (ty in (b'b', b't') and len(f) > 4 and f[4] == s) or (ty == b'm' and len(f) > 3 and f[3] == s):
        sec = t / 1e9
        print(time.strftime('%H:%M:%S', time.gmtime(sec)) + ('%.3f' % (sec % 1))[1:], b' '.join(x[:14] for x in [f[0]] + f[2:]).decode(errors='replace'))
        n += 1
        if n >= lim:
            break
p.kill()
