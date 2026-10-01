#!/usr/bin/env python3
# TK-022 (В-163): таблица замера скорости VPS по days.csv/summary.json стенда tk026-vps-bench.py + прогноз часов.
#   tk022-bench-table.py <имя>=<каталог замера> [<имя>=<каталог>...] [--out файл.md]
# Исходы клеток не читаются: только секунды, МБ/с, пики памяти, загрузка ЦП.
import csv, json, os, sys

GB_PER_DAY = {'янв–июн': (181, 1.44), 'авг–сен': (61, 1.80)}  # сутки, ГБ/сутки (docs/findings/deck-box-speed-2026-10-02.md)


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def machine(name, d):
    rows = list(csv.DictReader(open(f'{d}/days.csv')))
    summ = json.load(open(f'{d}/summary.json')) if os.path.exists(f'{d}/summary.json') else {}
    out = [f'### {name}', '',
           '| сутки | ГБ | доставка, мин (МБ/с) | проход 1, мин | проход 2, мин | проход 3, мин | сутки целиком, мин | полос | ЦП, % | iowait, % | пик RSS прохода, МБ | пик памяти, МБ | своп, МБ | ok |',
           '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        rss = max(f(r['p1_rss_mb']), f(r['p2_rss_mb']), f(r['p3_rss_mb']))
        out.append(f"| {r['day']} | {r['gb']} | {f(r['deliver_s']) / 60:.1f} ({r['deliver_mbs']}) | {f(r['p1_s']) / 60:.1f} | {f(r['p2_s']) / 60:.1f} | {f(r['p3_s']) / 60:.1f} "
                   f"| {f(r['day_wall_s']) / 60:.1f} | {r['lanes']} | {r['cpu_avg_pct']} | {r['iowait_avg_pct']} | {rss:.0f} | {r['mem_used_peak_mb']} | {r['swap_peak_mb']} | {r['ok']} |")
    seq = sum(f(r['deliver_s']) + f(r['p1_s']) + f(r['p2_s']) + f(r['p3_s']) for r in rows)
    gb = sum(f(r['gb']) for r in rows)
    out += ['']
    if gb:
        out.append(f'Σ по {len(rows)} сут: {gb:.2f} ГБ; одна полоса без перекрытия (доставка + 3 прохода): {seq / 60:.1f} мин = {seq / len(rows) / 60:.1f} мин/сут, {seq / gb:.0f} с/ГБ.')
    if summ.get('phaseB_s_per_gb'):
        out.append(f"Фаза B (полос {summ.get('lanes')}, конвейер, доставка в фоне): {summ['phaseB_wall_s'] / 60:.1f} мин на {summ['phaseB_days']} сут / {summ['phaseB_gb']:.2f} ГБ = {summ['phaseB_s_per_gb']:.0f} с/ГБ; "
                   f"ЦП {summ.get('cpu_avg', 0):.0f} %, iowait {summ.get('iowait_avg', 0):.0f} %, пик памяти {summ.get('mem_used_peak_mb', 0):.0f} МБ.")
    out += ['', '| прогноз | сутки × ГБ/сут | одна полоса без перекрытия, ч | конвейер фазы B, ч |', '|---|---|---|---|']
    for k, (n, g) in GB_PER_DAY.items():
        a = seq / gb * n * g / 3600 if gb else float('nan')
        b = summ['phaseB_s_per_gb'] * n * g / 3600 if summ.get('phaseB_s_per_gb') else float('nan')
        out.append(f'| {k} | {n} × {g:.2f} = {n * g:.0f} ГБ | {a:.1f} | {b:.1f} |')
    return '\n'.join(out) + '\n'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    text = '\n'.join(machine(*a.split('=', 1)) for a in args)
    if '--out' in sys.argv:
        open(sys.argv[sys.argv.index('--out') + 1], 'w', encoding='utf-8').write(text)
    print(text)


if __name__ == '__main__':
    main()
