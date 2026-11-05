# tools/xdl — скачиватели публичных свечей 1 м и фандинга (TK-493, стат-арбитраж на 9 биржах)

Скрипты написаны 05.11.2026 для теста G-021 на 9 биржах, окно **01.10–04.11.2026 UTC (35 суток, 50 400 минут)**. Только
стандартная библиотека Python 3.11+ (на Софии 3.12, на Steam Deck 3.14), ключей нет — все данные публичные. Лежат здесь **как есть**
(копии с машин закачки: `/opt/xdl/` на VPS София и `~/xdl/` на Steam Deck; sha256 скриптов закачки на машинах и в рабочей копии совпадают; на машинах и в рабочей копии у них окончания строк CRLF, в git по
`.gitattributes` (`*.py eol=lf`) они хранятся как LF — содержимое то же; `check_out.py`, `_hl_check.py`, `_hl_limit.py` на машинах
закачки нет — это вспомогательные скрипты из рабочего каталога сессии).

Общий формат результата (подробно — докстринг `xdl_common.py`):

```
<out>/<биржа>/candles/<SYMBOL>.csv.gz    t_open_ms,o,h,l,c,v,quote_v   (1 мин; у Hyperliquid основная сетка — candles15/, 15 мин)
<out>/<биржа>/funding/<SYMBOL>.csv.gz    time_ms,rate,interval_h
<out>/<биржа>/instruments.csv            поля INSTR_FIELDS (класс, статус, множитель, интервал фандинга…)
<out>/<биржа>/_state/<SYMBOL>.done       метка «символ докачан»; повторный запуск готовое пропускает
<out>/<биржа>/_logs/                     журналы
```

Дыры не дорисовываются (нет свечи — нет строки). Что именно означает дыра, зависит от биржи (см. отчёт
`docs/findings/xdl9-data-gate-2026-11-06.md`): Bitget сам заполняет минуты без сделок плоскими свечами v=0; у Kraken и Lighter такие
свечи при закачке не записывались; OKX их не отдаёт.

## Файлы

| файл | что | биржи |
|---|---|---|
| `xdl_common.py` | общие функции: HTTP с повторами, ограничитель частоты, запись CSV/gzip, метки `.done`, аргументы | все |
| `binance_aster.py` | свечи `/fapi/v1/klines` + архив data.binance.vision (Binance), фандинг `/fapi/v1/fundingRate` | Binance USD-M, Aster |
| `bybit.py` | `/v5/market/kline`, `/v5/market/funding/history`, `/v5/market/instruments-info` | Bybit linear |
| `okx.py` | пакетные файлы static.okx.com (сутки UTC+8) + REST `/api/v5/market/history-candles`, фандинг `funding-rate-history` | OKX SWAP |
| `bingx.py` | `/openApi/swap/v3/quote/klines`, `/openApi/swap/v2/quote/fundingRate` | BingX |
| `bitget.py` | `/api/v2/mix/market/history-candles` + `/candles` (гибрид), `/history-fund-rate` | Bitget USDT-M/USDC-M |
| `kraken_lighter.py` | Kraken `/api/charts/v1/trade/{sym}/1m`, Lighter `/api/v1/candles` | Kraken Futures, Lighter |
| `hyperliquid.py` | POST `/info` (`candleSnapshot`, `fundingHistory`, `allPerpMetas`) | Hyperliquid |
| `check_out.py` | проверка выходных файлов одной биржи (порядок, дубли, плохие бары), с необязательным побайтным сравнением | все |
| `_hl_check.py`, `_hl_limit.py` | одноразовые пробы Hyperliquid (сверка с Binance/Bybit по живому API; лимит запросов) — не часть закачки | Hyperliquid |

## Команды запуска (восстановлены по заголовкам журналов закачки 05.11; общие флаги — `--out --start --end --rate --workers --symbols --limit-symbols --list-only --force`)

```bash
# окно по умолчанию: --start 2026-10-01 --end 2026-11-05 (целые сутки, конец исключительно)
python3 bybit.py          --out OUT --rate 20 --workers 8
python3 okx.py            --out OUT --rate 8 --bulk-rate 8 --workers 6                  # --source auto
python3 bingx.py          --out OUT --rate 8 --workers 8
python3 hyperliquid.py    --out OUT --rate 1.0 --workers 2                              # --intervals 15m,1m
python3 kraken_lighter.py --exchange kraken  --out OUT --rate 3 --workers 2             # перпетуалы PF_*; датированные FF_* — только --include-dated
python3 kraken_lighter.py --exchange lighter --out OUT --rate 2 --workers 2             # спот — только --include-spot
python3 binance_aster.py  --exchange aster   --out OUT --source rest --rate 12 --workers 8
python3 binance_aster.py  --exchange binance --out OUT --rate 2 --workers 4 --shard K/3 # три процесса K=0,1,2; --source auto (архив + REST на пропавшие сутки)
# Bitget докачивали две машины по половинам списка (список символов — через запятую в одну строку):
python3 bitget.py --out OUT --rate 12 --rate-history 4 --workers 8 --symbols "$(cat todo_A.txt)"   # София
python3 bitget.py --out OUT --rate 8  --rate-history 4 --workers 8 --symbols "$(cat todo_B.txt)"   # Steam Deck
python3 check_out.py OUT/<биржа>                                                                    # проверка файлов одной биржи
```

На Steam Deck фоновые запуски — через `systemd-run --user` (не `nohup`); на Софии счёт и пробы запрещены (В-182), допустима закачка с `nice`.

## Проверка полноты

`tools/compute/statarb/xdl9/data_gate.py` строит манифест по всем биржам (покрытие, дыры, v=0, плохие бары, фандинг); `lagcorr.py` —
кросс-корреляцию BTC/ETH/SOL на лагах −3…+3; `compare_tk490.py` — сверку с прежней закачкой пампов. Результаты —
`docs/findings/xdl9-data-gate-2026-11-06.md`.
