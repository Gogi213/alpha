- Грабли calc: python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке.

## TK-489 (перемер D2 TK-479 после E-010; скрипты 276b5176) — waiting до 05:50
- Заявка calc `1103022721144` (remeasure-funding.sh: прежний `/data/tk0479/alpha-tk479` vs новый `/data/tk0482/alpha-e010-plain`, 2 повтора, лог `/data/sched/logs/1103022721144.log`). Дальше: `PYTHONIOENCODING=utf-8 python tools/compute/statarb/remeasure_table.py table|check 1103022721144` → таблица прежнее/новое (старый лог 1102012237287) → в отчёт TK-479 `docs/findings/statarb-core-bench-2026-11-02.md` строки 67, 68, 70: пометка «неверно, E-010» + новые числа; `accept.py log 1103022721144`; result done с --path отчёта.
- Зависят от фандинга только строки 67–68 (скорость funding-pp/ps) и 70 (ставок); пары/кросс/корзины/дрейф/Gate — нет; в ревью tk479-* фандинга нет.

## TK-488 возврат Судьи (код c3186ec9; отчёт и таблица 03.11 05:35) — СДАНО, ждёт Судью
- Результат: вселенная 439 (universe2), `docs/findings/tradfi-candles-2026-11-03.md`, `tradfi-class-table-2026-11-03.csv`, known3 3/3. calc `/data/tk0488/`: coverage-tradfi2.csv, sha-crypto-{before,after} (diff 0), stat-*-{before,after}, fill.done. Стадия/prev `/srv/alpha-sda/candles-1m-488b`.
- Gate funding API: ≤30 сут/запрос, `from` старше 180 сут — 400; листание только `to`. alsched submit требует `--kind net` для лёгкого и `-- bash …` (guard ругается, но ставит). Скрипты на calc копировать scp в `/data/tk0488/bin/` вручную (старый tk488_report.py лежал, md5!). Python на Windows не видит /tmp Git-Bash — копии в data/.
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
