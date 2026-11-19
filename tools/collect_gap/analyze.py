#!/usr/bin/env python3
"""Покрытие «символ × минута»: записи коллектора (aggregate.py) против REST-минуток (klines.py).

«Рынок торговал в минуту» = объём REST-свечи > 0. Дыра книги = торговая минута без единой записи `b` по символу
(сделка всегда меняет лучший уровень, так что запись книги в этой минуте обязана быть). Дыра сделок — без записи `t`.
Минута — по приёму (rx) у записи и по времени открытия свечи у REST; граница минуты даёт единичные ложные дыры,
поэтому рядом считается «мягкая» дыра (нет записи в m−1, m и m+1); участки ≥ 5 мин от этого не зависят.

Классы участка тишины (≥ 5 подряд минут без `b`, с хотя бы одной торговой минутой REST):
  absent — символ за весь запуск процесса не дал ни одной записи книги (его не было в подписке процесса: пул считается
           один раз при старте; qv24 на старте запуска, из REST, сравнивается с порогом пула);
  stall  — запуск процесса записи по символу давал, потом тишина (или тишина с начала при начавшихся записях позже);
  mixed  — участок пересекает оба случая.
Выход: <out>/<биржа>/{summary.json, stretches-book.csv, stretches-trade.csv, absent-runs.csv, shard-outages.csv, minutes.csv}.
Запуск: python3 analyze.py --exchange bybit --agg /data/collect-gap/agg --klines /data/collect-gap/klines --out /data/collect-gap/out
"""
import argparse, calendar, csv, glob, json, os, re, time
import numpy as np

MIN_QV = 300000.0  # порог пула G-022 (runner.rs, --min-quote-volume)
STRETCH_MIN = 5


def ts(minute_abs):
    return time.strftime('%Y-%m-%dT%H:%M', time.gmtime(minute_abs * 60))


def load_klines(kdir, ex):
    parts = sorted(glob.glob(os.path.join(kdir, f'{ex}-klines-p*.npz')))
    if not parts:
        return None
    rows = {}  # символ -> (ok, vol, qv); при повторе берётся строка с ok
    m0 = None
    for p in parts:
        d = np.load(p)
        if m0 is None:
            m0 = int(d['minute0'][0])
        assert int(d['minute0'][0]) == m0
        for j, s in enumerate(d['syms']):
            s = str(s)
            if s not in rows or (bool(d['ok'][j]) and not rows[s][0]):
                rows[s] = (bool(d['ok'][j]), d['vol'][j], d['qv'][j])
    syms = sorted(rows)
    return {'syms': syms, 'vol': np.stack([rows[s][1] for s in syms]), 'qv': np.stack([rows[s][2] for s in syms]),
            'ok': np.array([rows[s][0] for s in syms]), 'minute0': m0}


def load_top_excluded(path):
    """{epoch старта (с): [символы топа]} из журнала юнита."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    rx = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\+00:00 .*?: pool (\d+) top_excluded (\[.*\])\s*$')
    for line in open(path, encoding='utf-8', errors='replace'):
        m = rx.match(line.rstrip())
        if m:
            t = calendar.timegm(time.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S'))
            out[t] = (int(m.group(2)), json.loads(m.group(3)))
    return out


def runs_of(mask):
    """Максимальные отрезки True: [(a, b)] включительно."""
    if not mask.any():
        return []
    x = np.concatenate(([0], mask.astype(np.int8), [0]))
    d = np.diff(x)
    a = np.nonzero(d == 1)[0]
    b = np.nonzero(d == -1)[0] - 1
    return list(zip(a.tolist(), b.tolist()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exchange', required=True)
    ap.add_argument('--agg', required=True)
    ap.add_argument('--klines', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--journal', default='', help='journalctl юнита (short-iso), строки "pool N top_excluded [...]" — исключённый топ по запускам')
    a = ap.parse_args()
    ex = a.exchange
    odir = os.path.join(a.out, ex)
    os.makedirs(odir, exist_ok=True)

    d = np.load(os.path.join(a.agg, ex + '.npz'))
    asyms = [s for s in d['syms']]
    M0 = int(d['minute0'][0])
    book, trade = d['book'], d['trade']
    bshard = [str(s) for s in d['book_shard']]
    nmin = book.shape[1]
    K = load_klines(a.klines, ex)
    kidx = {s: i for i, s in enumerate(K['syms'])}
    off = M0 - K['minute0']
    assert off >= 0
    # --- запуски процесса (по именам файлов) и шарды книги по запускам
    fs = json.load(open(os.path.join(a.agg, ex + '-filesyms.json'))) if os.path.exists(os.path.join(a.agg, ex + '-filesyms.json')) else {}
    run_starts = sorted({int(re.search(r'-(\d+)\.tsv\.zst$', f).group(1)) for f in fs})
    run_min = [max(0, s // 60 - M0) for s in run_starts]  # минута начала запуска на сетке
    run_end = run_min[1:] + [nmin]
    # (запуск, символ) -> шард книги
    sh_of = {}
    for f, summ in fs.items():
        m = re.match(r'.*?/?(book\d+)-(\d+)\.tsv\.zst$', f)
        if not m:
            continue
        for s in summ:
            sh_of[(int(m.group(2)), s)] = m.group(1)
    # --- окно
    tot_book = book.sum(0)
    w0 = int(np.nonzero(tot_book)[0][0]) + 2
    w1 = nmin
    nsym = len(asyms)
    # объёмы REST на сетку агрегатора
    vol = np.full((nsym, nmin), -1.0)
    qv = np.full((nsym, nmin), -1.0, dtype=np.float32)
    have_rest = np.zeros(nsym, dtype=bool)
    for i, s in enumerate(asyms):
        j = kidx.get(s)
        if j is None:
            continue
        have_rest[i] = bool(K['ok'][j])
        vol[i] = K['vol'][j, off:off + nmin]
        qv[i] = K['qv'][j, off:off + nmin]
    traded = vol > 0
    # первая минута существования символа по REST (свеча есть)
    exists = vol >= 0
    first_exist = np.where(exists.any(1), exists.argmax(1), nmin)
    win = np.zeros(nmin, dtype=bool)
    win[w0:w1] = True
    sel = have_rest  # символы с полным ответом REST
    # --- запуск, в который попадает минута
    run_of_min = np.zeros(nmin, dtype=int)
    for k, (m0, m1) in enumerate(zip(run_min, run_end)):
        run_of_min[m0:m1] = k
    # присутствие символа в запуске (записи книги за весь запуск) и qv24 на старте запуска
    present = np.zeros((nsym, len(run_min)), dtype=bool)
    symidx = {sy: i for i, sy in enumerate(asyms)}
    for f, summ in fs.items():  # присутствие — по файлам запуска (а не по минутам: в минуте старта ещё дописывает прежний процесс)
        mm = re.match(r'.*?/?(book\d+)-(\d+)\.tsv\.zst$', f)
        if not mm:
            continue
        k = run_starts.index(int(mm.group(2)))
        for sy, (n, _a, _b) in summ.items():
            if n > 0 and sy in symidx:
                present[symidx[sy], k] = True
    kq = np.cumsum(np.maximum(K['qv'], 0).astype(np.float64), axis=1)
    kq = np.concatenate([np.zeros((kq.shape[0], 1)), kq], axis=1)  # kq[:, t] = сумма до минуты t (не включая)

    def qv24_lb(sym, m):
        j = kidx.get(sym)
        if j is None:
            return None, False
        t = m + off
        lo = max(0, t - 1440)
        full = (t - 1440) >= 0
        return float(kq[j, t] - kq[j, lo]), full

    # --- метрики по минутам
    bk = book > 0
    tr = trade > 0
    T = traded & win[None, :] & sel[:, None]
    # торговая минута учитывается только начиная с первой свечи символа
    for i in range(nsym):
        T[i, :first_exist[i]] = False
    n_traded = int(T.sum())
    hole_b = T & ~bk
    bk_soft = bk.copy()
    bk_soft[:, 1:] |= bk[:, :-1]
    bk_soft[:, :-1] |= bk[:, 1:]
    hole_b_soft = T & ~bk_soft
    hole_t = T & ~tr
    tr_soft = tr.copy()
    tr_soft[:, 1:] |= tr[:, :-1]
    tr_soft[:, :-1] |= tr[:, 1:]
    hole_t_soft = T & ~tr_soft
    summary = {
        'exchange': ex, 'window_utc': [ts(M0 + w0), ts(M0 + w1)], 'minutes': w1 - w0,
        'symbols_total': nsym, 'symbols_rest_ok': int(sel.sum()), 'symbols_rest_missing': [asyms[i] for i in range(nsym) if not sel[i]][:50],
        'traded_symbol_minutes': n_traded,
        'book_hole_minutes': int(hole_b.sum()), 'book_hole_pct': round(100 * hole_b.sum() / max(1, n_traded), 4),
        'book_hole_soft_minutes': int(hole_b_soft.sum()), 'book_hole_soft_pct': round(100 * hole_b_soft.sum() / max(1, n_traded), 4),
        'trade_hole_minutes': int(hole_t.sum()), 'trade_hole_pct': round(100 * hole_t.sum() / max(1, n_traded), 4),
        'trade_hole_soft_minutes': int(hole_t_soft.sum()), 'trade_hole_soft_pct': round(100 * hole_t_soft.sum() / max(1, n_traded), 4),
        'runs': [{'start_utc': ts(s // 60), 'start_epoch': s} for s in run_starts],
    }

    # --- участки тишины книги / сделок
    def stretches(silent, kind):
        rows = []
        for i in range(nsym):
            if not sel[i]:
                continue
            row = silent[i].copy()
            row[:max(first_exist[i], w0)] = False
            row[w1:] = False
            for (x, y) in runs_of(row):
                n = y - x + 1
                if n < STRETCH_MIN:
                    continue
                tm = int(traded[i, x:y + 1].sum())
                if tm == 0:
                    continue  # естественная тишина: рынок не торговал
                # класс по запускам
                ks = np.unique(run_of_min[x:y + 1])
                absent_min = int(sum(((run_of_min[x:y + 1] == k).sum()) for k in ks if not present[i, k]))
                cls = 'absent' if absent_min == n else ('stall' if absent_min == 0 else 'mixed')
                k0 = run_of_min[x]
                shard = sh_of.get((run_starts[k0], asyms[i]), '')
                rows.append({'symbol': asyms[i], 'start_utc': ts(M0 + x), 'end_utc': ts(M0 + y), 'len_min': n, 'traded_min': tm,
                             'rest_quote_usd': round(float(np.maximum(qv[i, x:y + 1], 0).sum()), 1), 'class': cls,
                             'runs': ','.join(ts(run_starts[k] // 60) for k in ks), 'shard': shard, 'x': x, 'y': y})
        return rows

    sb = stretches(~bk, 'book')
    st = stretches(~tr, 'trade')
    for name, rows in (('book', sb), ('trade', st)):
        with open(os.path.join(odir, f'stretches-{name}.csv'), 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['symbol', 'start_utc', 'end_utc', 'len_min', 'traded_min', 'rest_quote_usd', 'class', 'runs', 'shard'])
            for r in sorted(rows, key=lambda r: (-r['len_min'], r['symbol'])):
                w.writerow([r[k] for k in ('symbol', 'start_utc', 'end_utc', 'len_min', 'traded_min', 'rest_quote_usd', 'class', 'runs', 'shard')])
        mins = sum(r['len_min'] for r in rows)
        summary[f'stretches_{name}'] = {
            'count': len(rows), 'minutes': mins, 'symbols': len({r['symbol'] for r in rows}),
            'by_class': {c: {'count': sum(1 for r in rows if r['class'] == c), 'minutes': sum(r['len_min'] for r in rows if r['class'] == c)} for c in ('absent', 'stall', 'mixed')},
            'len_buckets': {lab: sum(1 for r in rows if lo <= r['len_min'] < hi) for lab, lo, hi in (('5-14', 5, 15), ('15-59', 15, 60), ('60-239', 60, 240), ('240+', 240, 10 ** 9))},
            'traded_minutes_inside': sum(r['traded_min'] for r in rows),
        }
    # доля дыр, входящих в участки ≥ 5 мин
    in_str = np.zeros_like(hole_b)
    for r in sb:
        in_str[asyms.index(r['symbol']), r['x']:r['y'] + 1] = True
    summary['book_hole_minutes_in_stretches'] = int((hole_b & in_str).sum())
    summary['book_hole_minutes_short'] = int((hole_b & ~in_str).sum())
    sh_idx = np.argwhere(hole_b & ~in_str)
    with open(os.path.join(odir, 'short-holes.csv'), 'w', newline='') as fh:  # дыры книги вне участков ≥ 5 мин: (символ, минута)
        w = csv.writer(fh)
        w.writerow(['symbol', 'minute_utc', 'soft_hole'])
        for i, m in sh_idx[:20000]:
            w.writerow([asyms[i], ts(M0 + int(m)), int(hole_b_soft[i, m])])
    shs = {}
    for i, m in sh_idx:
        shs.setdefault(ts(M0 + int(m))[:13], set()).add(asyms[i])
    summary['short_holes_by_hour'] = {h: len(v) for h, v in sorted(shs.items())}
    summary['short_holes_soft'] = int(sum(1 for i, m in sh_idx if hole_b_soft[i, m]))

    # --- (запуск, символ): нет записей за весь запуск — причина по qv24 на старте
    ar = []
    topx = load_top_excluded(a.journal)
    run_top = {}
    for k, st0 in enumerate(run_starts):
        near = [t for t in topx if abs(t - st0) <= 60]
        run_top[k] = set(topx[min(near, key=lambda t: abs(t - st0))][1]) if near else set()
    summary['pool_size_by_run'] = {ts(run_starts[k] // 60): topx[min([t for t in topx if abs(t - run_starts[k]) <= 60], key=lambda t: abs(t - run_starts[k]))][0] for k in range(len(run_starts)) if [t for t in topx if abs(t - run_starts[k]) <= 60]}
    for k, (m0, m1) in enumerate(zip(run_min, run_end)):
        lo, hi = max(m0, w0), min(m1, w1)
        if hi <= lo:
            continue
        for i in range(nsym):
            if not sel[i] or present[i, k]:
                continue
            tm = int((traded[i, lo:hi] & (np.arange(lo, hi) >= first_exist[i])).sum())
            if tm == 0:
                continue
            q, full = qv24_lb(asyms[i], run_starts[k] // 60 - M0)
            if asyms[i] in run_top[k]:
                verdict = 'top_excluded'  # крупнейшие по обороту не берутся в пул Binance/Bybit (default_top_exclude = 10)
            elif q is not None and q >= MIN_QV:
                verdict = 'above_threshold'  # оборот ≥ порога даже по неполным суткам: в пуле быть обязан → не подписан / молчит
            elif full:
                verdict = 'below_threshold'
            else:
                verdict = 'unknown(no 24h history)'
            ar.append({'run_start_utc': ts(run_starts[k] // 60), 'symbol': asyms[i], 'minutes_in_window': hi - lo, 'traded_min': tm,
                       'qv24_at_start': None if q is None else round(q), 'qv24_over_threshold': None if q is None else round(q / MIN_QV, 3), 'qv24_full_history': full, 'verdict': verdict,
                       'qv_during_run': round(float(np.maximum(qv[i, lo:hi], 0).sum()))})
    with open(os.path.join(odir, 'absent-runs.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['run_start_utc', 'symbol', 'minutes_in_window', 'traded_min', 'qv24_at_start', 'qv24_over_threshold', 'qv24_full_history', 'verdict', 'qv_during_run'])
        for r in sorted(ar, key=lambda r: (r['run_start_utc'], -r['traded_min'])):
            w.writerow([r[k] for k in ('run_start_utc', 'symbol', 'minutes_in_window', 'traded_min', 'qv24_at_start', 'qv24_over_threshold', 'qv24_full_history', 'verdict', 'qv_during_run')])
    # доля дыр по обороту: REST-оборот торговых минут без записи книги против всего REST-оборота окна
    qpos = np.maximum(qv, 0).astype(np.float64)
    tot_usd = float(qpos[sel][:, w0:w1].sum()) if sel.any() else 0.0
    hole_usd = float((qpos * hole_b).sum())
    summary['book_hole_usd'] = {'hole_quote_usd': round(hole_usd), 'total_quote_usd': round(tot_usd), 'pct': round(100 * hole_usd / max(1.0, tot_usd), 4)}
    # дыры по запускам: сколько символов выпало из подписки запуска и сколько торговало
    byrun = {}
    for r in ar:
        e = byrun.setdefault(r['run_start_utc'], {'absent_symbols_traded': 0, 'traded_minutes': 0, 'quote_usd': 0})
        e['absent_symbols_traded'] += 1
        e['traded_minutes'] += r['traded_min']
        e['quote_usd'] += r['qv_during_run']
    summary['absent_by_run'] = byrun
    bk_edges = [(0, 3e5, '<300k'), (3e5, 1e6, '300k-1M'), (1e6, 1e7, '1M-10M'), (1e7, 1e18, '>=10M')]
    bq = {lab: {'pairs': 0, 'traded_min': 0, 'quote_usd': 0} for _, _, lab in bk_edges}
    for r in ar:
        q0 = r['qv24_at_start']
        if q0 is None:
            continue
        for lo, hi, lab in bk_edges:
            if lo <= q0 < hi:
                bq[lab]['pairs'] += 1
                bq[lab]['traded_min'] += r['traded_min']
                bq[lab]['quote_usd'] += r['qv_during_run']
    summary['absent_by_qv24_bucket'] = bq
    summary['absent_runs'] = {
        'pairs': len(ar), 'by_verdict': {v: sum(1 for r in ar if r['verdict'] == v) for v in ('top_excluded', 'below_threshold', 'above_threshold', 'unknown(no 24h history)')},
        'traded_min_by_verdict': {v: sum(r['traded_min'] for r in ar if r['verdict'] == v) for v in ('top_excluded', 'below_threshold', 'above_threshold', 'unknown(no 24h history)')},
        'above_threshold_within_5pct': sum(1 for r in ar if r['verdict'] == 'above_threshold' and r['qv24_over_threshold'] <= 1.05),  # в пределах точности оценки qv24 (минутные суммы против тикера биржи)
    }

    # --- одновременность: шард-вылеты (доля торговавших символов шарда без записей книги в минуту)
    out_rows = []
    for k, (m0, m1) in enumerate(zip(run_min, run_end)):
        shards = {}
        for i in range(nsym):
            if present[i, k]:
                shards.setdefault(sh_of.get((run_starts[k], asyms[i]), '?'), []).append(i)
        for shname, idx in shards.items():
            idx = np.array(idx)
            lo, hi = max(m0, w0), min(m1, w1)
            if hi <= lo:
                continue
            tr_n = T[idx][:, lo:hi].sum(0)
            hole_n = hole_b_soft[idx][:, lo:hi].sum(0)
            frac = np.where(tr_n >= 3, hole_n / np.maximum(tr_n, 1), 0.0)
            for (x, y) in runs_of(frac >= 0.5):
                out_rows.append({'run': ts(run_starts[k] // 60), 'shard': shname, 'start_utc': ts(M0 + lo + x), 'end_utc': ts(M0 + lo + y), 'len_min': y - x + 1,
                                 'shard_symbols': len(idx), 'max_symbols_traded_silent': int(hole_n[x:y + 1].max())})
    with open(os.path.join(odir, 'shard-outages.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['run', 'shard', 'start_utc', 'end_utc', 'len_min', 'shard_symbols', 'max_symbols_traded_silent'])
        for r in sorted(out_rows, key=lambda r: (r['start_utc'], r['shard'])):
            w.writerow([r[k] for k in ('run', 'shard', 'start_utc', 'end_utc', 'len_min', 'shard_symbols', 'max_symbols_traded_silent')])
    summary['shard_outages_ge50pct'] = {'count': len(out_rows), 'minutes': sum(r['len_min'] for r in out_rows)}

    # --- поминутный ряд: сколько символов торговало, сколько из них без книги / без сделок
    with open(os.path.join(odir, 'minutes.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['minute_utc', 'traded_syms', 'no_book', 'no_book_soft', 'no_trade', 'no_trade_soft'])
        for m in range(w0, w1):
            w.writerow([ts(M0 + m), int(T[:, m].sum()), int(hole_b[:, m].sum()), int(hole_b_soft[:, m].sum()), int(hole_t[:, m].sum()), int(hole_t_soft[:, m].sum())])
    nb = hole_b_soft[:, w0:w1].sum(0)
    summary['simultaneous_no_book_soft_per_minute'] = {'p50': float(np.percentile(nb, 50)), 'p90': float(np.percentile(nb, 90)), 'p99': float(np.percentile(nb, 99)), 'max': int(nb.max())}
    json.dump(summary, open(os.path.join(odir, 'summary.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:6000])


if __name__ == '__main__':
    main()
