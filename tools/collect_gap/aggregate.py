#!/usr/bin/env python3
"""Сборка матрицы «символ × минута» по файлам коллектора G-022 (book / trade / mark).

Читает <root>/<день>/<шард>-<запуск>.tsv.zst (формат — docs/collector-ws-format.md), для каждой строки b/t/m берёт
время приёма (rx, нс UTC: первая строка файла — абсолютное, дальше — разности) и считает записи по (символ, минута UTC).
Выход: <out>/<биржа>.npz — syms, minute0, kinds {book,trade,mark}: int32[nsym, nmin]; <out>/<биржа>-files.csv — опись файлов;
<out>/<биржа>-filesyms.json — по файлу: символ → [записей, первая минута, последняя минута].
Запуск: python3 aggregate.py --exchange bybit --root /data/turbo-probe/soak-bybit/data --out /data/collect-gap/agg --t-end <epoch s>
Только чтение исходных файлов; работает через `zstd -dc` (на живом файле последний кадр может быть неполным — это не ошибка).
"""
import argparse, glob, json, os, subprocess, sys, time, csv
from multiprocessing import Pool
import numpy as np

T0 = 1794528000  # 2026-11-13 00:00:00 UTC
KIND = {b'b': 0, b't': 1, b'm': 2}
SYM_IDX = {b'b': 4, b't': 4, b'm': 3}


def scan(args):
    path, minute0, nmin = args
    shard = os.path.basename(path).split('-')[0]
    kind = shard.rstrip('0123456789')
    t = 0
    first_ns = None
    last_ns = None
    nlines = 0
    cnt = {}  # sym -> list[int] длиной nmin
    p = subprocess.Popen(['zstd', '-dc', path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    mdiv = 60_000_000_000
    base_min = minute0
    first = True
    for line in p.stdout:
        f = line.split(b'\t', 5)
        if len(f) < 3:
            continue
        try:
            t += int(f[1])
        except ValueError:
            continue
        nlines += 1
        if first:
            first_ns = t
            first = False
        last_ns = t
        ty = f[0]
        if ty == b'b' or ty == b't':
            if len(f) < 5:
                continue
            sym = f[4]
        elif ty == b'm':
            if len(f) < 4:  # неполная последняя строка живого файла
                continue
            sym = f[3]
        else:
            continue
        m = t // mdiv - base_min
        if m < 0 or m >= nmin:
            continue
        a = cnt.get(sym)
        if a is None:
            a = cnt[sym] = [0] * nmin
        a[m] += 1
    p.stdout.close()
    rc = p.wait()
    res = {}
    summ = {}
    for k, v in cnt.items():
        arr = np.array(v, dtype=np.int32)
        res[k.decode()] = arr
        nz = np.nonzero(arr)[0]
        if len(nz):
            summ[k.decode()] = [int(arr.sum()), int(nz[0]), int(nz[-1])]  # всего записей, первая и последняя минута (от minute0)
    return path, shard, kind, nlines, first_ns, last_ns, rc, res, summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exchange', required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--t-end', type=int, required=True, help='конец окна, epoch с (округляется вниз до минуты)')
    ap.add_argument('--t-start', type=int, default=T0)
    ap.add_argument('--procs', type=int, default=3)
    ap.add_argument('--only', default='', help='подстрока имени файла (проба)')
    a = ap.parse_args()
    minute0 = a.t_start // 60
    nmin = a.t_end // 60 - minute0
    files = sorted(glob.glob(os.path.join(a.root, '2026-*', '*.tsv.zst')))
    files = [f for f in files if a.only in os.path.basename(f) and not os.path.basename(f).startswith('backfill-')]   # файлы дозапроса — не данные ws
    # дни до t_start пропускаем по имени каталога
    day_lo = time.strftime('%Y-%m-%d', time.gmtime(a.t_start))
    files = [f for f in files if os.path.basename(os.path.dirname(f)) >= day_lo]
    os.makedirs(a.out, exist_ok=True)
    print(f'{a.exchange}: {len(files)} files, nmin={nmin}', flush=True)
    syms = {}
    mats = {k: {} for k in ('book', 'trade', 'mark')}  # kind -> sym -> np.int32[nmin]
    shard_of = {k: {} for k in ('book', 'trade', 'mark')}  # sym -> set(шардов)
    inv = []
    filesyms = {}
    t00 = time.time()
    with Pool(a.procs) as pool:
        for i, (path, shard, kind, nl, f0, l0, rc, cnt, summ) in enumerate(pool.imap_unordered(scan, [(f, minute0, nmin) for f in files])):
            inv.append((path, shard, nl, f0, l0, rc))
            filesyms[os.path.relpath(path, a.root)] = summ
            for sym, arr in cnt.items():
                m = mats[kind].get(sym)
                if m is None:
                    mats[kind][sym] = arr
                else:
                    m += arr
                shard_of[kind].setdefault(sym, set()).add(shard)
            if i % 10 == 0:
                print(f'  {i + 1}/{len(files)} {os.path.basename(path)} lines={nl} rc={rc} t={time.time() - t00:.0f}s', flush=True)
    allsyms = sorted(set(mats['book']) | set(mats['trade']) | set(mats['mark']))
    out = {'syms': np.array(allsyms), 'minute0': np.array([minute0])}
    for k in mats:
        M = np.zeros((len(allsyms), nmin), dtype=np.int32)
        for j, s in enumerate(allsyms):
            if s in mats[k]:
                M[j] = mats[k][s]
        out[k] = M
        out[k + '_shard'] = np.array([','.join(sorted(shard_of[k].get(s, []))) for s in allsyms])
    np.savez_compressed(os.path.join(a.out, a.exchange + '.npz'), **out)
    with open(os.path.join(a.out, a.exchange + '-filesyms.json'), 'w') as fh:
        json.dump(filesyms, fh)
    with open(os.path.join(a.out, a.exchange + '-files.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['path', 'shard', 'lines', 'first_rx_ns', 'last_rx_ns', 'zstd_rc'])
        for r in sorted(inv):
            w.writerow(r)
    print(f'done {a.exchange}: {len(allsyms)} syms, {time.time() - t00:.0f}s', flush=True)


if __name__ == '__main__':
    main()
