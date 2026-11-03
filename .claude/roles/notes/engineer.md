- Windows: `C:\…` ломает разделитель `:` — в CLI `,`; Pool в pytest висит; CRLF у .sh → `tr -d '\r'`; git-bash мангрит `k=/data/…`.

## TK-488 (03.11 04:55, TradFi свечи 1 м, В-93) — ветка tk479-statarb; отчёт docs/findings/tradfi-candles-2026-11-03.md ГОТОВ
- Вселенная 440 (Bin 171/Bybit 184/Gate 85), свечи/фандинг поставлены, coverage + таблица класса сняты (docs/findings/tradfi-class-table-2026-11-03.csv). calc `/data/tk0488/` (universe/, coverage-tradfi.csv, tradfi-class-table.csv, sha-*, fund-sha-*).
- Судье списком (в отчёте): pre-IPO/pre-market, дыра Binance 28.10 (архив 404, REST отдаёт) у 170, горизонт 11-01…02, Gate-фандинг. Бэкап: 3 Bybit файла слиты (prev в `/srv/alpha-sda/candles-1m-488/prev`).
- Грабли calc: системный python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке; sha-списки сравнивать awk по пути (comm ломается на порядке).

## TK-487 (панели .sap; tk487-convert, eabdf11f) — ГОТОВО, rc 0
- calc `/data/tk0487/panels-*` (48,7 ГБ), манифест `/data/tk0487/panels-manifest.csv`; отчёт docs/findings/statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, 32e53065; на приёмке)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Worktrees ../alpha-tk482-head, ../alpha-tk482-prev — удалить после job 1103011440821.

## TK-486 (скорость этапа 1; ветка tk486-speed, worktree .claude/worktrees/TK-486) — 04:35
- Замер corr-bench (бинарь БЕЗ v3): прямая пара 2,7e8 пар·отсч/с, матрица corr.rs 3,05e8 на поток (×1,1), 8 потоков ×7,4. Причина — сборка x86-64 без AVX2/FMA (Xeon D-1541: avx2+fma, нет avx512). Диск мимо кэша: 77–85 МБ/с (HDD), тёплый 520–670.
- Подано: сборка v3 `calc.sh buildv3 .` юнит 1103013057390 → `/data/tk0486/alpha-run-v3`; clippy+test юнит 1103013304018 (corr: block_moments/window, окно = сумма блоков по 15 суток). Дальше: `bash /data/tk0486/corr-bench.sh /data/tk0486/alpha-run-v3 "1 8"` через alsched (waiter на 15 ядер не подавать — alsched поднимает work>15 мин до 15). Затем EG/ADF через моменты уровней/лагов, бенч окон «пересчёт vs блоки», отчёт docs/findings/statarb-speed-<дата>.md.
- Остановка бегущего юнита: `systemctl stop tk0s-<имя>-<id>.service` (alsched cancel снимает только очередь).
