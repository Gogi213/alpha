# TK-512 — aux_feats: Aster без источника OI — ПРИЁМКА (Судья, 09.11 ~17:20)

Сдача: ветка tk500-p40core @95f7436c (дерево `.claude/worktrees/TK-512`), итог `docs/findings/tk512-aux-sweep-final-2026-11-09.md`.
Прежний вердикт — `tk512-aster-oi-return-2026-11-09.md` (возврат по пунктам 1–3). Код @201a5d1c принят там же; 201a5d1c→95f7436c — только `aux_sweep.py`, `aux_sweep_follow.sh` и итог (код ядра не менялся).

| пункт (возврат 1–3) | вердикт | проверка (сам, srv 5.231.25.224) |
|---|---|---|
| 1. Живой AVAAI: oi5m у 143 не NaN, auxholes без aster/open_interest | держится | `out_d1/12/2026-09-17+14/143.aux.csv`: 641 строк, oi5m NaN 0, шапка с `srcOIA,srcOIB`; `grep -c "aster.*open_interest" out_d1/auxholes.csv` → 0 |
| 2. Пары без Aster — кол. 1–10 байт в байт с прежним | держится | `/root/tk512/live/res/144,145.aux.csv` (сверены автором со старым out_d1 до перегона) = текущие `out_d1/12/…/144,145.aux.csv` по кол. 1–12 (`cmp`, «same12») |
| 3. Перегон aux пар с Aster для D1 | держится | md5 `/root/p40core/aux_feats.py` 23a85aa1 = `git show 201a5d1c:…` (= 95f7436c), `assemble.py` d2a7af43 = 95f7436c; `orch.py:317` зовёт `{HERE}/aux_feats.py` — недописанные чанки при возобновлении считаются новым кодом. Мой пересчёт по `pairs_d1.csv`: DONE-каталогов 29 из 96; в DONE старого вида 0 (без Aster 208, с Aster 116 — все новые: srcOI 10 — 67, 00 — 49); вне DONE 20 старого вида (все без Aster) + 13 нового; итого с Aster 119, все нового вида |

Замечание (вывод не меняет): в итоге строки «aux.csv в чанках DONE 337» и «пар с Aster 119» считают и 13 файлов нового вида вне DONE; строго в DONE — 324 и 116 (таблица ниже в том же файле — 144+64+67+49 = 324 — верна).

Остаток автора «D1 не дописан (стоп В-119)» — не пункт заказа: заказ — перегон aux для D1 на новом коде; новый код стоит в `/root/p40core`, готовое перегнано. Слияние tk500-p40core — общим гейтом ветки, не этой задачей.

Решения: В-109 (период 17.09–22.10), В-119 (D1 не считать во время закачки — сдача D1 не запускала) — не противоречит.

## Числа

| число | файл:строка |
|---|---|
| 357 | .claude/worktrees/TK-512/docs/findings/tk512-aux-sweep-final-2026-11-09.md:7 |
| 20 | .claude/worktrees/TK-512/docs/findings/tk512-aux-sweep-final-2026-11-09.md:8 |
| 119 | .claude/worktrees/TK-512/docs/findings/tk512-aux-sweep-final-2026-11-09.md:10 |
| 420 | .claude/worktrees/TK-512/docs/findings/tk512-aux-sweep-final-2026-11-09.md:11 |
| 67 | .claude/worktrees/TK-512/docs/findings/tk512-aux-sweep-final-2026-11-09.md:19 |
| 49 | .claude/worktrees/TK-512/docs/findings/tk512-aux-sweep-final-2026-11-09.md:20 |
