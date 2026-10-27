# Сентябрь 2026: OI и фандинг Bybit + Gate (TK-415) — охват

Части: 1 — OI + фандинг; 2 — mark/index/premium; 3 — стаканы; сделки Bybit — TK-417.

Список инструментов — охват свечей TK-411 (строки с минутами данных > 0): Bybit linear 931, Gate фьючерсы USDT 1018.
Период 2026-09-01 00:00 — 2026-10-01 00:00 UTC. Данные как отдаёт биржа, без агрегатов и пересчётов.
Файлы на calc: `/srv/alpha-sda/tk415/out/{bybit,gate}/{oi,funding}/<СИМВОЛ>.csv.gz`, охват по инструментам —
`/srv/alpha-sda/tk415/out/coverage.csv` (копия в `docs/findings/sep-oi-funding-coverage-2026-10-27.csv`, 3898 строк).
Скрипты: `tools/compute/tk415-oi-funding.py` (закачка), `tools/compute/tk415-finalize.py` (охват).

## Что скачано

| биржа | вид | источник | шаг | инструментов с данными | строк | размер |
|---|---|---|---|---|---|---|
| Bybit | OI | `/v5/market/open-interest`, intervalTime=5min (самый мелкий шаг истории) | 5 мин | 931 из 931 | 7 565 784 | 66,9 МБ |
| Bybit | фандинг | `/v5/market/funding/history` | по событию | 859 из 931 | 114 249 | 0,6 МБ |
| Gate | OI + long/short + ликвидации | `/futures/usdt/contract_stats`, interval=5m | 5 мин | 1018 из 1018 | 8 464 249 | 258,3 МБ |
| Gate | фандинг | `/futures/usdt/funding_rate` | по событию | 1018 из 1018 | 125 391 | 0,6 МБ |

Колонки: Bybit OI `t_ms, open_interest, single_open_interest`; Gate `contract_stats` — все 25 полей биржи
(`lsr_taker, lsr_account, long_liq_size, short_liq_size, open_interest, open_interest_usd, mark_price, top_*, long_taker_size,
short_taker_size, long_users, short_users, last_funding_rate, *_liq_usd, *_liq_amount` …); фандинг `t_ms, funding_rate`.

## Полнота

- **Полный месяц OI (8640 строк при 5 мин):** Bybit 817 из 931, Gate 920 из 1018.
- **Неполный OI:** Bybit 114 (72 датированных фьючерса `BTCUSDT-18SEP26` и т. п. + 42 бессрочных;
  у 35 ряд кончается раньше конца месяца), Gate 98 (листинг внутри сентября; 234…8467 строк). У Gate рядов, кончившихся раньше конца, нет.
- **Фандинга нет у 72 инструментов Bybit** — все датированные фьючерсы (у них нет фандинга по построению).
- Gate `contract_stats` отдаёт и 1-минутный шаг (проба BTC); качали 5 мин, как просили. Минутный на 1018 инструментов ≈ ещё 37 мин закачки.
- **Ликвидации Gate:** поля `long_liq_*`/`short_liq_*` в `contract_stats` — ненулевые суммы у 968 из 1018 инструментов.
  **Ликвидации Bybit:** истории в открытом доступе нет, только живой WS — за сентябрь не получить; в охвате пометка.

## Сверка выборки с API (27.10, живой запрос → файл)

Случайные инструменты (seed 7), окно из середины ряда / первой записи; сравнение значение-в-значение по общим меткам времени:

- Bybit OI: BTCUSDT-18SEP26, TMXUSDT, AWEUSDT, ADIUSDT, HDUSDT, DRIFTUSDT — по 5 меток, все равны.
- Gate OI: XBI, MELI, IDOL, INIT, ORBIO, AMZN — по 31 метке, все равны.
- Фандинг Bybit: OPENUSDT, SUIPERP, GBPUSDUSDT, EIGENPERP — 4–5 ставок, все равны; Gate: XVS, BIO, ESP, PROLOGUE — 3–5 ставок, все равны.
- Расхождений 0 из 20 выборок.

## Часть 2: mark / index / premium-index (calc:/srv/alpha-sda/tk415/out/{bybit,gate}/{mark,index,premium}/)

| Биржа | Ряд | Источник, шаг | Инструментов с данными | Полный месяц | Строк | Размер |
|---|---|---|---|---|---|---|
| Bybit | mark | `/v5/market/mark-price-kline`, 1 мин | 896 из 931 | 817 (43 200 строк) | 37,17 млн | 282 МБ |
| Bybit | index | `index-price-kline`, 1 мин | 856 из 931 | 809 | 36,08 млн | 268 МБ |
| Bybit | premium | `premium-index-price-kline`, 1 мин | 856 из 931 | 809 | 36,08 млн | 225 МБ |
| Gate | mark | `/futures/usdt/candlesticks` `mark_<С>`, **15 мин** | 1018 из 1018 | 920 (2 880 строк) | 2,82 млн | 26 МБ |
| Gate | index | `index_<С>`, **15 мин** | 1018 из 1018 | 920 | 2,82 млн | 31 МБ |

- Gate за сентябрь отдаёт mark/index только 15 мин: 1 м и 5 м — HTTP 400 (глубина 10 000 точек, 15 м = 104 сут.). Отдельного
  ряда premium у Gate нет.
- Сверка с живым API (Bybit, 12 случайных минут: mark/index/premium × BTC/SOL/DOGE/HYPE): равны 12/12.
- Охват по инструментам — `coverage.csv` (вид mark/index/premium); обновлён тем же `tk415-finalize.py`.

## Часть 3: стаканы (calc:/data/tk415/ob/{gate,bybit}/<рынок>/<СИМ>/), событийные, как отдаёт архив

Окно — весь сентябрь (решение CEO 27.10: границы пампов не знаем). Список — 390 инструментов
(`/root/obsize-list.tsv`): ликвидные 85 + остальные волатильные с эпизодами TK-413. Делисты с эпизодами — часть 4 ниже.

| Источник | Рынок | Инструментов | Файлов | Размер (gz) |
|---|---|---|---|---|
| Gate `download.gatedata.org/{futures_usdt,spot}/orderbooks/202609/` | фьюч | 97 | 61 636 (час) | 94 ГБ |
| Gate | спот | 226 | 158 850 (час) | 49 ГБ |
| Bybit `quote-saver.bycsi.com/orderbook/{linear,spot}/` ob200 | linear | 50 | 1 404 (сутки) | 45 ГБ |
| Bybit | спот | 17 | 510 (сутки) | 6,2 ГБ |

- Итого 222 400 файлов, 194 ГБ; свободно на /data после закачки 754 ГБ. Файлы с HEAD-кодом 404 не качались (в архиве нет).
- Gate: калибровка потоков (`_calib.tsv`) 16→9,1 / 32→23,0 / 64→29,9 МБ/с, 429/403 не было ни разу; бой 64 потока ≈ 17 МБ/с
  (потолок — число запросов ≈ 15–30/с на IP). Ошибки боя (таймаут/сброс/обрыв) повторены перезапуском: итоговая сверка
  «очередь = файлов на диске» 220 486 = 220 486, `_missing.tsv` пуст. 7 осиротевших `.part` (FOLD_USDT спот) — не файлы данных.
- Bybit: потолок канала ≈ 45–50 МБ/с, выше 4 потоков не растёт; 1 914/1 914, ошибок 5 (повторены).
- Скрипт — `tools/compute/tk415-ob-dl.py`, ход — `_progress.log` в каталогах `ob/gate`, `ob/bybit`.

## Не сделано / вне TK-415

- Сделки Bybit (linear + spot, public.bybit.com) — вынесены в **TK-417** (CEO 27.10 03:25).
- Ликвидации Bybit — истории в открытом доступе нет, только живой WS (в архив не попадают). У Gate ликвидации — поля
  `long_liq_size/short_liq_size` в contract_stats (часть 1).
- Gate: делистов с эпизодами нет (TK-413 v3: все 1018 фьюч. и спот-пары с данными торгуются на 27.10), стаканы Gate для делистов не качались.

## Часть 4: стаканы делистов с эпизодами (27.10, возврат Судьи)

Список — 33 делиста Bybit linear с ≥ 1 эпизодом θ=5 % из TK-413 v3 (`docs/findings/pump-dump-episodes-sep-2026-10-27.csv`, категория «делист»:
3 снятые монеты ASP/ICX/SCRT + 30 датированных фьючерсов BTC/ETH/SOL/XRP/DOGE/HYPE/MNT на 04, 11, 18, 25.09 и XAUT на 18, 25.09) + спот тех трёх монет.
Без HEAD: тем же `tk415-ob-dl.py` (4 потока, колено Bybit из части 3) по всем 30 суткам; 404 = «в архиве нет». Job calc `1027020533666` (net),
выход `calc:/data/tk415/ob-delist/bybit-{linear,spot}/<СИМ>/`, список `/data/tk415/delist-list.tsv`, скрипт таблицы `tools/compute/tk415-delist-table.py`.
Закачка 3 мин: 505 файлов, 1,66 ГБ, ошибок и 429/403 нет, `_fails.tsv` пуст; 1080 запросов = 505 файлов + 575 «404». /data после закачки 753 ГБ свободно.

| рынок | инструмент | файлов (дней) | ГБ | 404 (дней нет) | дни с файлами | не получено |
|---|---|---|---|---|---|---|
| bybit linear | ASPUSDT | 5 | 0.01 | 25 | 01–05 | 0 |
| bybit linear | BTCUSDT-04SEP26 | 4 | 0.02 | 26 | 01–04 | 0 |
| bybit linear | BTCUSDT-11SEP26 | 11 | 0.05 | 19 | 01–11 | 0 |
| bybit linear | BTCUSDT-18SEP26 | 18 | 0.06 | 12 | 01–18 | 0 |
| bybit linear | BTCUSDT-25SEP26 | 25 | 0.24 | 5 | 01–25 | 0 |
| bybit linear | DOGEUSDT-04SEP26 | 4 | 0.01 | 26 | 01–04 | 0 |
| bybit linear | DOGEUSDT-11SEP26 | 11 | 0.04 | 19 | 01–11 | 0 |
| bybit linear | DOGEUSDT-18SEP26 | 18 | 0.04 | 12 | 01–18 | 0 |
| bybit linear | DOGEUSDT-25SEP26 | 25 | 0.07 | 5 | 01–25 | 0 |
| bybit linear | ETHUSDT-04SEP26 | 4 | 0.02 | 26 | 01–04 | 0 |
| bybit linear | ETHUSDT-11SEP26 | 11 | 0.07 | 19 | 01–11 | 0 |
| bybit linear | ETHUSDT-18SEP26 | 18 | 0.09 | 12 | 01–18 | 0 |
| bybit linear | ETHUSDT-25SEP26 | 25 | 0.23 | 5 | 01–25 | 0 |
| bybit linear | HYPEUSDT-04SEP26 | 4 | 0.00 | 26 | 01–04 | 0 |
| bybit linear | HYPEUSDT-11SEP26 | 11 | 0.03 | 19 | 01–11 | 0 |
| bybit linear | HYPEUSDT-18SEP26 | 18 | 0.04 | 12 | 01–18 | 0 |
| bybit linear | HYPEUSDT-25SEP26 | 25 | 0.02 | 5 | 01–25 | 0 |
| bybit linear | ICXUSDT | 18 | 0.14 | 12 | 01–18 | 0 |
| bybit linear | MNTUSDT-04SEP26 | 4 | 0.01 | 26 | 01–04 | 0 |
| bybit linear | MNTUSDT-11SEP26 | 11 | 0.02 | 19 | 01–11 | 0 |
| bybit linear | MNTUSDT-18SEP26 | 18 | 0.02 | 12 | 01–18 | 0 |
| bybit linear | MNTUSDT-25SEP26 | 25 | 0.04 | 5 | 01–25 | 0 |
| bybit linear | SCRTUSDT | 5 | 0.03 | 25 | 01–05 | 0 |
| bybit linear | SOLUSDT-04SEP26 | 4 | 0.01 | 26 | 01–04 | 0 |
| bybit linear | SOLUSDT-11SEP26 | 11 | 0.03 | 19 | 01–11 | 0 |
| bybit linear | SOLUSDT-18SEP26 | 18 | 0.04 | 12 | 01–18 | 0 |
| bybit linear | SOLUSDT-25SEP26 | 25 | 0.06 | 5 | 01–25 | 0 |
| bybit linear | XAUTUSDT-18SEP26 | 18 | 0.02 | 12 | 01–18 | 0 |
| bybit linear | XAUTUSDT-25SEP26 | 25 | 0.00 | 5 | 01–25 | 0 |
| bybit linear | XRPUSDT-04SEP26 | 4 | 0.01 | 26 | 01–04 | 0 |
| bybit linear | XRPUSDT-11SEP26 | 11 | 0.05 | 19 | 01–11 | 0 |
| bybit linear | XRPUSDT-18SEP26 | 18 | 0.06 | 12 | 01–18 | 0 |
| bybit linear | XRPUSDT-25SEP26 | 25 | 0.02 | 5 | 01–25 | 0 |
| bybit spot | ASPUSDT | 0 | 0.00 | 30 | — | 0 |
| bybit spot | ICXUSDT | 24 | 0.06 | 6 | 01–24 | 0 |
| bybit spot | SCRTUSDT | 4 | 0.01 | 26 | 01–04 | 0 |
| **итого** | | **505** | **1.66** | **575** | | **0** |

- Файлы лежат подряд с 1-го числа до последних суток жизни инструмента (датированные: 04→01–04, 11→01–11, 18→01–18, 25→01–25; пропусков внутри нет), дальше 404 — инструмента уже нет. Снятые монеты: ICX linear — до 18.09, ICX спот — до 24.09, ASP и SCRT linear — до 05.09, SCRT спот — до 04.09; ASP спот в архиве нет совсем (30 из 30 — 404).
- Сентябрь целиком по каждому делисту, не по окнам эпизодов (как и остальные стаканы). XAUTUSDT-04SEP26 и -11SEP26 (без эпизодов) не качались.