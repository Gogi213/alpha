#!/usr/bin/env python3
"""TK-387 (G-016): свопы Solana из SQD Portal (без ключа), потоково — сразу дельты балансов, сырьё не хранится.

Фильтр tokenBalances по mint всех монет разом (один проход по блокам на все монеты), сутки режутся на сегменты по слотам,
сегменты идут параллельно (пул процессов). Строка дельты — как у tk378-sqd-deltas.py (ts,slot,txidx,sig,payer,fee,cu,mint,account,delta,dec),
дальше tk378-sol-swaps2.py по суткам. Портал отдаёт 429/503/529 вперемешку — повтор с паузой. Возобновляемо: готовый сегмент (.done) пропускается.
Запуск: tk387-sqd-sol.py MINTS.csv OUT T_FROM T_TO PER_DAY NPROC [probe]   (T — unix-секунды UTC, MINTS — src,coin,mint)
Выход: OUT/seg/<день>-<k>.csv(.done), OUT/slots.json, OUT/status.txt; метка OUT/sqd.rc (0 — всё готово)."""
import csv, json, os, sys, time, urllib.request, urllib.error
from multiprocessing import Pool

mints_csv, out = sys.argv[1], sys.argv[2]
T_FROM, T_TO, PER_DAY, NPROC = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
PROBE = len(sys.argv) > 7 and sys.argv[7] == 'probe'
U = 'https://portal.sqd.dev/datasets/solana-mainnet/stream'
H = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
Q = {'So11111111111111111111111111111111111111112', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'}
MINTS = [r['mint'] for r in csv.DictReader(open(mints_csv)) if r['src'] == 'gate30k' or r['src'] == 'gate']
MINTS = sorted(set(MINTS))
MS = set(MINTS)
F = {'block': {'number': True, 'timestamp': True},
     'transaction': {'signatures': True, 'transactionIndex': True, 'feePayer': True, 'fee': True, 'computeUnitsConsumed': True, 'err': True},
     'tokenBalance': {'transactionIndex': True, 'account': True, 'preMint': True, 'postMint': True,
                      'preAmount': True, 'postAmount': True, 'preDecimals': True, 'postDecimals': True}}
HDR = 'ts,slot,txidx,sig,payer,fee,cu,mint,account,delta,dec'.split(',')


def post(b, tries=60):
    for a in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(U, json.dumps(b).encode(), H), timeout=600).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 529, 502, 504):
                time.sleep(min(5 + 3 * a, 30))
                continue
            raise RuntimeError(f'{e.code} {e.read()[:200]}')
        except Exception:
            time.sleep(5)
    raise RuntimeError('retries')


def head(n):
    r = post({'type': 'solana', 'fromBlock': n, 'toBlock': n + 3000, 'fields': {'block': {'number': True, 'timestamp': True}}})
    j = json.loads(r.decode().split('\n')[0])['header']
    return j['number'], j['timestamp']


def slot_at(t, n=457030000, x=1792022400):
    """секущая по (слот, время): якорь 15.10.2026 00:00 UTC ≈ слот 457,03 млн"""
    n0, x0 = n, x
    n = n0 + int((t - x0) / 0.2)
    pts = []
    for _ in range(14):
        n, x = head(n)
        if abs(x - t) < 60:
            return n
        pts.append((n, x))
        if len(pts) >= 2 and pts[-1][1] != pts[-2][1]:
            rate = (pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
        else:
            rate = 0.2
        n = n + int((t - x) / rate)
    return n


def seg(a):
    name, frm, to = a
    f = f'{out}/seg/{name}.csv'
    if os.path.exists(f + '.done'):
        return name, 0, 0.0
    t0 = time.time()
    b = {'type': 'solana', 'fromBlock': frm, 'toBlock': to, 'fields': F,
         'tokenBalances': [{'preMint': MINTS, 'transaction': True, 'transactionTokenBalances': True},
                           {'postMint': MINTS, 'transaction': True, 'transactionTokenBalances': True}]}
    rows = 0
    fh = open(f + '.tmp', 'w', newline='')
    w = csv.writer(fh)
    w.writerow(HDR)
    cur = frm
    while cur <= to:
        b['fromBlock'] = cur
        data = None
        for _try in range(6):
            raw = [l for l in post(b).decode().split('\n') if l]
            ok = []
            try:
                for l in raw:
                    ok.append(json.loads(l))
            except json.JSONDecodeError:
                if not ok:
                    if _try == 5:
                        raise
                    time.sleep(5)
                    continue
            data = ok
            break
        if not data:
            break
        for d in data:
            h = d['header']
            tx = {t['transactionIndex']: t for t in d.get('transactions', [])}
            by = {}
            for tb in d.get('tokenBalances', []):
                mint = tb.get('postMint') or tb.get('preMint')
                dl = int(tb.get('postAmount') or 0) - int(tb.get('preAmount') or 0)
                if dl and (mint in MS or mint in Q):
                    by.setdefault(tb['transactionIndex'], []).append((mint, tb['account'], dl, tb.get('postDecimals', tb.get('preDecimals'))))
            for ti, rr in by.items():
                if not any(r[0] in MS for r in rr):
                    continue
                t = tx.get(ti, {})
                if t.get('err'):
                    continue
                sig = (t.get('signatures') or [''])[0]
                for mint, acc, dl, dec in rr:
                    w.writerow([h['timestamp'], h['number'], ti, sig, t.get('feePayer', ''), t.get('fee', ''), t.get('computeUnitsConsumed', ''), mint, acc, dl, dec])
                    rows += 1
        last = data[-1]['header']['number']
        if last + 1 <= cur or last >= to - 1:
            break
        cur = last + 1
    fh.close()
    os.replace(f + '.tmp', f)
    open(f + '.done', 'w').write(f'{rows}\n')
    open(f'{out}/status.txt', 'a').write(f"{time.strftime('%F %T')} {name} rows={rows} {time.time()-t0:.0f}s\n")
    return name, rows, time.time() - t0


if __name__ == '__main__':
    os.makedirs(f'{out}/seg', exist_ok=True)
    days = list(range(T_FROM, T_TO + 1, 86400))
    sf = f'{out}/slots.json'
    slots = json.load(open(sf)) if os.path.exists(sf) else {}
    for t in days:
        if str(t) not in slots:
            slots[str(t)] = slot_at(t)
            json.dump(slots, open(sf, 'w'))
    jobs = []
    for i, t in enumerate(days[:-1] if len(days) > 1 else days):
        a = slots[str(t)]
        z = slots[str(days[i + 1])] - 1 if i + 1 < len(days) else a + 400000
        step = (z - a + 1) // PER_DAY + 1
        for k in range(PER_DAY):
            lo, hi = a + k * step, min(a + (k + 1) * step - 1, z)
            if lo <= hi:
                jobs.append((f"{time.strftime('%Y%m%d', time.gmtime(t))}-{k:02d}", lo, hi))
    if PROBE:
        jobs = jobs[:1]
    print(f'{len(jobs)} сегментов, {len(MINTS)} mint, слоты/сутки ≈ {slots[str(days[1])] - slots[str(days[0])] if len(days) > 1 else "?"}', flush=True)
    with Pool(NPROC) as p:
        for name, rows, dt in p.imap_unordered(seg, jobs):
            print(name, rows, f'{dt:.0f}s', flush=True)
    if not PROBE:
        open(f'{out}/sqd.rc', 'w').write('0\n')
