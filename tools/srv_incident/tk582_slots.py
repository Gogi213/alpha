# per-shard slot table: slots.py <dir> <prefix book|mark|trade> <day> <HH:MM:SS> <HH:MM:SS> <field>
import sys, glob, json, datetime
d, pref, day, a, b, fld = sys.argv[1:7]
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
t0, t1 = ep(a), ep(b)
tab = {}; names = []
for f in sorted(glob.glob(d + '/stats-%s*.jsonl' % pref)):
    n = f.split('stats-')[1].split('.')[0]; names.append(n)
    for l in open(f):
        if '"ts_ms"' not in l: continue
        e = json.loads(l)
        if t0 <= e['ts_ms'] <= t1:
            v = e.get(fld)
            if isinstance(v, list): v = sum(v)
            tab.setdefault(e['ts_ms'] // 1000, {})[n] = v
print('slot   ' + ' '.join(n[-2:].rjust(5) for n in names))
for s in sorted(tab):
    row = tab[s]
    print(datetime.datetime.fromtimestamp(s, datetime.timezone.utc).strftime('%H:%M:%S'), ' '.join((('%5.0f' % row[n]) if isinstance(row.get(n), (int, float)) else '    .') for n in names))
