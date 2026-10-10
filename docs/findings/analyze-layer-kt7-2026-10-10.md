# КТ-7 слой разбора `tools/analyze` — срез 1 (TK-146, 10.10)

Ветка `tk146-analyze`. Перенесено `git mv` (история цела) из `tools/compute/` в `tools/analyze/`: `monthly-pnl`, `p12-sharpe`, `p12lib`, `p12args`, `p12_r{1,2}_head`, `p12-r{1,2}-analyze`, `p12-tiers`, `p12-coin-c2`, `tk083-kpi-analyze`+`tk083_head`, `tk113-{cells,coins,ext}`, `tk114-{report2,testB,tier1-select,tier2-select}`, `tests/test_p12lib.py`.
Вход: `python tools/analyze <монитор|sub>` (`__main__.py`: monthly, sharpe, tiers, cuts, cells, ext, coin-c2, tier1, tier2, testB, report, portfolio); тест `tests/test_main.py`.

## md5 до · после (те же входы `data/`, прогон на ПК)
| потребитель | файл | md5 до | после |
|---|---|---|---|
| monthly | monthly-pnl-v171c-2026-10-09.csv | cf115852… | = |
| monthly | …md | 6e817500… | = после замены строки пути скрипта (`tools/compute/` → `tools/analyze/`, единственное отличие) |
| sharpe free | p12-sharpe-free-2026-10-08.csv / .inputs | 8f25a5e6… / f78945c7… | = / = |
| cells | cells-v171c-…csv / .md | 2be2b32b… / 0ac93199… | = / = |
| cuts | coins-breakdown-…md | f64896ab… | = |
| ext | ext-v171c-…csv / .md | ffce5ef4… / 72c8e15d… | = / = |

Тесты: `pytest tools/analyze/tests tools/compute/tests/test_portfolio_sim.py` — 28 пройдено.

## Срез 2
tk114-{report2,testB,tier1-select,tier2-select} прогнаны до (`e9a3a94e`) и после на ПК (`data/tk114`): md5 всех пяти выходов равны (p13-coins-B2/free, p13-tier1A, p13-tier2A, p13-final.md.part). Job-скрипты `tk113-drop-r1ext.sh`, `tk114-{halfA,halfB,tier1,v2}.sh`, `tk114-prep.py` — в `tools/compute/archive/`. `git grep` старых путей вне тикетов = 0 (пути в доках/findings поправлены).
Тест `test_t21_psim_gate::test_synthetic_gate_all_cases_match` красный и на базе `e9a3a94e` — не от переноса.

## Решение Судье
`portfolio-sim.py` оставлен в `tools/compute/` (зависит от `_lib/`, ссылки: `_load` в fresh-days/p02, `$T/` на серверах в p12-psim/expo/r1-psim, gate t21): это не разбор, а счётчик, который зовут прогонщики; в `analyze` — подкоманда `portfolio`. Перенос — отдельным КТ при КТ-9 (пути прогонов), если Судья требует. Не прогнан sharpe B2 (тот же код, что free).

## Срез 3 (возврат Судьи 14:40)
Разборщики TK-120 перенесены `git mv` в `tools/analyze/`, подкоманды `b1keys`, `thresholds`, `e106-loss`. Тело файлов побайтно равно `e9a3a94e` (md5: thresholds fcd57557, b1keys 639ff4fe, e106-loss 6e8c7206 — до = после), ни `__file__`, ни `sys.path`, ни соседних импортов — выход определяется только входом, расположение не влияет. Данные (`data/tk063`, `/data/tk064`, `/data/tk0115`) только на calc; сверку выходов на calc делает Судья тем же гейтом (`/data/j146`).
`tools/compute/tk135-gate.sh` возвращён к тексту `e9a3a94e` и перенесён в `tools/compute/archive/` (закрытый гейт TK-135; его old/-дерево — до КТ-7).
