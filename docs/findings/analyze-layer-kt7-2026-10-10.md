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

## Осталось
`portfolio-sim.py` (≈50 ссылок: `_load`, `tmp-kpi/`, `bin/`, `gate-merge.sh`), `tk114-prep/halfA/halfB/tier1/v2.sh`, `tk113-drop-r1ext.sh`, скрипты `tk115/118/120-*`, `p12-r1-psim`; не прогнаны (нужны данные calc): tk114-report2/testB/tier1/tier2, sharpe B2.
