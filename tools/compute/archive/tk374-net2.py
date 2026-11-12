#!/usr/bin/env python3
"""TK-374: net банка DEX<->Gate. usage: tk374-net2.py raw.json pool-fees.json OUT_PREFIX [gate_taker_bp=20]
rev (ход Gate -> вход в пул, выход хеджем на Gate): net = diff - fee_пула - taker_Gate; fwd (вход и выход на Gate): net = diff - 2*taker_Gate. Газ не учтён.
diff — событие-контроль в бп (по v[0]=n, v[4]=сумма diff); уровень клетки — взвешено по событиям, уровень монеты — по монете. fee_пула — из pool-fees.json (v3: on-chain fee(), иначе 0,30 %)."""
import sys, json, csv, collections
raw = json.load(open(sys.argv[1])); fees = json.load(open(sys.argv[2])); pre = sys.argv[3]
TK = float(sys.argv[4]) if len(sys.argv) > 4 else 20.0
coin = collections.defaultdict(lambda: [0, 0.0])      # (dir,x,T,h,coin,net) -> n, sum diff
for r in raw:
    if r['status'] != 'ok': continue
    for k, v in r.get('cells', {}).items():
        d, x, T, h, dd = k.split('|')
        c = coin[(d, x, int(T), int(h), r['coin'], r['net'])]; c[0] += v[0]; c[1] += v[4]
rows = []
for (d, x, T, h, cn, net), (n, s) in sorted(coin.items()):
    if n < 30: continue
    f = fees.get(f'{cn}@{net}', {}).get('fee_bp')
    diff = s / n
    fee = f if d == 'rev' else None
    netv = diff - (f + TK) if d == 'rev' else diff - 2 * TK
    rows.append([d, x, T, h, cn, net, n, round(diff, 2), f, round(netv, 2)])
with open(pre + '-coins.csv', 'w', newline='', encoding='utf-8') as fh:
    w = csv.writer(fh); w.writerow(['dir', 'x', 'T', 'h', 'coin', 'net', 'n', 'diff_bp', 'pool_fee_bp', 'net_bp']); w.writerows(rows)
cell = collections.defaultdict(lambda: [0, 0.0, 0.0, 0, 0])
for d, x, T, h, cn, net, n, diff, f, nv in rows:
    c = cell[(d, x, T, h)]; c[0] += n; c[1] += diff * n; c[2] += nv * n; c[3] += 1; c[4] += nv > 0
with open(pre + '-cells.csv', 'w', newline='', encoding='utf-8') as fh:
    w = csv.writer(fh); w.writerow(['dir', 'x', 'T', 'h', 'n', 'diff_bp', 'net_bp', 'coins', 'coins_net_pos'])
    for k, (n, sd, sn, nc, npos) in sorted(cell.items()): w.writerow([*k, n, round(sd / n, 2), round(sn / n, 2), nc, npos])
print('taker', TK)
for k, (n, sd, sn, nc, npos) in sorted(cell.items(), key=lambda kv: -kv[1][2] / kv[1][0])[:12]:
    print(k, 'n', n, 'diff', round(sd / n, 1), 'net', round(sn / n, 1), 'монет net>0', npos, '/', nc)
best = sorted(rows, key=lambda r: -r[9])[:10]
print('лучшие монета-клетки:'); [print(*r) for r in best]
