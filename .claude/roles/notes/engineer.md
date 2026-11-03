- Грабли calc: python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке.

## TK-489 (перемер D2 TK-479 после E-010) — ГОТОВО: `docs/findings/statarb-funding-remeasure-2026-11-03.md`, строки 67–68,70 отчёта TK-479 поправлены. Заявки calc 1103022721144 (--digest) и 1103023301277 (чистая скорость); `python -X utf8 tools/compute/statarb/remeasure_table.py table|report|check <id>` (без -I: он глушит PYTHONIOENCODING).

## TK-488 — возврат 2 (Н6–Н8) СДАНО (код 274d692e, отчёт tradfi-candles-2026-11-03.md, вселенная 436 = 168/184/84), ждёт Судью
- calc `/data/tk0488/` (universe3, tradfi-class-table3.csv, tradfi-list3.csv, run-uni3.sh); правило «дни не раньше листинга −1 сут» в tk488_universe.py; MDTUSDT-файл TK-476 уже сшит из двух активов — не трогал. Скрипты копировать scp в `/data/tk0488/bin/`; submit — `--kind net -- bash …`.
## TK-487 (панели .sap, tk487-convert eabdf11f) — ГОТОВО: calc `/data/tk0487/panels-*`, отчёт statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, f940f2ef; in_review у Судьи, E-010 исправлен a62ef6eb)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Выход без задержки 2-й ноги — вне ТЗ. D2 TK-479 мог считать фандинг на пустых ставках — CEO уведомлён комментарием.
- Worktrees ../alpha-tk482-* и ветка tk482-e010-red — удалить после приёмки. Бинари calc `/data/tk0482/alpha-*`.
- Грабли vps-check: одна сборка на тикет (rc 75; CALC_FORCE=1 обходит); два запуска с одним VPS_TAG затирают исходники друг друга; фоновый `( … &)` в Bash умирает — run_in_background.
## TK-486 (скорость этапа 1; tk486-speed 11435b8e, worktree .claude/worktrees/TK-486)
- Замер без v3: пара 2,7e8 пар·отсч/с, corr.rs 3,05e8/ядро, 8 потоков ×7,3; v3 не помог (46 AVX-инстр., цикл не векторизуется, ~7 тактов/отсчёт).
- Сделано: corr.rs блоки; eg.rs (15 сумм, центровать ряд на символ); ядра `--kernel ref|zip|zip2` (умолчание ref). Бинарь `/data/tk0486/alpha-run-k`.
- В полёте: замер ядер юнит 1103022741501, wait до 05:50. Дальше: выбрать ядро → умолчанием после гейта байт-в-байт; суммы EG по блокам; отчёт docs/findings/statarb-speed-<дата>.md (было/стало ×N, строка Алгоритм, предел числом).
- Подача на calc: `cd /data/sched && SCHED_MANIFEST=0 python3 alsched.py submit --name … --max-runtime … --est … --cls prod --kind work --cores N --mem M --reason … --adhoc … -- bash …`. vps-check all красен (check-sprawl) — test/clippy по отдельности.
