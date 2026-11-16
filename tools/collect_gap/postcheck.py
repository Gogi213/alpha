#!/usr/bin/env python3
"""Сверка окна записей коллектора с REST-минутками биржи: «символ × минута» (приёмка выкладки пул-фикса, G-022).

Вход: матрица записей (`aggregate.py`, окно [t-start, t-end)), REST-минутки (`klines.py`, то же окно), состав выше порога по REST (`univ.py`).
Для каждого символа состава с оборотом ≥ порога: торговал ли рынок в минуту (объём свечи > 0) и была ли запись `b` (книга), `t` (сделки).
Первая и последняя минуты окна не считаются (граница). Символ без записей с оборотом < 1,2 × порога помечен `border` (мог пересечь порог внутри окна). «Мягкая» дыра — нет записи в m−1, m и m+1.
Выход: JSON-сводка и список символов без единой записи книги за окно (с оборотом) — «не подписаны» независимо от счётчиков коллектора.
Запуск: python3 postcheck.py --exchange bybit --agg agg/bybit.npz --klines klines-dir --univ univ.json --label after --out out.json
"""
import argparse, glob, json, os, time
import numpy as np


def load_klines(kdir, ex):
    rows, m0 = {}, None
    for p in sorted(glob.glob(os.path.join(kdir, f'{ex}-klines-p*.npz'))):
        d = np.load(p)
        m0 = int(d['minute0'][0])
        for j, s in enumerate(d['syms']):
            s = str(s)
            if s not in rows or (bool(d['ok'][j]) and not rows[s][0]):
                rows[s] = (bool(d['ok'][j]), d['vol'][j], d['qv'][j])
    return rows, m0


def soft(hole, rec):
    """Дыра, у которой нет записи и в соседних минутах (границы окна — нет)."""
    out = hole.copy()
    out[1:-1] &= (rec[:-2] == 0) & (rec[2:] == 0)
    out[0] = out[-1] = False
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exchange', required=True)
    ap.add_argument('--agg', required=True)
    ap.add_argument('--klines', required=True)
    ap.add_argument('--univ', required=True)
    ap.add_argument('--label', default='')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    u = json.load(open(a.univ))
    thr = u['min_qv']
    univ = {s: v for s, v in u['qv'].items() if v >= thr}
    K, km0 = load_klines(a.klines, a.exchange)
    d = np.load(a.agg)
    am0 = int(d['minute0'][0])
    asym = {str(s): i for i, s in enumerate(d['syms'])}
    B, T = d['book'], d['trade']
    nmin = B.shape[1]
    off = km0 - am0  # индекс в минутках = индекс в матрице записей + off
    lo, hi = 1, nmin - 1  # границы окна не считаются
    res = {'exchange': a.exchange, 'label': a.label, 'window_min': hi - lo, 'univ_ge_thr': len(univ), 'klines_ok': 0, 'klines_missing': []}
    absent, late_first = [], []
    tot = {'sym_min': 0, 'hole_b': 0, 'hole_b_soft': 0, 'hole_t': 0, 'hole_t_soft': 0, 'qv': 0.0, 'qv_hole_b': 0.0, 'stall_sym_min': 0, 'stall_hole_b': 0,
           'prefirst_hole_b': 0}
    with_b = with_t = 0
    for s, q in sorted(univ.items()):
        r = K.get(s)
        if r is None or not r[0]:
            res['klines_missing'].append(s)
            continue
        res['klines_ok'] += 1
        vol, qv = r[1], r[2]
        traded = np.zeros(nmin, dtype=bool)
        qm = np.zeros(nmin)
        for m in range(lo, hi):
            j = m + off
            if 0 <= j < len(vol) and vol[j] > 0:
                traded[m] = True
                qm[m] = max(float(qv[j]), 0.0)
        i = asym.get(s)
        b = B[i] if i is not None else np.zeros(nmin, dtype=np.int32)
        t = T[i] if i is not None else np.zeros(nmin, dtype=np.int32)
        nb = int(b[lo:hi].sum())
        with_b += nb > 0
        with_t += int(t[lo:hi].sum()) > 0
        n = int(traded.sum())
        if n == 0:
            continue
        hb, ht = traded & (b == 0), traded & (t == 0)
        tot['sym_min'] += n
        tot['hole_b'] += int(hb.sum())
        tot['hole_b_soft'] += int(soft(hb, b).sum())
        tot['hole_t'] += int(ht.sum())
        tot['hole_t_soft'] += int(soft(ht, t).sum())
        tot['qv'] += float(qm.sum())
        tot['qv_hole_b'] += float(qm[hb].sum())
        # «срыв» — дыра после первой записи книги символа в окне; «до первой записи» — символ ещё не был в подписке (пересек порог внутри окна)
        nzb = np.nonzero(b[lo:hi])[0]
        first = lo + int(nzb[0]) if len(nzb) else hi
        tot['stall_sym_min'] += int(traded[first:hi].sum())
        tot['stall_hole_b'] += int(hb[first:hi].sum())
        tot['prefirst_hole_b'] += int(hb[lo:first].sum())
        if len(nzb) and first > lo + 2 and hb[lo:first].any():
            late_first.append({'symbol': s, 'qv24': round(q), 'first_b_utc': time.strftime('%H:%M', time.gmtime((am0 + first) * 60)),
                               'traded_min_before_first': int(traded[lo:first].sum())})
        if nb == 0:
            absent.append({'symbol': s, 'qv24': round(q), 'traded_min': n, 'border': q < 1.2 * thr})
    res.update(tot)
    res['with_b'], res['with_t'] = int(with_b), int(with_t)
    res['hole_b_pct'] = round(100 * tot['hole_b'] / max(1, tot['sym_min']), 4)
    res['hole_b_soft_pct'] = round(100 * tot['hole_b_soft'] / max(1, tot['sym_min']), 4)
    res['hole_t_soft_pct'] = round(100 * tot['hole_t_soft'] / max(1, tot['sym_min']), 4)
    res['qv_hole_b_pct'] = round(100 * tot['qv_hole_b'] / max(1.0, tot['qv']), 4)
    res['stall_hole_b_pct'] = round(100 * tot['stall_hole_b'] / max(1, tot['stall_sym_min']), 4)
    res['late_first_n'] = len(late_first)
    res['late_first'] = sorted(late_first, key=lambda x: -x['traded_min_before_first'])
    res['absent_n'] = len(absent)
    res['absent_firm_n'] = sum(1 for x in absent if not x['border'])  # «граница» — оборот < 1,2 × порога: мог пересечь порог внутри окна
    res['absent'] = sorted(absent, key=lambda x: -x['qv24'])
    json.dump(res, open(a.out, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ('absent', 'klines_missing', 'late_first')}, ensure_ascii=False))
    print('late_first (пересекли порог внутри окна):', [(x['symbol'], x['first_b_utc'], x['traded_min_before_first']) for x in res['late_first'][:12]])
    print('absent top:', [(x['symbol'], x['qv24']) for x in res['absent'][:12]])
    print('klines_missing:', len(res['klines_missing']), res['klines_missing'][:8])


if __name__ == '__main__':
    main()
