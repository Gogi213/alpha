- Судье списком (в отчёте): pre-IPO/pre-market, дыра Binance 28.10 (архив 404, REST отдаёт) у 170, горизонт 11-01…02, Gate-фандинг. Бэкап: 3 Bybit файла слиты (prev в `/srv/alpha-sda/candles-1m-488/prev`).
- Грабли calc: системный python3 без numpy — `/data/tk421/venv/bin/python`; universe-*.csv: символ во 2-й колонке; sha-списки сравнивать awk по пути (comm ломается на порядке).

## TK-488 возврат Судьи (03.11 05:00; код c3186ec9 на tk479-statarb) — в работе на calc
- Вселенная без PREDICTUSDT до 11-02: `/data/tk0488/universe2` (439). Юнит fill2 `1103015533069` (run-fill.sh): fill (REST) + Gate-фандинг + merge (стадия `/srv/alpha-sda/candles-1m-488b`, prev там же) + stat до/после + coverage → `coverage-tradfi2.csv`, done = `fill.done`. sha крипто Binance до: `sha-crypto-before.txt` (юнит ...539216); после — `bash sha-crypto.sh after`, сверка diff.
- Осталось: отчёт заново (tk488_report на universe2 + coverage-tradfi2 + фандинг Gate), known3 — `docs/findings/tk488-known3-2026-11-03.csv` (3/3 совпало), в md: Н1–Н5, REST-строки без n_trades у Bybit/Gate, Gate v=0 минуты не пишутся.
- Gate funding API: ≤30 сут/запрос, `from` старше 180 сут — 400; pagination только `to`. tk478-funding.py теперь с guard `__main__`.
## TK-487 (панели .sap; ветка tk487-convert, eabdf11f) — ГОТОВО, rc 0
- calc `/data/tk0487/panels-*` (манифест `/data/tk0487/panels-manifest.csv`), отчёт docs/findings/statarb-panels-2026-11-03.md. Бин `/data/tk0484/alpha-run-plain`; ssh: -i + UserKnownHostsFile, root@89.163.242.211.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, 32e53065; на приёмке)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Worktrees ../alpha-tk482-head, ../alpha-tk482-prev — удалить после job 1103011440821.

## TK-486 (скорость этапа 1; ветка tk486-speed c8d518dd, worktree .claude/worktrees/TK-486) — 04:58
- Замер без v3: прямая пара 2,7e8 пар·отсч/с, матрица corr.rs ×1,1/ядро, 8 потоков ×7,4; HDD мимо кэша 77–85 МБ/с. Сборка v3 готова: `/data/tk0486/alpha-run-v3` (юнит 1103013057390 rc 0).
- Сделано: corr.rs блоки (RowStrip — clippy), eg.rs (EG по 15 суммам, окно/половины = суммы блоков; центровать ряд на символ до сумм). Тест eg на calc зелёный до правки центровки α — перепроверить: лог `/tmp/tk486-check-c8d518dd.log` (vps-check all, VPS_TAG=-tk486c).
- В полёте: corr-bench на v3 (1 и 8 потоков) юнит 1103015629095, лог `/data/sched/logs/1103015629095.log`; wait до 05:40.
- Дальше: ядро сумм EG по блокам (15 сумм вместо 6, маска w = m_t·m_{t−1}, 5 признаков на ногу + 4 кросс) в corr.rs, бенч окон «пересчёт vs блоки», отчёт docs/findings/statarb-speed-<дата>.md. alsched submit: бинарь вне манифеста — предупреждение, но принимает.
- ssh-обёртка /tmp/sshx глотает первую команду: начинать `true; …`. vps-check test на calc может стоять в очереди > 7 мин.
