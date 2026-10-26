# Сентябрь 2026: OI и фандинг Bybit + Gate (TK-415) — охват

Часть 1 из задачи (OI + фандинг). Mark/index/premium, сделки Bybit и стаканы — следующие записи этого же файла.

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

## Не сделано в этой записи

Bybit mark/index/premium-index 1 м, Gate mark/index 1 м, сделки Bybit (linear + spot, public.bybit.com), замер объёма стаканов
(Gate orderbooks + Bybit архив) — следующие шаги TK-415.
