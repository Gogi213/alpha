#!/usr/bin/env python3
"""TK-366: исключения verify недели 15–21.10 — csv (рынок, символ, статус, суток, trades, violations, доля, gaps, invariants).
   python3 tk366w-verify-csv.py [корень=/srv/alpha-sda/tk366w] > csv ; берёт log/verify.tsv, поверх — log/verify-reimp.tsv; строки status != ok"""
import re, sys, os
D = sys.argv[1] if len(sys.argv) > 1 else '/srv/alpha-sda/tk366w'
st = {}
for f in ('verify.tsv', 'verify-reimp.tsv'):
    p = os.path.join(D, 'log', f)
    if os.path.exists(p):
        for l in open(p, errors='replace'):
            q = l.rstrip('\n').split('\t')
            if len(q) > 3: st[(q[0], q[1])] = q[3]
num = lambda s, k: int(re.search(k + r'=(\d+)', s).group(1))
print('market,symbol,status,days,trades,violations,share,gaps,invariants')
for (mk, sym), s in sorted(st.items()):
    if s == 'ok': continue
    log = os.path.join(D, 'bin-' + mk, 'e-2026-10', 'verify-' + sym + '.log')
    tot = [l for l in open(log, errors='replace') if l.startswith('verify: files=')] if os.path.exists(log) else []
    if not tot: print(f'{mk},{sym},{s},,,,,,'); continue
    t = tot[-1]; tr, vi = num(t, 'trades'), num(t, 'violations')
    print(f'{mk},{sym},{s},{num(t, "files")},{tr},{vi},{vi / tr if tr else 0:.5f},{num(t, "gaps")},{num(t, "invariants")}')
