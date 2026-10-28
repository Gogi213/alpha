# TK-433 v11 дельта 2: ходы P10/50/90 и сетка ×k·X_D (описательно) из windows-*.csv (представители, без исключённых), X = q99 по (D, знак)
import csv, json, numpy as np
xq = json.load(open('data/tk433v11/s30/xq.json', encoding='utf-8'))
out = []
for m, f in (('09', 'w09'), ('08', 'w08')):
    rows = list(csv.DictReader(open(f'data/tk433v11/mv/{f}.csv', encoding='utf-8')))
    out.append(f'\n## Месяц {m}: ход события, % (X_D = q99 сентября для обоих месяцев; представители кластеров, без E1/E3/E4)\n\n| D | знак | N | P10 | P50 | P90 | макс |\n|---|---|---|---|---|---|---|')
    grid = ['\n### Сетка k·X_D (описательно): доля событий (q99) с ходом ≥ k·X_D\n\n| D | знак | N | ×1 | ×1,5 | ×2 | ×4 | ×8 |\n|---|---|---|---|---|---|---|---|']
    for D in (90, 180, 360, 720, 1440):
        for s in ('+', '-'):
            X = xq[f'{D}|{s}']['q99']
            a = np.array([float(r['ход_%']) for r in rows if int(r['D']) == D and r['знак'] == s and abs(float(r['X']) - X) < 1e-6
                          and r['исключено'] == '' and r['представитель'] == '1'])
            if not len(a): out.append(f'| {D} | {s} | 0 | | | | |'); continue
            p = np.percentile(a, [10, 50, 90]); out.append(f'| {D} | {s} | {len(a)} | {p[0]:.1f} | {p[1]:.1f} | {p[2]:.1f} | {a.max():.0f} |')
            grid.append(f'| {D} | {s} | {len(a)} | ' + ' | '.join(f'{(a >= k * X).mean():.2f}' for k in (1, 1.5, 2, 4, 8)) + ' |')
    out += grid
open('docs/findings/pump-v11-moves-2026-10-28.md', 'w', encoding='utf-8', newline='\n').write(
    '# TK-433 v11: ходы P10/50/90 и сетка ×k (дельта по возврату Судьи 07:01)\n\nИсточник: windows-*.csv calc (v11, v11a), xq.json. Код: tools/compute/tk433-v11-moves.py. Сетка ×0,5 и сравнение с контролями — не здесь (нужны ходы контролей, см. «Не сделано» в логе).\n' + '\n'.join(out) + '\n')
