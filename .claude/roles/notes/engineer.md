- Судье списком (в отчёте): pre-IPO/pre-market, дыра Binance 28.10 (архив 404, REST отдаёт) у 170, горизонт 11-01…02, Gate-фандинг. Бэкап: 3 Bybit файла слиты (prev в `/srv/alpha-sda/candles-1m-488/prev`).
- Грабли calc: python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке.

## TK-488 возврат Судьи (03.11 05:00; код c3186ec9 на tk479-statarb) — в работе на calc
- Вселенная без PREDICTUSDT до 11-02: `/data/tk0488/universe2` (439). Юнит fill2 `1103015533069` (run-fill.sh): fill (REST) + Gate-фандинг + merge (стадия `/srv/alpha-sda/candles-1m-488b`, prev там же) + stat до/после + coverage → `coverage-tradfi2.csv`, done = `fill.done`. sha крипто Binance до: `sha-crypto-before.txt` (юнит ...539216); после — `bash sha-crypto.sh after`, сверка diff.
- Осталось: отчёт заново (tk488_report на universe2 + coverage-tradfi2 + фандинг Gate), known3 — `docs/findings/tk488-known3-2026-11-03.csv` (3/3 совпало), в md: Н1–Н5, REST-строки без n_trades у Bybit/Gate, Gate v=0 минуты не пишутся.
- Gate funding API: ≤30 сут/запрос, `from` старше 180 сут — 400; pagination только `to`. tk478-funding.py теперь с guard `__main__`.
## TK-487 (панели .sap, tk487-convert eabdf11f) — ГОТОВО: calc `/data/tk0487/panels-*`, отчёт statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, f940f2ef; in_review у Судьи, E-010 исправлен a62ef6eb)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Выход без задержки 2-й ноги — вне ТЗ. D2 TK-479 мог считать фандинг на пустых ставках — CEO уведомлён комментарием.
- Worktrees ../alpha-tk482-{head,prev,prev2,red,green} — удалить после приёмки; ветка tk482-e010-red. Бинари calc `/data/tk0482/alpha-{e010-plain,prev,prev2-plain}`.
- Грабли vps-check: одна сборка на тикет (`build-TK-482 уже идёт`, rc 75; CALC_FORCE=1 обходит) — тесты/clippy подавать по очереди; две заявки с одним VPS_TAG делят слот и затирают исходники друг друга (красный/зелёный в параллель — только с разными TAG и по одному); фоновый `( … &)` в Bash-инструменте умирает — run_in_background.
## TK-486 (скорость этапа 1; tk486-speed c8d518dd, worktree .claude/worktrees/TK-486)
- Замер без v3: пара 2,7e8 пар·отсч/с, corr.rs ×1,1/ядро, 8 потоков ×7,4; HDD мимо кэша 77–85 МБ/с. Сборка `/data/tk0486/alpha-run-v3`.
- Сделано: corr.rs блоки, eg.rs (суммы блоков; центровать ряд на символ до сумм) — перепроверить лог `/tmp/tk486-check-c8d518dd.log`. В полёте corr-bench v3: юнит 1103015629095, wait до 05:40.
- Дальше: ядро сумм EG по блокам (15 сумм, маска m_t·m_{t−1}), бенч «пересчёт vs блоки», отчёт docs/findings/statarb-speed-<дата>.md. ssh-обёртка /tmp/sshx глотает первую команду: начинать `true; …`.
