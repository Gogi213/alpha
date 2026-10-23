#!/usr/bin/env python3
"""TK-377: отрывы Gate-фьючерса от DEX (монеты Gate-фьюч без Bybit с DEX ETH/Base: LAPTOP, PROM, PROS), 15-21.10. Запуск на calc: tk377-lag.py OUT_DIR
gap(t)=ln(Gate_fut/DEX)*1e4 бп (DEX = цена главного пула, последний своп <= t, ffill); dev = gap - медиана gap за 7 суток (убирает постоянный сдвиг).
Эпизод: первый момент |dev|>=X (X 50/100/200/500 бп = 0,5/1/2/5 %), следующий эпизод не раньше конца окна 3600 с. Вход против отрыва по Gate-фьючу t+1 с.
Сведение: |dev|<=X/2. Кто двигался: за время до сведения (<=3600 с) вклад Gate и DEX в падение |dev|. MAE: наибольший рост |dev| против входа до выхода.
PnL по Gate-ноге: s*ln(P_exit/P_entry); выход = сведение, иначе таймаут (600/3600 с). net тейкер = PnL - 2*7.5 бп. Мейкер: верхняя граница (комиссия 0, без модели заполнения)."""
import sys, os, csv, gzip, json, math, statistics, array, collections, random
RND = random.Random(377)
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
ROOT = '/data/tk0374/usd'; GR = '/srv/alpha-sda/tk372-gate/raw/fut'; D0 = 1792022400; DAYS = range(15, 22); N = 7 * 86400
COINS = ('LAPTOP', 'PROM', 'PROS'); XS = (50, 100, 200, 500); TOS = (600, 3600); FEE = 7.5
def gate(coin):
    a = array.array('d', [float('nan')]) * N
    for day in DAYS:
        f = f'{GR}/{coin}_USDT/2026-10-{day:02d}.csv.gz'
        if not os.path.exists(f): continue
        with gzip.open(f, 'rt') as fh:
            h = fh.readline().rstrip('\n').split(','); ti, pi = h.index('create_time_ms'), h.index('price')
            for l in fh:
                s = l.split(',')
                try: v = float(s[ti]); i = int(v / 1000 if v > 1e11 else v) - D0
                except ValueError: continue
                if 0 <= i < N: a[i] = float(s[pi])
    last = float('nan')
    for i in range(N):
        if a[i] == a[i]: last = a[i]
        else: a[i] = last
    return a
def dex(coin):
    best = None
    for net in ('ethereum', 'base'):
        for d in os.listdir(f'{ROOT}/{net}'):
            if d.split('-')[0] != coin: continue
            pools = collections.defaultdict(list)
            for day in DAYS:
                p = f'{ROOT}/{net}/{d}/2026-10-{day:02d}.csv'
                if os.path.exists(p):
                    for r in csv.DictReader(open(p)):
                        if r['price_usd']: pools[r['pool']].append((int(r['ts']), int(r['logidx']), float(r['price_usd'])))
            for pool, rows in pools.items():
                if best is None or len(rows) > len(best[2]): best = (net, pool, rows)
    net, pool, rows = best; rows.sort()
    a = array.array('d', [float('nan')]) * N; age = array.array('i', [-1]) * N
    for ts, _, px in rows:
        i = ts - D0
        if 0 <= i < N: a[i] = px
    last = float('nan'); la = -10**9
    for i in range(N):
        if a[i] == a[i]: last = a[i]; la = i
        else: a[i] = last
        age[i] = i - la if la > -10**9 else -1
    return net, pool, len(rows), a, age
res = []; summ = []
for coin in COINS:
    g = gate(coin); net, pool, nsw, d, age = dex(coin)
    ok = [i for i in range(N) if g[i] == g[i] and d[i] == d[i]]
    gap = array.array('d', [float('nan')]) * N
    for i in ok: gap[i] = math.log(g[i] / d[i]) * 1e4
    if nsw < 200 or len(ok) < 86400 or not (0.8 < statistics.median(g[i] / d[i] for i in ok[::600]) < 1.25):
        summ.append(dict(coin=coin, net=net, pool=pool, swaps=nsw, status='skip: DEX нет/цена не совпала', sec_both=len(ok))); continue
    med = statistics.median(gap[i] for i in ok[::60])
    summ.append(dict(coin=coin, net=net, pool=pool, swaps=nsw, sec_both=len(ok), gap_median_bp=round(med, 1), gap_sd_bp=round(statistics.pstdev([gap[i] - med for i in ok[::60]]), 1)))
    for X in XS:
        t = 0
        while t < N - 3700:
            dev = gap[t] - med if gap[t] == gap[t] else float('nan')
            if not (dev == dev and abs(dev) >= X): t += 1; continue
            s = -1 if dev > 0 else 1   # против отрыва: Gate выше DEX -> шорт
            te = t + 1; day = t // 86400
            if g[te] != g[te] or d[te] != d[te]: t += 1; continue
            tconv = None; mae = 0.0
            for u in range(te, min(te + 3600, N - 1) + 1):
                dv = gap[u] - med
                if dv != dv: continue
                grow = abs(dv) - abs(gap[te] - med)
                if grow > mae: mae = grow
                if abs(dv) <= X / 2: tconv = u - te; break
            end = te + (tconv if tconv is not None else 3600)
            dg = math.log(g[end] / g[te]) * 1e4; dd = math.log(d[end] / d[te]) * 1e4
            # вклад в падение |dev|: Gate идёт к DEX = s*dg>0 ; DEX идёт к Gate = -s*dd>0
            gate_part = s * dg; dex_part = -s * dd
            row = dict(coin=coin, X=X, day=15 + day, t_entry=te, dev0=round(dev, 1), age_dex=age[t], conv_s=tconv if tconv is not None else '', gate_part=round(gate_part, 1), dex_part=round(dex_part, 1), mae=round(mae, 1))
            hold = tconv if tconv is not None else 3600; hr = (te // 3600) * 3600; cs = []
            for _ in range(10):
                tc = hr + RND.randrange(3600) + 1
                if tc + hold < N and g[tc] == g[tc] and g[tc + hold] == g[tc + hold]: cs.append(s * math.log(g[tc + hold] / g[tc]) * 1e4)
            row['ctl_same_hold'] = round(sum(cs) / len(cs), 1) if cs else ''
            row['hold_s'] = hold
            for to in TOS:
                e = te + min(tconv if tconv is not None else to, to)
                pnl = s * math.log(g[e] / g[te]) * 1e4
                row[f'pnl{to}'] = round(pnl, 1)
            res.append(row)
            t = te + (tconv if tconv is not None else 3600) + 1
json.dump(summ, open(f'{OUT}/summ.json', 'w'))
cols = list(res[0]); w = csv.DictWriter(open(f'{OUT}/episodes.csv', 'w', newline=''), cols); w.writeheader(); w.writerows(res)
print(len(res)); print(json.dumps(summ))
