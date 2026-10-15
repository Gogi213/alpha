#!/usr/bin/env python3
"""TK-259: свод result.jsonl пробы I/O -> медиана[мин-макс] по (опыт, ячейка). Запуск: python -X utf8 -I tk259-io-agg.py <result.jsonl>"""
import sys
import json,statistics as st,collections
rows=[json.loads(l) for l in open(sys.argv[1])]
g=collections.OrderedDict()
for r in rows:
    if r['exp']=='meta': continue
    g.setdefault((r['exp'],r['cell']),[]).append(r)
def med(rs,k): 
    v=[r[k] for r in rs if k in r]; return (st.median(v),min(v),max(v)) if v else None
for (e,c),rs in g.items():
    out=[f"{e} {c:28s} n={len(rs)}"]
    for k in ('wall_s','MB','MBps','cpu_s','sda_MB','sda_busy_pct','sdb_MB','sdb_busy_pct','first_s','next_avg_s'):
        m=med(rs,k)
        if m and (k in('wall_s','MBps') or m[0]): out.append(f"{k}={m[0]:g}[{m[1]:g}-{m[2]:g}]")
    print(' '.join(out))
