- Судье списком (в отчёте): pre-IPO/pre-market, дыра Binance 28.10 (архив 404, REST отдаёт) у 170, горизонт 11-01…02, Gate-фандинг. Бэкап: 3 Bybit файла слиты (prev в `/srv/alpha-sda/candles-1m-488/prev`).
- Грабли calc: python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке.

## TK-488 возврат Судьи (03.11 05:00; код c3186ec9 на tk479-statarb) — в работе на calc
- Вселенная без PREDICTUSDT до 11-02: `/data/tk0488/universe2` (439). Юнит fill2 `1103015533069` (run-fill.sh): fill (REST) + Gate-фандинг + merge (стадия `/srv/alpha-sda/candles-1m-488b`, prev там же) + stat до/после + coverage → `coverage-tradfi2.csv`, done = `fill.done`. sha крипто Binance до: `sha-crypto-before.txt` (юнит ...539216); после — `bash sha-crypto.sh after`, сверка diff.
- Осталось: отчёт заново (tk488_report на universe2 + coverage-tradfi2 + фандинг Gate), known3 — `docs/findings/tk488-known3-2026-11-03.csv` (3/3 совпало), в md: Н1–Н5, REST-строки без n_trades у Bybit/Gate, Gate v=0 минуты не пишутся.
- Gate funding API: ≤30 сут/запрос, `from` старше 180 сут — 400; pagination только `to`. tk478-funding.py теперь с guard `__main__`.
## TK-487 (панели .sap; ветка tk487-convert, eabdf11f) — ГОТОВО, rc 0
- calc `/data/tk0487/panels-*` (манифест `/data/tk0487/panels-manifest.csv`), отчёт docs/findings/statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`; ssh: -i + UserKnownHostsFile, root@89.163.242.211.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, f940f2ef; in_review у Судьи, E-010 исправлен a62ef6eb)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Выход без задержки 2-й ноги — вне ТЗ. D2 TK-479 мог считать фандинг на пустых ставках — CEO уведомлён комментарием.
- Worktrees ../alpha-tk482-{head,prev,prev2,red,green} — удалить после приёмки; ветка tk482-e010-red. Бинари calc `/data/tk0482/alpha-{e010-plain,prev,prev2-plain}`.
- Грабли vps-check: одна сборка на тикет (`build-TK-482 уже идёт`, rc 75; CALC_FORCE=1 обходит) — тесты/clippy подавать по очереди; две заявки с одним VPS_TAG делят слот и затирают исходники друг друга (красный/зелёный в параллель — только с разными TAG и по одному); фоновый `( … &)` в Bash-инструменте умирает — run_in_background.
## TK-486 (скорость этапа 1; ветка tk486-speed 11435b8e, worktree .claude/worktrees/TK-486) — 05:20
- Замер: прямая пара 3,0e8 пар·отсч/с; матрица corr.rs (ref) 3,09e8/ядро и на plain, и на v3 (бинарь v3 имеет всего 46 AVX-инструкций) → цикл не векторизуется (вероятно проверки границ `ma[e]`), а не «нет AVX2». 8 потоков ×7,3. HDD мимо кэша 77–85 МБ/с.
- Сделано: corr.rs блоки + `--kernel ref|zip|zip2` (zip без границ, zip2 по 2 строки; тест байт в байт `kernels_are_bit_identical`), eg.rs (EG по 15 суммам). vps-check `all` отказывает (check-sprawl: tools/compute 741 > 83) → гонять `test statarb` и `clippy` по отдельности; tests c8d518dd — юнит 1103021135552, тесты corr 11435b8e — 1103021405231.
- В полёте: сборка v3 коммита 11435b8e → `/data/tk0486/alpha-run-k` (юнит 1103021524887, свой target-v3; скрипт-копия tools/vps-check-v3.tmp.sh в worktree — не коммитить, удалить). Затем: `alsched submit` `bash /data/tk0486/corr-bench-k.sh /data/tk0486/alpha-run-k 300 1` (cores 1) и `… 900 8` (cores 8); сравнить ref/zip/zip2.
- Дальше: выбрать ядро (умолчанием — после гейта); EG-суммы по блокам в corr.rs, бенч окон, отчёт docs/findings/statarb-speed-<дата>.md (было/стало).
- ssh-обёртка /tmp/sshx глотает первую команду: начинать `true; …`. vps-check при уже идущем build-TK-486 отказывает дублем — `CALC_FORCE=1`.
