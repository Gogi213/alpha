#!/usr/bin/env python3
"""TK-416: валидация архивов стакана Bybit за 2026-09 (ob200 .data.zip, TK-415).

  file <zip> <свечи-каталог>            — один инструмент×сутки, строка csv в stdout (отладка)
  run  <корень ob> <свечи> <out.csv> N  — все zip корня, N процессов, по строке на инструмент×сутки
  cover <корень ob> <свечи> <heads.tsv,..> <valid.csv> <out.csv> — охват: инструмент×сутки × статус

Событийно: каждая строка потока разбирается, книга восстанавливается (snapshot/delta), посекундных агрегатов нет.
Свечи 1 м — только как внешний эталон для сверки минутной границы (TK-411).
"""
import sys, os, io, csv, gzip, json, zipfile, glob, re, collections
from multiprocessing import Pool

DAY_MS = 86400000
GAP_EDGES = [100, 200, 500, 1000, 5000, 60000]  # мс: <=edge; последняя корзина > 60 с
TOPN = 20
FIELDS = ['рынок', 'инструмент', 'сутки', 'статус', 'байт_zip', 'строк', 'снапшотов', 'дельт', 'ошибок_разбора',
          'первое_мс_от_начала', 'последнее_мс_до_конца', 'ts_назад', 'u_назад', 'u_разрывов', 'u_сброс_1', 'seq_назад',
          'снапшот_сбросов', 'снапшот_расхожд_уровней', 'удалений_отсутствующих', 'пустая_сторона', 'пересечений',
          'касаний', 'первое_пересечение_мс', 'макс_разрыв_мс'] + \
         ['разрывов_<=%d' % e for e in GAP_EDGES] + ['разрывов_>60000', 'мин_свеч', 'мин_сверено', 'мин_вне_книги',
          'мин_в_разрыве', 'нарушений_hl', 'нарушений_hl_>1бп', 'нарушений_hl_>10бп', 'макс_нарушение_бп',
          'тик', 'нарушений_hl_>1тика', 'макс_нарушение_тиков', 'u_первый', 'u_последний', 'seq_первый', 'seq_последний', 'ts_первый', 'ts_последний',
          'close_медиана_бп', 'close_p95_бп', 'close_p99_бп', 'close_макс_бп', 'close_>10бп', 'close_>50бп']

DEBUG = bool(os.environ.get('VAL_DEBUG'))
DEEP = bool(os.environ.get('VAL_DEEP'))  # выборка: сверка hl с глубиной книги (весь ob200, не только лучшие цены); дорого (O(200) на событие)
if DEEP:
    FIELDS = FIELDS + ['hl_глуб_нарушений', 'hl_глуб_>1тика', 'hl_глуб_>10бп', 'hl_глуб_макс_бп']


def load_candles(cdir, mkt, sym, d0):
    """Свечи только за сутки d0: без кэша по инструменту (кэш на месяц × 67 инструментов × 15 процессов = oom, TK-416)."""
    p = os.path.join(cdir, mkt, sym + '.csv.gz')
    out = {}
    if os.path.exists(p):
        with gzip.open(p, 'rt') as f:
            rd = csv.reader(f)
            next(rd, None)
            for r in rd:
                t = int(r[0])
                if d0 <= t < d0 + DAY_MS:
                    out[t] = (float(r[2]), float(r[3]), float(r[4]))  # h, l, c
    return out


def pct(v, q):
    if not v:
        return ''
    s = sorted(v)
    return round(s[min(len(s) - 1, int(q * len(s)))], 3)


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


def scan(path, mkt, sym, day, cdir):
    r = dict.fromkeys(FIELDS, 0)
    r.update({'рынок': mkt, 'инструмент': sym, 'сутки': day, 'статус': 'ok', 'байт_zip': os.path.getsize(path)})
    d0 = int(__import__('datetime').datetime.strptime(day, '%Y-%m-%d').replace(
        tzinfo=__import__('datetime').timezone.utc).timestamp()) * 1000
    cnd = load_candles(cdir, mkt, sym, d0)
    try:
        zf = zipfile.ZipFile(path)
        names = zf.namelist()
        if not names:
            r['статус'] = 'пустой_zip'
            return r
        fh = zf.open(names[0])
    except Exception as e:
        r['статус'] = 'битый_zip:' + type(e).__name__
        return r
    loads = json.loads
    bids, asks = {}, {}
    gaps = [0] * (len(GAP_EDGES) + 1)
    prev_ts = prev_u = prev_seq = None
    first_ts = last_ts = None
    cur_m = None
    bbmin = bamax = None
    dlo = dhi = None
    deep_v, deep_vt = [], []
    last_mid = None
    n_line = n_snap = n_delta = n_err = 0
    crossed = locked = 0
    close_d, viol, viol_t = [], [], []
    n_cm = n_cmp = n_out = n_gapmin = 0
    maxgap = 0
    tick = None
    last_u = last_seq = None
    first_cross = ''
    bb = ba = None
    cmin_in_file = {m for m in cnd if d0 <= m < d0 + DAY_MS}
    done_m = set()

    def fin(m, bbmin_, bamax_, mid_, dlo_=None, dhi_=None):
        nonlocal n_cmp
        c = cnd.get(m * 60000)
        if c is None:
            return
        h, l, cl = c
        done_m.add(m * 60000)
        n_cmp += 1
        mid = mid_ or cl
        v = max(0.0, bbmin_ - l, h - bamax_) / mid * 1e4
        viol.append(v)
        if DEEP and dlo_ is not None:
            dv = max(0.0, dlo_ - l, h - dhi_)
            deep_v.append(dv / mid * 1e4)
            if tick and dv > 1.5 * tick:
                deep_vt.append(dv / tick)
        vt = max(0.0, bbmin_ - l, h - bamax_)
        if tick and vt > 1.5 * tick:
            viol_t.append(vt / tick)
        if DEBUG and len(viol) <= 40:
            print('DBG', m, 'bbmin', bbmin_, 'bamax', bamax_, 'mid', mid_, 'candle h/l/c', h, l, cl, 'v', round(v, 2), file=sys.stderr)
        close_d.append(abs(mid_ - cl) / cl * 1e4)

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
            u = dd.get('u')
            seq = dd.get('seq')
            if first_ts is None:
                first_ts = ts
                r['u_первый'], r['seq_первый'] = u, seq
            if prev_ts is not None:
                g = ts - prev_ts
                if g < 0:
                    r['ts_назад'] += 1
                elif g > 100:
                    if g > maxgap:
                        maxgap = g
                    for i, e in enumerate(GAP_EDGES):
                        if g <= e:
                            gaps[i] += 1
                            break
                    else:
                        gaps[-1] += 1
                else:
                    gaps[0] += 1
            m = ts // 60000
            if cur_m is None:
                cur_m = m
            elif m != cur_m and bids and asks:
                fin(cur_m, bbmin, bamax, last_mid, dlo, dhi)
                if m - cur_m > 1:
                    n_gapmin += m - cur_m - 1
                cur_m = m
                bbmin, bamax = bb, ba
                dlo, dhi = (min(bids), max(asks)) if DEEP else (None, None)
            if typ == 'snapshot':
                n_snap += 1
                if n_snap > 1 or bids:
                    r['снапшот_сбросов'] += 1
                    nb = sorted(((float(p), float(s)) for p, s in dd.get('b', [])), reverse=True)[:TOPN]
                    na = sorted((float(p), float(s)) for p, s in dd.get('a', []))[:TOPN]
                    ob = sorted(bids.items(), reverse=True)[:TOPN]
                    oa = sorted(asks.items())[:TOPN]
                    r['снапшот_расхожд_уровней'] += len(set(nb) ^ set(ob)) + len(set(na) ^ set(oa))
                bids = {float(p): float(s) for p, s in dd.get('b', [])}
                asks = {float(p): float(s) for p, s in dd.get('a', [])}
                if tick is None:
                    ds = [b - a for L in (sorted(bids), sorted(asks)) for a, b in zip(L, L[1:]) if b > a]
                    tick = min(ds) if ds else None
                if DEBUG:
                    print('SNAP', ts, 'u', u, 'seq', seq, 'prev_u', prev_u, 'nb', len(dd.get('b', [])), 'na', len(dd.get('a', [])), file=sys.stderr)
                if u is not None and prev_u is not None:
                    if u < prev_u:
                        r['u_назад'] += 1
                    elif u > prev_u + 1:
                        r['u_разрывов'] += 1
                    r['u_сброс_1'] += (u == 1)
            else:
                n_delta += 1
                if not bids and not asks and n_snap == 0:
                    r['статус'] = 'дельта_без_снапшота'
                for p, s in dd.get('b', ()):
                    pf = float(p)
                    if float(s) == 0.0:
                        if bids.pop(pf, None) is None:
                            r['удалений_отсутствующих'] += 1
                    else:
                        bids[pf] = float(s)
                for p, s in dd.get('a', ()):
                    pf = float(p)
                    if float(s) == 0.0:
                        if asks.pop(pf, None) is None:
                            r['удалений_отсутствующих'] += 1
                    else:
                        asks[pf] = float(s)
                if u is not None and prev_u is not None:
                    if u <= prev_u:
                        r['u_назад'] += 1
                    elif u != prev_u + 1:
                        r['u_разрывов'] += 1
            if u is not None:
                prev_u = u
            if seq is not None:
                if prev_seq is not None and seq < prev_seq:
                    r['seq_назад'] += 1
                prev_seq = seq
            prev_ts = ts
            last_ts = ts
            last_u, last_seq = u, seq
            if bids and asks:
                bb = max(bids)
                ba = min(asks)
                if bb > ba:
                    crossed += 1
                    if not first_cross:
                        first_cross = ts - d0
                elif bb == ba:
                    locked += 1
                if bbmin is None:
                    bbmin, bamax = bb, ba
                else:
                    if bb < bbmin:
                        bbmin = bb
                    if ba > bamax:
                        bamax = ba
                if DEEP:
                    lo_, hi_ = min(bids), max(asks)
                    if dlo is None or lo_ < dlo:
                        dlo = lo_
                    if dhi is None or hi_ > dhi:
                        dhi = hi_
                last_mid = (bb + ba) / 2
            else:
                r['пустая_сторона'] += 1
        if cur_m is not None and bids and asks:
            fin(cur_m, bbmin, bamax, last_mid, dlo, dhi)
    except Exception as e:
        r['статус'] = 'обрыв_чтения:' + type(e).__name__
    if n_err and r['статус'] == 'ok':
        r['статус'] = 'ошибки_разбора'
    if n_line == 0 and r['статус'] == 'ok':
        r['статус'] = 'пустой_файл'
    r['строк'], r['снапшотов'], r['дельт'], r['ошибок_разбора'] = n_line, n_snap, n_delta, n_err
    if first_ts is not None:
        r['первое_мс_от_начала'] = first_ts - d0
        r['последнее_мс_до_конца'] = d0 + DAY_MS - last_ts
        # свечи вне покрытия книги
        n_cm = len(cmin_in_file)
        n_out = sum(1 for m in cmin_in_file if m not in done_m and (m * 60000 + 59999 < first_ts or m * 60000 > last_ts))
    r['пересечений'], r['касаний'], r['первое_пересечение_мс'] = crossed, locked, first_cross
    r['макс_разрыв_мс'] = maxgap
    for i, e in enumerate(GAP_EDGES):
        r['разрывов_<=%d' % e] = gaps[i]
    r['разрывов_>60000'] = gaps[-1]
    r['мин_свеч'], r['мин_сверено'], r['мин_вне_книги'], r['мин_в_разрыве'] = n_cm, n_cmp, n_out, n_gapmin
    r['тик'] = tick if tick else ''
    r['нарушений_hl_>1тика'] = len(viol_t)
    r['макс_нарушение_тиков'] = round(max(viol_t), 1) if viol_t else ''
    r['u_последний'], r['seq_последний'], r['ts_первый'], r['ts_последний'] = last_u, last_seq, first_ts, last_ts
    r['нарушений_hl'] = sum(1 for v in viol if v > 0)
    r['нарушений_hl_>1бп'] = sum(1 for v in viol if v > 1)
    r['нарушений_hl_>10бп'] = sum(1 for v in viol if v > 10)
    r['макс_нарушение_бп'] = round(max(viol), 3) if viol else ''
    if DEEP:
        r['hl_глуб_нарушений'] = sum(1 for v in deep_v if v > 0)
        r['hl_глуб_>1тика'] = len(deep_vt)
        r['hl_глуб_>10бп'] = sum(1 for v in deep_v if v > 10)
        r['hl_глуб_макс_бп'] = round(max(deep_v), 3) if deep_v else ''
    r['close_медиана_бп'], r['close_p95_бп'], r['close_p99_бп'] = pct(close_d, .5), pct(close_d, .95), pct(close_d, .99)
    r['close_макс_бп'] = round(max(close_d), 3) if close_d else ''
    r['close_>10бп'] = sum(1 for v in close_d if v > 10)
    r['close_>50бп'] = sum(1 for v in close_d if v > 50)
    return r


def key_of(path):
    b = os.path.basename(path)
    m = re.match(r'(\d{4}-\d\d-\d\d)_(.+)_ob200\.data\.zip$', b)
    return m.group(1), m.group(2)


def work(a):
    path, mkt, cdir = a
    day, sym = key_of(path)
    try:
        return scan(path, mkt, sym, day, cdir)
    except Exception as e:  # не теряем строку
        return dict.fromkeys(FIELDS, 0) | {'рынок': mkt, 'инструмент': sym, 'сутки': day, 'статус': 'сбой_валидатора:' + repr(e)[:80]}


def cmd_file(path, cdir):
    mkt = os.path.basename(os.path.dirname(os.path.dirname(path)))
    r = work((path, mkt, cdir))
    for k in FIELDS:
        print('%s\t%s' % (k, r[k]))


def cmd_run(root, cdir, out, n):
    jobs = []
    for mkt in sorted(os.listdir(root)):
        mp = os.path.join(root, mkt)
        if not os.path.isdir(mp):
            continue
        for sym in sorted(os.listdir(mp)):
            for p in sorted(glob.glob(os.path.join(mp, sym, '*_ob200.data.zip'))):
                jobs.append((os.path.getsize(p), p, mkt))
    jobs.sort(reverse=True)  # тяжёлые первыми
    jobs = [(p, m, cdir) for _, p, m in jobs]
    part = out + '.part'
    done = set()
    if os.path.exists(part):  # докачка после обрыва: готовые строки не пересчитываются
        with open(part, newline='') as f:
            for r in csv.DictReader(f):
                done.add((r['рынок'], r['инструмент'], r['сутки']))
    todo = []
    for p, m, c in jobs:
        d, s_ = key_of(p)
        if (m, s_, d) not in done:
            todo.append((p, m, c))
    print('файлов', len(jobs), 'готово', len(done), 'в работе', len(todo), flush=True)
    new = not os.path.exists(part) or not done
    with open(part, 'a' if not new else 'w', newline='') as f, Pool(n) as pool:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for i, r in enumerate(pool.imap_unordered(work, todo, chunksize=1)):
            w.writerow(r)
            f.flush()
            if (i + 1) % 50 == 0:
                print(i + 1, '/', len(todo), flush=True)
    with open(part, newline='') as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (r['рынок'], r['инструмент'], r['сутки']))
    with open(out + '.tmp', 'w', newline='') as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(out + '.tmp', out)
    print('готово', out, len(rows))


def cmd_cover(root, cdir, heads, valid_csv, out):
    code = {}
    for h in heads.split(','):
        if not os.path.exists(h):
            continue
        for line in open(h):
            p = line.rstrip('\n').split('\t')
            if len(p) < 3:
                continue
            k = p[0].split()
            if len(k) == 4 and k[0] == 'bybit' and (k[1], k[2], k[3]) not in code:
                code[(k[1], k[2], k[3])] = (p[1], int(p[2]) if p[2].isdigit() else 0)
    val = {}
    for r in csv.DictReader(open(valid_csv)):
        val[(r['рынок'], r['инструмент'], r['сутки'])] = r['статус']
    rows = []
    for (mk, sym, day), (c, sz) in sorted(code.items()):
        mkt = 'bybit-' + mk
        d0 = int(__import__('datetime').datetime.strptime(day, '%Y-%m-%d').replace(
            tzinfo=__import__('datetime').timezone.utc).timestamp()) * 1000
        nc = len(load_candles(cdir, mkt, sym, d0))
        on_disk = os.path.exists(os.path.join(root, mkt, sym, '%s_%s_ob200.data.zip' % (day, sym)))
        st = val.get((mkt, sym, day))
        if c == '404':
            s = 'нет_архива_были_сделки' if nc else 'нет_архива_торгов_нет'
        elif not on_disk:
            s = 'не_скачан'
        elif st is None:
            s = 'не_проверен'
        else:
            s = st if st != 'ok' else ('ok' if nc else 'ok_без_свечей_суток')
        rows.append({'рынок': mkt, 'инструмент': sym, 'сутки': day, 'код_head': c, 'байт_head': sz,
                     'на_диске': int(on_disk), 'мин_свеч_суток': nc, 'статус': s})
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(collections.Counter(r['статус'] for r in rows))


if __name__ == '__main__':
    c = sys.argv[1]
    if c == 'file':
        cmd_file(sys.argv[2], sys.argv[3])
    elif c == 'run':
        cmd_run(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
    elif c == 'files':  # выборка: files <out.csv> N zip...
        zs = sys.argv[4:]
        with Pool(int(sys.argv[3])) as pool, open(sys.argv[2], 'w', newline='') as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            for r in pool.imap_unordered(work, [(z, os.path.basename(os.path.dirname(os.path.dirname(z))), '/srv/alpha-sda/candles-1m') for z in zs]):
                w.writerow(r)
    elif c == 'cover':
        cmd_cover(*sys.argv[2:7])
