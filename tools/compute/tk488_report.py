#!/usr/bin/env python3
"""TK-488 (В-93): итоговая таблица класса TradFi — по контракту вселенной: класс, биржа, источник метаданных, флаг, дни во вселенной,
покрытие свечей (из tk476_coverage.py), чего не хватает вселенной (need_candles из tk488_universe.py), фандинг и причина дыр.
Ничего не выдумывает: причина — числа coverage и need-candles словами; фандинг всех трёх бирж — из каталога фандинга (Gate качает tk488_gate_funding.py).
usage: tk488_report.py <tradfi-list.csv> <каталог universe TK-488> <coverage-tradfi.csv> <каталог фандинга|-> <выход.csv>"""
import csv, gzip, importlib.util, os, sys
from collections import Counter
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk488u', os.path.join(HERE, 'tk488_universe.py'))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

COLS = ['биржа', 'символ', 'класс', 'подкласс', 'тип_контракта', 'статус', 'источник_метаданных', 'флаг', 'дней_во_вселенной',
        'вселенная_с', 'вселенная_по', 'свечи_первая_utc', 'свечи_последняя_utc', 'строк', 'дыр', 'пропущено_минут', 'макс_дыра_мин',
        'дублей', 'не_по_порядку', 'не_на_минуте', 'не_хватает', 'фандинг_записей', 'фандинг_первая_utc', 'фандинг_последняя_utc', 'причина']


def days_in(ivs):
    return sum((b - a).days + 1 for a, b in ivs)


def funding(fdir, ex, sym):
    """(записей, первая, последняя) | None."""
    p = os.path.join(fdir, ex, sym + '.csv.gz')
    if not os.path.exists(p):
        return None
    with gzip.open(p, 'rt') as f:
        r = csv.reader(f)
        next(r)
        ts = [int(x[0]) for x in r if x]
    iso = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M')
    return (len(ts), iso(min(ts)), iso(max(ts))) if ts else (0, '', '')


def reason(c, need):
    """Причина — только из чисел coverage и need-candles."""
    if c is None:
        return 'нет файла свечей'
    if c['ошибка']:
        return 'файл не читается: ' + c['ошибка']
    parts = []
    if need:
        parts.append('вселенная требует сверх файла: ' + need)
    if int(c['дыр']):
        parts.append(f"дыр {c['дыр']}, пропущено {c['пропущено_минут']} мин (макс. {c['макс_дыра_мин']})")
    for k, w in (('дублей', 'дублей'), ('не_по_порядку', 'не по порядку'), ('не_на_минуте', 'не на минуте')):
        if int(c[k]):
            parts.append(f'{w} {c[k]}')
    return '; '.join(parts) or 'ок'


def build(lst, unidir, covf, fdir):
    cov = {(r['биржа'], r['символ']): r for r in csv.DictReader(open(covf, encoding='utf-8'))}
    meta = {(r['exchange'], r['symbol']): r for r in u.tradfi_rows(lst)}
    last_day = max((datetime.strptime(r['последняя_utc'], '%Y-%m-%d %H:%M') for r in cov.values() if r['последняя_utc']),
                   default=datetime.now()).date()
    out = []
    for ex in u.EXCH:
        ivs = u.intervals(os.path.join(unidir, f'universe-{ex}.csv'))
        for sym in sorted(ivs):
            m, c = meta[(ex, sym)], cov.get((ex, sym))
            have = (datetime.strptime(c['первая_utc'], '%Y-%m-%d %H:%M'), datetime.strptime(c['последняя_utc'], '%Y-%m-%d %H:%M')) if c and c['первая_utc'] else None
            need = u.need_candles(ivs[sym], have, last_day)
            f = funding(fdir, ex, sym) if fdir != '-' else 'не считали'
            fcols = [f, '', ''] if isinstance(f, str) else ['нет файла', '', ''] if f is None else list(f)
            cc = [c[k] for k in ('первая_utc', 'последняя_utc', 'строк', 'дыр', 'пропущено_минут', 'макс_дыра_мин', 'дублей', 'не_по_порядку', 'не_на_минуте')] if c else [''] * 9
            out.append([ex, sym, m['class'], m['subtype'], m['contract_type'], m['status'], m['meta_source'], m['flag'], days_in(ivs[sym]),
                        min(a for a, _ in ivs[sym]), max(b for _, b in ivs[sym]), *cc, need, *fcols, reason(c, need)])
    return out


def listing_violations(rows, unidir):
    """Контракты, у которых вселенная начинается раньше даты листинга -1 сут (символ занят чужим крипто-контрактом) — должно быть пусто."""
    out = []
    for r in rows:
        ld = u.listing_day(r)
        ivs = u.intervals(os.path.join(unidir, f"universe-{r['exchange']}.csv")).get(r['symbol'])
        if ld and ivs and min(a for a, _ in ivs) < ld - timedelta(1):
            out.append((r['exchange'], r['symbol'], min(a for a, _ in ivs), ld))
    return out


def main():
    lst, unidir, covf, fdir, outf = sys.argv[1:6]
    rows = build(lst, unidir, covf, fdir)
    viol = listing_violations(u.tradfi_rows(lst), unidir)
    print('вселенная раньше листинга -1 сут:', len(viol), *viol)
    with open(outf, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    cnt = Counter((r[0], r[2]) for r in rows)
    bad = Counter((r[0], r[2]) for r in rows if r[-1] != 'ок')
    for k in sorted(cnt):
        print(*k, 'контрактов', cnt[k], 'не «ок»', bad[k])
    print('всего', len(rows), 'не «ок»', sum(bad.values()))


if __name__ == '__main__':
    main()
