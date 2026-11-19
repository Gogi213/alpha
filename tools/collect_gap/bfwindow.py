#!/usr/bin/env python3
"""BingX: сколько номеров дозапроса уцелело бы при другом порядке и времени запросов (REST видит только последние 1 000 сделок символа).
Вход — файл дозапроса `backfill-bingx-<запуск>.tsv.zst` (строки `rg`: символ, от, до, найден, получено, статус, шард, время пропуска) и записи сделок ws (`trade*-<запуск>.tsv.zst`): по ним — самый свежий номер символа
в любой момент приёма. Запрос в момент T отдаёт номера ≥ `свежий(T) − 999`; из пропуска `lo..=hi` годны номера этого окна.
Модель проверяется на факте: порядок и время запросов боя (`rg`) должны дать боевую потерю. Дальше — варианты: подтверждение 10 с / 1 с, порядок FIFO / по срочности (оценка по самому пропуску: темп = номеров / длительность пропуска),
пауза между запросами 1,5 с / 0,2 с (лимит BingX на рыночные запросы — 500 за 10 с). Запуск: bfwindow.py <корень данных> <запуск> <день, напр. 2026-11-16> [--t0 мс --t1 мс]."""
import argparse, bisect, glob, itertools, os, subprocess

WINDOW = 1000


def zlines(path):
    out = subprocess.run(['zstd', '-dc', path], capture_output=True).stdout.decode('utf-8', 'replace')
    return out.split('\n')


def load_gaps(root, run, day):
    gaps = []
    for f in sorted(glob.glob(os.path.join(root, day, f'backfill-bingx-{run}.tsv.zst'))):
        for ln in zlines(f):
            p = ln.split('\t')
            if p[0] == 'rg' and len(p) >= 12:
                gaps.append(dict(done=int(p[1]), sym=p[2], lo=int(p[3]), hi=int(p[4]), found=int(p[5]), got=int(p[6]), status=p[7], shard=p[9], gap_ms=int(p[11])))
    return gaps


def load_ids(root, run, day, syms, t0, t1):
    """{символ: ([приём мс], [номер])} — записи сделок ws в окне приёма [t0, t1], в порядке приёма."""
    out = {s: ([], []) for s in syms}
    for f in sorted(glob.glob(os.path.join(root, day, f'trade*-{run}.tsv.zst'))):
        t_ns, prev = 0, {}
        for ln in zlines(f):
            p = ln.split('\t')
            if len(p) < 3 or not p[0]:
                continue
            try:
                t_ns += int(p[1])
            except ValueError:
                continue
            if p[0] != 't' or len(p) < 6:
                continue
            sym = p[4]
            a = int(p[5]) if sym not in prev else prev[sym] + 1 + int(p[5])
            prev[sym] = a
            ms = t_ns // 1_000_000
            if sym in out and t0 <= ms <= t1:
                out[sym][0].append(ms)
                out[sym][1].append(a)
    for sym, (ts, aa) in out.items():  # самый свежий номер к каждому приёму (номера приходят не по порядку)
        out[sym] = (ts, list(itertools.accumulate(aa, max)))
    return out


def latest(ids, t):
    """Самый свежий номер символа, принятый не позже t (мс); None — записей нет."""
    ts, aa = ids
    i = bisect.bisect_right(ts, t)
    return aa[i - 1] if i else None


def recovered(g, ids, t):
    lat = latest(ids[g['sym']], t)
    if lat is None:
        return 0
    return max(0, g['hi'] - max(g['lo'], lat - WINDOW + 1) + 1)


def simulate(gaps, ids, order, confirm_ms, call_gap_ms):
    """Один поток запросов: очередь в порядке `order`, первый запрос — не раньше найден + confirm; возвращает (потеряно номеров, число пропусков с потерей, последний запрос, мс)."""
    t = 0
    lost = n_lost = 0
    for g in order:
        t = max(t + call_gap_ms, g['found'] + confirm_ms)
        r = recovered(g, ids, t)
        n = g['hi'] - g['lo'] + 1
        lost += n - r
        n_lost += n > r
    return lost, n_lost, t


def reorder_delays(root, run, day, t0, t1):
    """Как долго номер, пропущенный при приходе более высокого, ждёт своего прихода (перестановка линий / замена) — окно приёма [t0, t1] без обрывов: сколько номеров ещё не пришло через 0,5 / 1 / 2 / 5 / 10 с."""
    delays, never, runs = [], 0, 0
    for f in sorted(glob.glob(os.path.join(root, day, f'trade*-{run}.tsv.zst'))):
        t_ns, prev, hi, miss = 0, {}, {}, {}
        for ln in zlines(f):
            p = ln.split('	')
            if len(p) < 3 or not p[0]:
                continue
            try:
                t_ns += int(p[1])
            except ValueError:
                continue
            if p[0] != 't' or len(p) < 6:
                continue
            sym = p[4]
            a = int(p[5]) if sym not in prev else prev[sym] + 1 + int(p[5])
            prev[sym] = a
            ms = t_ns // 1_000_000
            if ms < t0 or ms > t1:
                continue
            h = hi.get(sym)
            m = miss.setdefault(sym, {})
            if h is None:
                hi[sym] = a
                continue
            if a in m:
                delays.append(ms - m.pop(a))
            elif a > h + 1 and a - h - 1 <= 1000:
                runs += 1
                for i in range(h + 1, a):
                    m[i] = ms
            if a > h:
                hi[sym] = a
        never += sum(len(m) for m in miss.values())
    n = len(delays) + never
    print(f'окно {t0 / 1000:.0f}…{t1 / 1000:.0f}: пропущенных номеров {n} (пришли позже {len(delays)}, не пришли к концу окна {never}), открытых пропусков {runs}')
    for lim in (500, 1000, 2000, 5000, 10000):
        print(f'  ещё не пришли через {lim / 1000:g} с: {sum(1 for d in delays if d > lim) + never}  (макс. ожидание пришедших {max(delays) / 1000 if delays else 0:.2f} с)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('run')
    ap.add_argument('day')
    ap.add_argument('--t0', type=int, default=0)
    ap.add_argument('--t1', type=int, default=0)
    ap.add_argument('--reorder', action='store_true', help='только замер ожидания пропущенного номера в окне --t0/--t1 (мс приёма)')
    a = ap.parse_args()
    if a.reorder:
        return reorder_delays(a.root, a.run, a.day, a.t0, a.t1)
    gaps = load_gaps(a.root, a.run, a.day)
    f0 = min(g['found'] for g in gaps)
    gaps = [g for g in gaps if g['found'] - f0 < 60_000]  # одно событие: пропуски, найденные в первую минуту после возобновления
    t0, t1 = a.t0 or f0 - 60_000, a.t1 or max(g['done'] for g in gaps) + 5_000
    ids = load_ids(a.root, a.run, a.day, {g['sym'] for g in gaps}, t0, t1)
    n_ids = sum(g['hi'] - g['lo'] + 1 for g in gaps)
    print(f'пропусков {len(gaps)}, номеров {n_ids}; найдены {f0 / 1000:.0f}…{max(g["found"] for g in gaps) / 1000:.0f} с, последний итог {max(g["done"] for g in gaps) / 1000:.0f} с')
    actual_lost = sum(g['hi'] - g['lo'] + 1 - g['got'] for g in gaps if g['status'] != 'ok')
    print(f'бой: потеряно {actual_lost} номеров в {sum(1 for g in gaps if g["status"] != "ok")} пропусках (partial)')
    fifo = sorted(gaps, key=lambda g: g['done'])  # порядок запросов боя
    # срочность по самому пропуску: темп = номеров / длительность пропуска (приём до и после), срок = найден + (окно − номеров) / темп
    def urgency(g):
        n = g['hi'] - g['lo'] + 1
        dur = max(1, g['found'] - g['gap_ms'])
        return g['found'] + max(0, WINDOW - n) * dur / n
    edf = sorted(gaps, key=urgency)
    print(f'{"порядок":18s} {"подтв.":>7s} {"пауза":>6s} {"потеряно":>9s} {"пропусков":>10s} {"последний запрос, с после найден":>34s}')
    for name, order in (('бой (по rg)', fifo), ('по срочности', edf)):
        for conf, cg in ((10_000, 1500), (2_000, 1500), (1_000, 1500), (10_000, 200), (1_000, 200)):
            lost, nl, t = simulate(gaps, ids, order, conf, cg)
            print(f'{name:18s} {conf / 1000:7.1f} {cg / 1000:6.1f} {lost:9d} {nl:10d} {(t - f0) / 1000:34.0f}')
    # без очереди (запросы параллельно): потеря от одного подтверждения
    for conf in (10_000, 2_000, 1_000, 0):
        lost = sum(g['hi'] - g['lo'] + 1 - recovered(g, ids, g['found'] + conf) for g in gaps)
        print(f'без очереди, подтверждение {conf / 1000:.0f} с: потеряно {lost}')
    print('пропуски с потерей в бою: символ, номеров, получено, найден→итог с, годно сразу после найден (+1 с), номеров в окне к +1 с')
    for g in sorted((g for g in gaps if g['status'] != 'ok'), key=lambda g: -(g['hi'] - g['lo'])):
        n = g['hi'] - g['lo'] + 1
        r = recovered(g, ids, g['found'] + 1000)
        print(f'  {g["sym"]:24s} {n:6d} {g["got"]:6d} {(g["done"] - g["found"]) / 1000:7.0f} {r:6d}')


main()
