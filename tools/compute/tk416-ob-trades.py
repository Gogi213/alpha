#!/usr/bin/env python3
"""TK-416 п.4б: сверка книги ob200 Bybit (сентябрь, TK-415) со сделками Bybit (TK-417), по событиям (мс).

  file <zip> <сделки-корень>              — один инструмент×сутки, строка csv в stdout (отладка)
  run  <корень ob> <сделки> <out.csv> N   — все zip корня, N процессов, по строке на инструмент×сутки

Инвариант: сделка по цене p с тейкером Buy исполняется против аска p, Sell — против бида p, т.е. на момент ДО сделки уровень p
стоит в книге на стороне тейкера. Состояние «до сделки» = книга после всех событий с cts < t (cts — время матчинга, если нет — ts).
Сдвиг: если уровня p нет, ищем его в окне W мс назад (уровень удалён раньше t: книга «обогнала» сделку; последнее удаление) и вперёд
(уровень появился после t: книга «отстала»). Нет в окне → нет_в_книге; для них — в спреде / вне всей глубины ob200 / внутри глубины.
Посекундных агрегатов нет: каждая сделка и каждое событие книги разбираются поштучно."""
import sys, os, csv, gzip, json, zipfile, glob, re, datetime
from multiprocessing import Pool

DAY_MS = 86400000
W = 2000.0  # мс, окно поиска сдвига
EDGES = [10, 50, 200, 1000, 2000]
SH = ['<=%d' % e for e in EDGES]
FIELDS = ['рынок', 'инструмент', 'сутки', 'статус', 'сделок', 'buy', 'sell', 'до_книги', 'после_книги', 'в_книге', 'сдвиг_назад',
          'сдвиг_вперёд', 'нет_в_книге', 'нет_в_спреде', 'нет_вне_глубины', 'нет_внутри_глубины', 'нет_пустая_книга'] + \
         ['назад_' + s for s in SH] + ['вперёд_' + s for s in SH] + ['макс_сдвиг_мс', 'строк_книги', 'ошибок_разбора',
                                                                    'сделок_в_сутках_файла', 'cts_назад']

_spot_cache = {}


def day_start(day):
    return int(datetime.datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc).timestamp()) * 1000


def load_trades(troot, mkt, sym, day):
    """Сделки суток: (t_мс, цена, buy) отсортированно; None — файла нет."""
    d0 = day_start(day)
    ts, ps, bs = [], [], []
    if mkt == 'bybit-linear':
        p = os.path.join(troot, mkt, sym, '%s%s.csv.gz' % (sym, day))
        if not os.path.exists(p):
            return None
        with gzip.open(p, 'rt') as f:
            next(f)
            for ln in f:
                q = ln.split(',', 5)
                ts.append(float(q[0]) * 1000.0)
                bs.append(q[2] == 'Buy')
                ps.append(float(q[4]))
    else:
        p = os.path.join(troot, mkt, sym, '%s-%s.csv.gz' % (sym, day[:7]))
        if not os.path.exists(p):
            return None
        key = (p, day)
        with gzip.open(p, 'rt') as f:
            next(f)
            for ln in f:
                q = ln.split(',', 5)
                t = int(q[1])
                if d0 <= t < d0 + DAY_MS:
                    ts.append(float(t))
                    ps.append(float(q[2]))
                    bs.append(q[4] == 'buy')
    if any(ts[i] > ts[i + 1] for i in range(len(ts) - 1)):
        o = sorted(range(len(ts)), key=ts.__getitem__)
        ts, ps, bs = [ts[i] for i in o], [ps[i] for i in o], [bs[i] for i in o]
    return ts, ps, bs


def lines_of(fh):
    tail = b''
    while True:
        buf = fh.read(1 << 24)
        if not buf:
            break
        buf = tail + buf
        parts = buf.split(b'\n')
        tail = parts.pop()
        yield from parts
    if tail.strip():
        yield tail


def bucket(ad):
    for i, e in enumerate(EDGES):
        if ad <= e:
            return i
    return len(EDGES) - 1


def scan(path, mkt, sym, day, troot):
    r = dict.fromkeys(FIELDS, 0)
    r.update({'рынок': mkt, 'инструмент': sym, 'сутки': day, 'статус': 'ok'})
    tr = load_trades(troot, mkt, sym, day)
    if tr is None:
        r['статус'] = 'нет_файла_сделок'
        return r
    tt, tp, tb = tr
    n = len(tt)
    r['сделок'] = n
    r['buy'] = sum(tb)
    r['sell'] = n - r['buy']
    if n == 0:
        r['статус'] = 'нет_сделок'
        return r
    try:
        zf = zipfile.ZipFile(path)
        fh = zf.open(zf.namelist()[0])
    except Exception as e:
        r['статус'] = 'битый_zip:' + type(e).__name__
        return r
    loads = json.loads
    bids, asks = {}, {}
    gone_b, gone_a = {}, {}  # цена -> te последнего удаления уровня
    ti = 0
    pend = []  # [t, p, buy, bb, ba, lo, hi]
    n_line = n_err = 0
    prev_te = 0.0
    maxsh = 0.0
    c_in = c_back = c_fwd = c_none = c_pre = c_post = 0
    c_spread = c_out = c_inside = c_empty = 0
    back = [0] * len(EDGES)
    fwd = [0] * len(EDGES)
    cts_back = 0

    def finish(pd):
        nonlocal c_none, c_spread, c_out, c_inside, c_empty
        c_none += 1
        t, p, buy, bb, ba, lo, hi = pd
        if bb is None:
            c_empty += 1
        elif p < lo or p > hi:
            c_out += 1
        elif bb < p < ba:
            c_spread += 1
        else:
            c_inside += 1

    try:
        for raw in lines_of(fh):
            if not raw.strip():
                continue
            n_line += 1
            try:
                d = loads(raw)
                ts = d['ts']
                typ = d['type']
                dd = d['data']
            except Exception:
                n_err += 1
                continue
            te = float(d.get('cts') or ts)
            if te < prev_te:
                cts_back += 1
                te = prev_te
            prev_te = te
            # просроченные ожидания: окно вперёд закрыто
            if pend and pend[0][0] + W < te:
                keep = []
                for pd in pend:
                    if pd[0] + W < te:
                        finish(pd)
                    else:
                        keep.append(pd)
                pend = keep
            # сделки, для которых текущее состояние — «до сделки» (t <= te)
            while ti < n and tt[ti] <= te:
                t, p, buy = tt[ti], tp[ti], tb[ti]
                ti += 1
                if not bids and not asks:
                    c_pre += 1
                    continue
                side = asks if buy else bids
                if p in side:
                    c_in += 1
                    continue
                g = (gone_a if buy else gone_b).get(p)
                if g is not None and t - g <= W:
                    sh = t - g
                    c_back += 1
                    back[bucket(sh)] += 1
                    if sh > maxsh:
                        maxsh = sh
                    continue
                if bids and asks:
                    pend.append([t, p, buy, max(bids), min(asks), min(bids), max(asks)])
                else:
                    pend.append([t, p, buy, None, None, None, None])
            # применить событие
            if typ == 'snapshot':
                bids = {float(p): float(s) for p, s in dd.get('b', [])}
                asks = {float(p): float(s) for p, s in dd.get('a', [])}
            else:
                for p, s in dd.get('b', ()):
                    pf = float(p)
                    if float(s) == 0.0:
                        if bids.pop(pf, None) is not None:
                            gone_b[pf] = te
                    else:
                        bids[pf] = float(s)
                for p, s in dd.get('a', ()):
                    pf = float(p)
                    if float(s) == 0.0:
                        if asks.pop(pf, None) is not None:
                            gone_a[pf] = te
                    else:
                        asks[pf] = float(s)
            # ожидающие: уровень появился после сделки
            if pend:
                keep = []
                for pd in pend:
                    if pd[1] in (asks if pd[2] else bids):
                        sh = te - pd[0]
                        c_fwd += 1
                        fwd[bucket(sh)] += 1
                        if sh > maxsh:
                            maxsh = sh
                    else:
                        keep.append(pd)
                pend = keep
        for pd in pend:
            if pd[0] + W <= prev_te:
                finish(pd)
            else:
                c_post += 1
        c_post += n - ti
    except Exception as e:
        r['статус'] = 'обрыв_чтения:' + type(e).__name__
    if n_err and r['статус'] == 'ok':
        r['статус'] = 'ошибки_разбора'
    r['до_книги'], r['после_книги'], r['в_книге'] = c_pre, c_post, c_in
    r['сдвиг_назад'], r['сдвиг_вперёд'], r['нет_в_книге'] = c_back, c_fwd, c_none
    r['нет_в_спреде'], r['нет_вне_глубины'], r['нет_внутри_глубины'], r['нет_пустая_книга'] = c_spread, c_out, c_inside, c_empty
    for i, s in enumerate(SH):
        r['назад_' + s] = back[i]
        r['вперёд_' + s] = fwd[i]
    r['макс_сдвиг_мс'] = round(maxsh, 1)
    r['строк_книги'], r['ошибок_разбора'], r['cts_назад'] = n_line, n_err, cts_back
    r['сделок_в_сутках_файла'] = n
    return r


def key_of(path):
    m = re.match(r'(\d{4}-\d\d-\d\d)_(.+)_ob200\.data\.zip$', os.path.basename(path))
    return m.group(1), m.group(2)


def work(a):
    path, mkt, troot = a
    day, sym = key_of(path)
    try:
        return scan(path, mkt, sym, day, troot)
    except Exception as e:
        return dict.fromkeys(FIELDS, 0) | {'рынок': mkt, 'инструмент': sym, 'сутки': day, 'статус': 'сбой:' + repr(e)[:80]}


def cmd_file(path, troot):
    mkt = os.path.basename(os.path.dirname(os.path.dirname(path)))
    r = work((path, mkt, troot))
    for k in FIELDS:
        print('%s\t%s' % (k, r[k]))


def cmd_run(root, troot, out, nproc):
    jobs = []
    for mkt in sorted(os.listdir(root)):
        mp = os.path.join(root, mkt)
        if not os.path.isdir(mp):
            continue
        for sym in sorted(os.listdir(mp)):
            for p in glob.glob(os.path.join(mp, sym, '*_ob200.data.zip')):
                jobs.append((os.path.getsize(p), p, mkt))
    jobs.sort(reverse=True)
    part = out + '.part'
    done = set()
    if os.path.exists(part):
        with open(part, newline='') as f:
            for r in csv.DictReader(f):
                done.add((r['рынок'], r['инструмент'], r['сутки']))
    todo = [(p, m, troot) for _, p, m in jobs if (m, key_of(p)[1], key_of(p)[0]) not in done]
    print('файлов', len(jobs), 'готово', len(done), 'в работе', len(todo), flush=True)
    new = not os.path.exists(part) or not done
    with open(part, 'w' if new else 'a', newline='') as f, Pool(nproc) as pool:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for i, r in enumerate(pool.imap_unordered(work, todo, chunksize=1)):
            w.writerow(r)
            f.flush()
            if (i + 1) % 50 == 0:
                print(i + 1, '/', len(todo), flush=True)
    with open(part, newline='') as f:
        rows = sorted(csv.DictReader(f), key=lambda r: (r['рынок'], r['инструмент'], r['сутки']))
    with open(out + '.tmp', 'w', newline='') as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(out + '.tmp', out)
    print('готово', out, len(rows))


if __name__ == '__main__':
    if sys.argv[1] == 'file':
        cmd_file(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'run':
        cmd_run(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
    else:
        sys.exit(__doc__)
