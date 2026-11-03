- Подача: `cd /data/sched && SCHED_MANIFEST=0 python3 alsched.py submit --name X --cls prod --kind work --cores N --mem M --max-runtime S --adhoc "почему" --vypiska steps.tsv -- bash run.sh`; rc `/data/sched/rc/<id>`, лог `/data/sched/logs/<id>.log`. guard: `/data/registry/guard.py plan --steps …`.
- Валидатор ярлыков договора: `[чтение кода]` и `[вживую]` — БЕЗ команды; `[тест: python -m pytest <файл из git>]`, `[запуск: python tools/…]` (argv[0] python/python3/bash, без `-I`, скрипт и тест — уже закоммичены). Договор: `T comment <ID> --author engineer --text …`.
- Windows: `C:\…` ломает разделитель `:` — в CLI `,`; Pool в pytest висит; CRLF у .sh → `tr -d '\r'`; git-bash мангрит `k=/data/…`.

## TK-488 (03.11 04:36, TradFi свечи 1 м, В-93) — ветка tk479-statarb, коммиты e7fe6dc0, 0ce8d9a6, 2a2014f6
- Сделано: `tk488_tradfi_list.py` (Bin 238, Bybit 298, Gate 475), `tk488_universe.py` (порог 1 млн, вселенная 440: Bin 171/Bybit 184/Gate 85), `tk488_report.py` (итоговая таблица класса); тесты 4+7+2 зелёные. calc `/data/tk0488/` (universe/, run-*.sh, steps-*.tsv, sha-*.txt).
- Свечи ПОСТАВЛЕНЫ 04:31 (rc 0): Binance +4 нового (IBKR/MCD/MSTU/USDEX, конец 11-01 23:59, у остальных 10-31), Bybit AEHR/GILD/QNTX слиты (43200 общих минут, 0 расхождений; прежние в `/srv/alpha-sda/candles-1m-488/prev`, sha в `/data/tk0488/sha-{before,after,prev}.txt`).
- Идут: фандинг job `1103013209590` (только недостающие Binance 21, Bybit 7; sha до/после `fund-sha-*.txt`, логи fund-{by,bn}.log, `fund.done`); coverage 440 job `1103013242438` (`run-cov.sh` → `/data/tk0488/coverage-tradfi.csv`, ссылки cov-links/, `cov-nofile.txt`). wait до 05:10.
- Дальше: (1) `python tools/compute/tk488_report.py data/tk488/tradfi-list.csv <universe> coverage-tradfi.csv /srv/alpha-sda/funding docs/findings/tradfi-class-table-2026-11-03.csv` (скопировать с calc tradfi-list.csv, universe/, coverage); (2) отчёт `docs/findings/tradfi-candles-2026-11-03.md`; (3) Судье списком: pre-IPO, Gate pre-market, USD1, Binance/Gate снятые вне метаданных, Gate-фандинг (TK-478 не качал); (4) result done --path docs/findings/tradfi-candles-2026-11-03.md.
- ssh: `/tmp/sshx root@89.163.242.211`; `alsched submit`: --max-runtime ≤ 900 или --est.

## TK-487 (панели .sap; tk487-convert, eabdf11f) — ГОТОВО, rc 0
- calc `/data/tk0487/panels-*` (48,7 ГБ), манифест `/data/tk0487/panels-manifest.csv`; отчёт docs/findings/statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, 32e53065; на приёмке)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Worktrees ../alpha-tk482-head, ../alpha-tk482-prev — удалить после job 1103011440821.

## TK-486 (скорость этапа 1; ветка tk486-speed, worktree .claude/worktrees/TK-486) — 04:35
- Замер corr-bench (бинарь БЕЗ v3): прямая пара 2,7e8 пар·отсч/с, матрица corr.rs 3,05e8 на поток (×1,1), 8 потоков ×7,4. Причина — сборка x86-64 без AVX2/FMA (Xeon D-1541: avx2+fma, нет avx512). Диск мимо кэша: 77–85 МБ/с (HDD), тёплый 520–670.
- Подано: сборка v3 `calc.sh buildv3 .` юнит 1103013057390 → `/data/tk0486/alpha-run-v3`; clippy+test юнит 1103013304018 (corr: block_moments/window, окно = сумма блоков по 15 суток). Дальше: `bash /data/tk0486/corr-bench.sh /data/tk0486/alpha-run-v3 "1 8"` через alsched (waiter на 15 ядер не подавать — alsched поднимает work>15 мин до 15). Затем EG/ADF через моменты уровней/лагов, бенч окон «пересчёт vs блоки», отчёт docs/findings/statarb-speed-<дата>.md.
- Остановка бегущего юнита: `systemctl stop tk0s-<имя>-<id>.service` (alsched cancel снимает только очередь).
