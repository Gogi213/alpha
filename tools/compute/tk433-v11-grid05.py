# TK-433 v11 дельта (Судья 07:50 п.1): сетка k·X_D, k=0,5…8, события против контролей ±30 (выборка v9b, сид 433); X_D = q99 сентября. Ход = ret_bps_leg/100 (в ориентации знака).
import json, sys, pandas as pd, numpy as np
xq = json.load(open('data/tk433v11/s30/xq.json', encoding='utf-8'))
K = (0.5, 1, 1.5, 2, 4, 8)
out = ['# TK-433 v11: сетка ×0,5…×8 против контролей (описательно, вне Холма)\n',
       'Источник: выборка v9b (≤600/ячейку, сид 433), ход ноги = ret_bps_leg/100 в ориентации знака; контроли ±30 сут; X_D = q99 сентября (xq.json). Доля = окон с ходом ≥ k·X_D. '
       'События выборки — представители при X_D (q99), поэтому доля событий при ×1 близка к 1 (0,84–0,91, не 1: ход ноги до t_peak считается не так, как ход окна детектора); значимая часть сетки — доля контролей: как часто обычное окно той же длины достигает k·X_D. '
       'События при пороге 0,5·X_D (мягкий детектор) здесь НЕ посчитаны — нужен прогон детектора на calc.\n']
for m in ('09', '08'):
    f = pd.read_csv(f'data/tk433v11/f/f{m}.csv', usecols=['id', 'роль', 'θ', 'знак'])
    t = pd.read_csv(f'data/tk433v11/f/t{m}.csv', usecols=['id', 'ret_bps_leg'])
    d = f.merge(t, on='id'); d['mv'] = d['ret_bps_leg'] / 100
    out.append(f'\n## Месяц {m}\n\n| D | знак | роль | N | ' + ' | '.join(f'×{str(k).replace(".", ",")}' for k in K) + ' |\n|---|---|---|---|' + '---|' * len(K))
    for D in (90, 180, 360, 720, 1440):
        for sg, s in ((1, '+'), (-1, '-')):
            X = xq[f'{D}|{s}']['q99']
            for role in ('event', 'control'):
                a = d[(d['θ'] == D) & (d['знак'] == sg) & (d['роль'] == role)]['mv'].dropna().to_numpy()
                if sg == -1 and len(a) and np.median(a) < 0: a = -a
                if not len(a): continue
                out.append(f'| {D} | {s} | {role} | {len(a)} | ' + ' | '.join(f'{(a >= k * X).mean():.3f}' for k in K) + ' |')
open('docs/findings/pump-v11-grid05-2026-10-28.md', 'w', encoding='utf-8', newline='\n').write('\n'.join(out) + '\n')
