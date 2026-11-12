#!/usr/bin/env python3
"""TK-376 проход 6: из tk376-gap5.py делает tk376-gap6c.py — только контроль «случайный момент без условия gap» (п.6): 300 случайных секунд на пул-сутки, знак по gap, вход +1 с, тот же выход (net_1); эпизоды отключены. Запуск: patch.py gap5.py gap6c.py"""
import sys
s = open(sys.argv[1]).read()
a = s.index('            # контроль 2 (проход 4)'); b = s.index('            # контроль: |gap| во все секунды')
new = '''            import random, zlib
            rng = random.Random(zlib.crc32(f'{net}{dirname}{pool}'.encode()))
            tkr = TAKER[kind]
            for d in okdays:
                lo, hi = d * 86400, (d + 1) * 86400
                cand = [k for k in range(max(lo, 1), min(hi, N - 2)) if g[k] == g[k] and g[k + 1] == g[k + 1] and g[k] != 0]
                if len(cand) < 600: continue
                acc = {}
                for k in rng.sample(cand, 300):
                    ag = abs(g[k]); bn = 'le_fee' if ag <= fee else 'fee_50' if ag < 50 else '50_100' if ag < 100 else 'ge100'
                    v = (1 if g[k] > 0 else -1) * g[k + 1] - tkr - fe0
                    n_, s_ = acc.get(bn, (0, 0.0)); acc[bn] = (n_ + 1, s_ + v)
                for bn, (n_, s_) in acc.items():
                    out.append(dict(pool='CTRLSW', net=net, coin=dirname, dex=pool, day=15 + d, bin=bn, n=n_, nsum=round(s_, 2), fee_bp=fe0, bkind=kind))
'''
s = s[:a] + new + s[b:]
old = "['net', 'coin', 'dex', 'day', 'n', 'q50', 'q90', 'sh_fee', 'nn', 'nsum', 'nc', 'ncsum', 'fee_bp', 'sw_day', 'bkind']"
assert old in s; s = s.replace(old, "['net', 'coin', 'dex', 'day', 'bin', 'n', 'nsum', 'fee_bp', 'bkind']")
h = "hot = [k for k in range(1, N) if g[k] == g[k] and abs(g[k]) > fee and ok[k]]"
assert h in s; s = s.replace(h, "hot = []")
open(sys.argv[2], 'w').write(s)
