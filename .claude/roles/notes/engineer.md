- alsched: `--max-runtime` > 900 или голый `bash скрипт` → 15 ядер (NX); подавай `-- bash -c "…"`. Бин: `BIN_OUT=/data/tk0486/alpha-run VPS_TAG=486 CALC_FORCE=1 bash tools/vps-check.sh . bin`.
- Подача: `cd /data/sched && SCHED_MANIFEST=0 python3 alsched.py submit --name X --cls prod --kind work --cores N --mem M --max-runtime S --adhoc "почему" --vypiska steps.tsv -- bash run.sh`; rc `/data/sched/rc/<id>`, лог `/data/sched/logs/<id>.log`. guard: `/data/registry/guard.py plan --steps …`.
- Валидатор ярлыков договора: `[чтение кода]` и `[вживую]` — БЕЗ команды; `[тест: python -m pytest <файл из git>]`, `[запуск: python tools/…]` (argv[0] python/python3/bash, без `-I`, скрипт и тест — уже закоммичены). Договор: `T comment <ID> --author engineer --text …`.
- Windows: `C:\…` ломает разделитель `:` — в CLI `,`; Pool в pytest висит; CRLF у .sh → `tr -d '\r'`; git-bash мангрит `k=/data/…`.

## TK-488 (03.11 04:30, TradFi свечи 1 м, В-93) — ветка tk479-statarb, коммит e7fe6dc0
- Сделано: договор в логе; `tools/compute/tk488_tradfi_list.py` (метаданные 3 бирж → `data/tk488/tradfi-list.csv`, сырые json `data/tk488/raw`) + тест (3 зелёных). Живое 04:22: Binance 238, Bybit 298 (снятых TradFi нет), Gate 475.
- Метаданные: Binance `underlyingType` EQUITY/HK_/KR_/CN_EQUITY/COMMODITY/FX/PREMARKET/INDEX (INDEX+Crypto = BTCDOM/DEFI/ALL — не TradFi), contractType TRADIFI_PERPETUAL; Bybit `symbolType` stock/ETF/commodity/forex + `marketRegion` (innovation — крипто); Gate `contract_type` stocks/indices/metals/forex/commodities (+`is_pre_market`).
- Дальше: (1) порог 1 млн TradFi в вселенной: флаг в tk476_universe.py (умолчание = старое) + тест; снятые Binance/Gate — по архивам; (2) закачка дельты: Binance data.binance.vision (tk476-binance-new.py, TradFi с 2026), Bybit REST, Gate месячные архивы; (3) фандинг как tk478-funding.py; (4) coverage + отчёт `docs/findings/tradfi-candles-<дата>.md`; sha лежащих файлов до/после.

## TK-487 (панели .sap; ветка tk487-convert, worktree TK-487) — подан alsched `1103011144879` (04:12, 15 яд.)
- calc `/data/tk0487/{panels-<биржа>,log,missing-*.txt,panels-manifest.csv,verify.txt}`; бин `/data/tk0484/alpha-run-plain`. Дальше: manifest + verify → отчёт `docs/findings/statarb-panels-2026-11-03.md` (время, место, пустые, строк/с против 4,33 млн/с) → result done. Run-скрипт не закоммичен.

## TK-484 (tk484-pipeline, 6d0483e2; на приёмке) и TK-482 (tk479-statarb, 32e53065; на приёмке)
- Осталось: слияние TK-482 (конфликт tests.rs) → Params в run.rs (delay_x, rule); формирование под П-32 v4; «было/стало» (В-90). Worktrees ../alpha-tk482-head, ../alpha-tk482-prev — удалить после job 1103011440821.
