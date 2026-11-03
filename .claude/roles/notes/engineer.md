- Грабли calc: python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке.

## TK-489 E-011 (возврат Судьи 05:55): 5b7864bd — check_e010.py [дерево] ставит mtime +30 мин (слот calc не пересобирал крейт), check_e011.py = до→после→до→после (75d318ab rc 101 / HEAD rc 0). Прогон отдельным процессом: data/e011-run.log (+ .rc, его число не верить), 4 сборки ~17 мин каждая. Дальше: все 4 шага ok -> Судье (пересчитать check_hash E-010, E-011 fix --before 9a81582f); красное — разбор лога /data/sched/logs/<id>.log.
## TK-489 (перемер D2 TK-479 после E-010) — ГОТОВО: `docs/findings/statarb-funding-remeasure-2026-11-03.md`, строки 67–68,70 отчёта TK-479 поправлены. Заявки calc 1103022721144 (--digest) и 1103023301277 (чистая скорость); `python -X utf8 tools/compute/statarb/remeasure_table.py table|report|check <id>` (без -I: он глушит PYTHONIOENCODING).

## TK-488 — возврат 2 (Н6–Н8) СДАНО (код 274d692e, отчёт tradfi-candles-2026-11-03.md, вселенная 436 = 168/184/84), ждёт Судью
- calc `/data/tk0488/` (universe3, tradfi-class-table3.csv, tradfi-list3.csv, run-uni3.sh); правило «дни не раньше листинга −1 сут» в tk488_universe.py; MDTUSDT-файл TK-476 уже сшит из двух активов — не трогал. Скрипты копировать scp в `/data/tk0488/bin/`; submit — `--kind net -- bash …`.
## TK-487 (панели .sap, tk487-convert eabdf11f) — ГОТОВО: calc `/data/tk0487/panels-*`, отчёт statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, f940f2ef; in_review у Судьи, E-010 исправлен a62ef6eb)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Выход без задержки 2-й ноги — вне ТЗ. D2 TK-479 мог считать фандинг на пустых ставках — CEO уведомлён комментарием.
- Worktrees ../alpha-tk482-* и ветка tk482-e010-red — удалить после приёмки. Бинари calc `/data/tk0482/alpha-*`.
- Грабли vps-check: одна сборка на тикет (rc 75; CALC_FORCE=1 обходит); два запуска с одним VPS_TAG затирают исходники друг друга; фоновый `( … &)` в Bash умирает — run_in_background.
## TK-486 (скорость этапа 1; tk486-speed 89ee15a9, worktree .claude/worktrees/TK-486)
- Замер: corr.rs ~3e8 пар·отсч/с/ядро (~6,8 такта/отсчёт = скаляр), 8 потоков ×7,3; v3 и zip/zip2 не помогли (zip ×1,07, zip2 медленнее). eg.rs (15 сумм, центровать ряд на символ) готов.
- 89ee15a9: `--kernel avx` (явные 256-бит, mul+add без FMA, бит в бит с ref, runtime-детект); тесты corr 12/12. Сборка готова (/data/tk0486/alpha-run-avx); бенч ref/avx подан, юнит 1103024411111 (wait до 06:00).
- Дальше (бенч уже подан): повтор — `bash /data/tk0486/corr-bench-avx.sh /data/tk0486/alpha-run-avx 300 1` и `... 900 8` (ref vs avx); ждать ≥ ×5 -> умолчание avx (гейт: kernels_are_bit_identical уже есть); иначе FMA/f32-блок-GEMM. Затем: суммы EG по блокам в формировании, отчёт docs/findings/statarb-speed-<дата>.md (было/стало ×N, строка Алгоритм, предел числом), чтение: convert 4 потока 27,7 с/срез, HDD холодное 77–85 МБ/с.
- Подача на calc: `cd /data/sched && SCHED_MANIFEST=0 python3 alsched.py submit --name … --max-runtime … --est … --cls prod --kind work --cores N --mem M --reason … --adhoc … -- bash …`. vps-check all красен (check-sprawl) — test/clippy по отдельности; сборка бинаря: tools/vps-check-avx.tmp.sh (неотслеживаемый) с VPS_TAG и CALC_FORCE=1.
- Ожидатель на 15 ядрах не подавать (Судья 04:38).
