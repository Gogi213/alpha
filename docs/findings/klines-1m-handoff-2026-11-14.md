# Свечи 1m, funding, сделки — передача дел (14.11.2026, ветка `bb/klines-1m-v1`)

Скрипты: `tools/klines/{klines.py,trades.py,launch.sh,push-box.sh}`. На srv копии лежат в `/data/klines/bin/`. Всё идёт юнитами `systemd-run`, зависший ssh ничего не останавливает.

## Что готово (srv `/data/klines/`, Storage Box `alpha/klines/`)

- **П-42 (порог 100 млн):** Binance 227 монет + Bybit XOMUSDT; 1m 15.09–13.11 + разминка 01.09–14.09, funding, `first_qualifying_day.csv`. Проверено, на боксе sha256 сошлись.
- **Вселенная 12 млн** (≥ 12 млн USDT оборота хотя бы в один день 01.09–13.11): Binance 566 монет = 227 старых + 339 новых. 1m новых: 35 804 719 строк, пропущено минут 6 (`哈基米USDT` 06.09 07:15–07:20), 6 неполных дней — листинги/делистинги, 2 расхождения 1d↔1m на один тик (KSTRUSDT 16.09 low, NVDLUSDT 13.09 high), сверка с REST 5/5. Funding: 566 монет, 213 752 строки, 5/5. Манифест `binance/manifest.u12.json` (поля `universe: "12M"`, `universe_symbols`, `universe_origin`), `binance/funding_manifest.u12.json`.
- **Bybit ≥ 12 млн:** 335 монет; 291 есть на Binance (совпадение по базовой монете, псевдонимы PUMPFUN→PUMP, SHIB1000→1000SHIB, цены сходятся 0,97–1,02) — не качались; 44 качаются: 30 только на Bybit, 14 есть на Binance, но там < 12 млн. 1m новых 43 монет: 4 330 978 строк, пропущено 169 минут у 10 монет (сверка с REST 5/5, 1d↔1m без расхождений). Funding 44 монеты, 13 085 строк. Манифест `bybit/manifest.u12.json`, список — `bybit/overlap.u12.json`.
- **Сделки Bybit:** `/data/aggtrades/bybit/<МОНЕТА>/<МОНЕТА><день>.csv.gz`, 44 монеты, 3 087 файлов, 4,4 ГБ, как опубликовано; проверено: размер = Content-Length, весь gzip прочитан (CRC), сумма size по минутам = объём свечи 1m (3 дня, 0 расхождений). Манифест `/data/aggtrades/bybit/manifest.json`.

## Что идёт (на момент записи 16:05 UTC)

- **aggTrades Binance:** юнит `klines-agg-bn`, 41 529 файлов, 60,3 ГБ, ~39 ГБ готово в 16:02, остаток ≈ 15 мин. Лог `/data/aggtrades/run.log`; в конце сам считает проверки и пишет `/data/aggtrades/binance/manifest.json` (zip CRC на 10 файлах, 5 монет за 13.11 против REST, непрерывность agg_trade_id на 3 файлах).
- **Копия на Storage Box:** юнит `klines-box` (свечи/funding, `alpha/klines/`), затем цепочка `klines-agg-box` (`/data/klines/bin/agg-chain.sh`) сама дождётся конца загрузки и зальёт `/data/aggtrades` → `alpha/aggtrades/` (`BW=40000`). Вердикт — последняя строка `/data/klines-box-check/box.log` и `/data/aggtrades-box-check/box.log` (`VERDICT OK|MISMATCH`).

## Что осталось

1. Дождаться обоих `VERDICT OK`; при `MISMATCH` — `diff /data/*-box-check/{local,remote}.sha256`, повторить `push-box.sh` (докачка).
2. Прочитать `binance/manifest.json` aggTrades: `verification.rest_first_100_rows` все `identical: true`, `agg_id_breaks: 0`, `files_on_disk == files_planned`.
3. Итоговый отчёт координатору: числа выше + размеры (`du -sb /data/aggtrades`), время.

## Грабли (на будущее)

- `public.bybit.com` без заголовка `User-Agent` отдаёт 403; после очереди из ~3 тыс. HEAD-запросов восьмью потоками заблокировал IP srv на ~15 мин — теперь 5 запросов/с и пауза с пробой.
- REST `aggTrades` Binance отдаёт только последние 2 суток (код −4166) — сверка с REST возможна только за последний день окна.
- Сумма количеств aggTrades по минутам не равна объёму свечи минута в минуту (агрегат ставится по времени первой сделки и может пересечь границу минуты/суток) — признак полноты — непрерывность `agg_trade_id`.
- Сторож здоровья: `http://127.0.0.1:8088/api/v3` (`rows[].detail.lag_in`, флаги `ok/live_bad/late_bad`); `/data/health/*.json` устарели (12.11).
- Ключ бокса на srv — `/root/.ssh/id_box`, алиас `ssh box` (порт 23).
