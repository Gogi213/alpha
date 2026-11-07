#!/usr/bin/env python3
"""Независимый пересчёт доли одноминутных эпизодов (проверка пункта 1а). Формирование, сутки 2465..2478, панели .sap только чтение."""
import numpy as np, struct, sys, csv, os, time
from numpy.lib.stride_tricks import sliding_window_view
from multiprocessing import Pool
M0 = 3549600; NF = 20160
PAN = os.path.expanduser('~/xdl9/panels')
THETAS = (10, 20, 40, 80, 160)

def load_close(ex, sym):
    p = '%s/%s/%s.sap' % (PAN, ex, sym)
    with open(p, 'rb') as f:
        h = f.read(24)
    assert h[:4] == b'SAP1'
    first = struct.unpack('<q', h[8:16])[0]; n = struct.unpack('<Q', h[16:24])[0]
    lo, hi = max(M0, first), min(M0 + NF, first + n)
    c = np.full(NF, np.nan)
    if hi > lo:
        c[lo - M0:hi - M0] = np.fromfile(p, dtype='<f8', count=hi - lo, offset=24 + n * 8 + (lo - first) * 8)
    return c

def baseline(s):
    sp = np.concatenate([np.full(1440, np.nan), s])
    win = sliding_window_view(sp, 1440)[:NF:5]          # окно j покрывает исходные минуты [j-1440, j)
    cnt = np.isfinite(win).sum(axis=1)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        med = np.nanmedian(win, axis=1)
    med[cnt < 240] = np.nan
    return np.repeat(med, 5)[:NF]

def runs(flag, sgn, strict):
    """длины серий: strict - подряд идущие календарные минуты; иначе - подряд идущие ВАЛИДНЫЕ наблюдения (NaN-минуты пропускаются)"""
    idx = np.flatnonzero(flag)
    if idx.size == 0:
        return np.zeros(0, int)
    sg = sgn[idx]
    if strict:
        new = np.ones(idx.size, bool)
        new[1:] = (idx[1:] != idx[:-1] + 1) | (sg[1:] != sg[:-1])
    else:
        new = np.ones(idx.size, bool)
        new[1:] = sg[1:] != sg[:-1]                       # между флагованными валидными наблюдениями других валидных нет
    # нестрогий вариант: серия рвётся, если между idx[i-1] и idx[i] есть валидное наблюдение с флагом 0 (т.е. |dev|<theta)
    return idx, new

def work(job):
    key, ex1, s1, ex2, s2 = job
    try:
        cA = load_close(ex1, s1); cB = load_close(ex2, s2)
    except Exception as e:
        return (key, ex1, ex2, 'ERR %s' % e, None)
    v = np.isfinite(cA) & np.isfinite(cB) & (cA > 0) & (cB > 0)
    s = np.full(NF, np.nan)
    s[v] = 1e4 * np.log(cA[v] / cB[v])
    b = baseline(s)
    dev = s - b
    ok = v & np.isfinite(b)
    out = []
    validpos = np.flatnonzero(v)                            # позиции валидных минут
    rank = np.cumsum(v) - 1                                 # порядковый номер валидного наблюдения
    for th in THETAS:
        flag = ok & (np.abs(dev) >= th)
        idx = np.flatnonzero(flag)
        if idx.size == 0:
            out.append((th, 0, 0, 0, 0, 0, 0, 0)); continue
        sg = np.sign(dev[idx])
        # строгий: календарные минуты
        n1 = np.ones(idx.size, bool); n1[1:] = (idx[1:] != idx[:-1] + 1) | (sg[1:] != sg[:-1])
        st1 = np.flatnonzero(n1); D1 = np.diff(np.append(st1, idx.size))
        # по валидным наблюдениям: соседние флагованные позиции подряд идущие среди валидных (rank разница 1)
        rk = rank[idx]
        n2 = np.ones(idx.size, bool); n2[1:] = (rk[1:] != rk[:-1] + 1) | (sg[1:] != sg[:-1])
        st2 = np.flatnonzero(n2); D2 = np.diff(np.append(st2, idx.size))
        out.append((th, len(D1), int((D1 == 1).sum()), int((D1 >= 3).sum()), len(D2), int((D2 == 1).sum()), int((D2 >= 3).sum()), int(flag.sum())))
    return (key, ex1, ex2, 'ok', dict(nvalid=int(v.sum()), nA=int(np.isfinite(cA).sum()), nB=int(np.isfinite(cB).sum()), rows=out))

if __name__ == '__main__':
    t0 = time.time()
    rows = list(csv.DictReader(open(os.path.expanduser('~/xdl9/audit3/hints/crypto/pairs_all.csv'), encoding='utf-8')))
    jobs = [(r['key'], r['ex1'], r['sym1'], r['ex2'], r['sym2']) for r in rows if r['verdict'] != 'не в asset_pairs']
    sys.stderr.write('pairs %d\n' % len(jobs))
    outp = sys.argv[1]
    with Pool(int(sys.argv[2]) if len(sys.argv) > 2 else 2) as pool, open(outp, 'w', encoding='utf-8', newline='') as fo:
        w = csv.writer(fo)
        w.writerow(['key', 'ex1', 'ex2', 'status', 'nvalid', 'nA', 'nB', 'theta', 'ep_strict', 'd1_strict', 'ge3_strict', 'ep_valid', 'd1_valid', 'ge3_valid', 'minutes_flag'])
        for i, (key, ex1, ex2, st, d) in enumerate(pool.imap(work, jobs, chunksize=8)):
            if d is None:
                w.writerow([key, ex1, ex2, st]); continue
            for r in d['rows']:
                w.writerow([key, ex1, ex2, st, d['nvalid'], d['nA'], d['nB']] + list(r))
            if i % 100 == 0:
                sys.stderr.write('%d %.0f c\n' % (i, time.time() - t0))
    sys.stderr.write('done %.0f c\n' % (time.time() - t0))
