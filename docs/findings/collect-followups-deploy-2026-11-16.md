# G-022: выкладка доработок коллекторов (follow-ups v2) в бой (16.11.2026)

Ветка `bb/g-022-collect-followups-deploy-v1-thr_fayd2ss7im` (основа — `065ac7e2`). Код — `3835be7b` (`bb/g-022-collect-followups-v2-thr_3scdpt5jec`), отчёт доработок — `collect-followups-2026-11-16.md` §7, вердикт проверяющего — `collect-followups-review-2026-11-16.md`, раздел «Повторная проверка v2» («выкладывать на все пять бирж при условии Н1»). Бинарь `/data/turbo-probe/alpha-followups-3835be7b` (md5 `1a1955dd1425273ecb280ae03d1c2d1f`, `ALPHA_COMMIT=3835be7b` вшит, сборка на srv `binrel`). Порядок: Bybit → OKX → BingX → Gate → Binance, по одной, каждая с готовым откатом (`/data/perf/rollback.sh <юнит>` возвращает `<бинарь>.prev-34906d42` — боевой пул-фикс 9c44e141 — и прежние аргументы). Время — UTC (часы srv); GMT+4 = UTC + 4 ч.

## Метод приёмки

Тот же, что в `collect-pool-fix-deploy-2026-11-16.md` (приёмка по своим счётчикам, не по «OK» наката — `rollout.sh` снова дал «OK» через 1–14 с по последней записи health старого процесса), плюс счётчики доработок:

1. Символы с данными за минуту против `pool.rest_eligible`; независимый счётчик `symbols.not_subscribed` и `rest_checked` (≈ `rest_eligible`); `pool.over_cap` пуст, `max_shard_load`, `rates_tracked`, `spawned_shards`; файл `pool-state.json`.
2. Сверка с REST за первый час после выкладки (`tools/collect_gap/postcheck.sh`, окно запуска +2 … +62 мин, «до» — час 04:50–05:50 UTC на боевом пул-фиксе), по таймеру `fu-after60-<биржа>`.
3. Дозапрос: `backfill.ids_confirmed = ids_backfilled + ids_absent + ids_lost + open_ids`, `ids_lost_1h`, `rate_limited`, `paused_s`, файл `backfill-*.tsv.zst` (`tools/collect_gap/bfsum.py`).
4. Режим «стоянка» (`storm.episodes/holds/aborts`, события `storm_hold` / `storm_abort`), `connect_budget` (`alarm`, `max_wait_s`, `waits_total`), паузы `gaps_1h` / `gaps_1h_nontrade`, переподключения, таймауты.
5. Лаг прихода p50 / p99 (`accept.py` по записи health раз в 30 с — юнит `fu-watch`, `/data/perf/followups-deploy/watch-<биржа>.jsonl`): «до» — 30 мин перед накатом, «после» — с 90-й секунды.
6. Binance: сторож `book03` / `book05` (`binwatch.py`), условие отката — как в прошлой выкладке.

Инструменты в ветке: `tools/collect_gap/fucheck.py` (снимок приёмки одной биржи), `bfsum.py` (сводка файла дозапроса), `watch.py` (+ `backfill`, `storm`, `connect_budget`, `gaps_1h_nontrade`), `aggregate.py` (пропускает `backfill-*` — файлы дозапроса не данные ws; без правки сверка с `--only <запуск>` подхватила бы их).

## Условие проверяющего (Н1) — страница, выложена первой

`v3.py`: строка биржи не `ok`, если `backfill.ids_lost_1h > 0` (health считает по времени пропуска на каждый снимок; в ряд минут поздняя потеря не попадает — строка минуты пишется один раз, итог дозапроса приходит позже). Число — в подробностях строки («за час N, в столбиках M»), поле строки `backfill_lost_1h`. Тест `test_late_backfill_loss_from_health_turns_the_row_red_without_a_series_row` (потеря за час в health при пустом ряде → красная; `ids_lost_1h = 0`, дозапрос выключен, сборка без поля → не красит); страница `test_health.py` — 24 из 24 (локально и на srv с боевым `serve.py` и новой `page.html`); на коде до правки новый тест красный (`KeyError backfill_lost_1h`).

Выложено 05:55:27 UTC (до первого наката): `/data/turbo-probe/page/{v3.py, page.html, test_health.py}`, прежние — `*.prev-followups`; перезапуск `health-page-8088` и `health-soak-page`; обе страницы 200, `/api/v3` отдаёт `budget_bad`, `lost_ids_1h`, `backfill_lost_1h` (на пул-фиксовых сборках — 0). **Отклонение:** боевая `page.html` на srv — не файл репо (плотный стиль, вкладка «Арбитраж» правились прямо на srv, в git их нет), поэтому `page.html` репо не копировалась: правки доработок (строки подробностей `backfill`, `not_subscribed`, `budget`, `storm`, число потерянных номеров в подсказке столбика) перенесены в боевую страницу патчем по якорным строкам (файл — `/data/perf/followups-deploy/page-new/page.html`); проверка: `node --check` скрипта и прогон `detailEx` / `show` в `node` (vm-заглушка DOM) на данных `exchange_row` с `backfill` / `budget` / `storm` — строки выводятся, подсказка столбика показывает «потеряно номеров сделок». Клик-тест в браузере не делался (нет браузера на srv; София запрещена брифом). `v3.py` на srv был равен `v3.py` репо до доработок (diff — только ветка доработок), `serve.py` не менялся.

## Гейт

`bash tools/vps-check.sh . all` на srv (дерево `065ac7e2` + страница): fmt, clippy `-D warnings`, бюджет линтов — зелёные; `cargo test` **1354 из 1355**, один красный — `collector::tests::bingx_session_unpacks_gzip_answers_ping_and_does_not_write_heartbeats` (`books > 20`, `tests.rs:1750`: получено 19–20). Проверено A/B на том же хосте в те же минуты (тестовый бинарь каждого дерева, 25 прогонов вперемежку): основа `9c44e141` — 19×3, 20×9, 21×10, 22×2, 23×1 → **12 из 25 ≤ 20 (красный)**; доработки — 19×5, 20×7, 21×12, 22×1 → **12 из 25**. Тест считает кадры книги источника за ≈ 0,5 с (ждёт три `Pong` на `Ping` раз в 100 мс; кадры идут по ≈ 25 мс) — порог 20 лежит на самой середине разброса; при тихой машине (прошлые прогоны, проверяющий) выходит 21+. Не регрессия доработок. Тест не правился (код боевого бинаря — ровно `3835be7b`); правка порога — отдельное решение.

## База «до» (час 04:50–05:50 UTC, боевой пул-фикс 9c44e141, сверка с REST)

| Биржа | символ-минут | дыр книги (мягких) | дыр сделок | без единой записи | пересекли порог внутри окна |
|---|---|---|---|---|---|
| Bybit | 19 400 | 53 (52) | 56 | — | AUSDT (6 мин) |
| OKX | 17 698 | 36 (35) | 36 | — | ASTS-USDT-SWAP (20) |
| BingX | 50 198 | 117 (115) | 117 | — | NCCOXAUSGD2USD (50), NCCOHEATINGOIL2USD (9) |
| Gate | 12 088 | 50 (31) | 35 | — | MANTRA_USDT (20), ZHONGJI_USDT (11), TENCENT_USDT (3) |
| Binance | 30 016 | **0** (0) | 1 | 0 | — |

(`/data/perf/followups-deploy/before/<биржа>/`.)

**Наблюдение до выкладки: Bybit с 05:00 UTC — потеря паузами.** Старый процесс (`9c44e141`, запуск 01:23) с 05:00:00 писал по ≈ 50 с паузы в минуту (3 124 с за час 05, 468 пауз, `lost_1h_s` на странице 3 155 с, строка красная): пауза только у шардов `book05`, `trade05`, `mark05` — шарды, которые пул-фикс открыл живым пересмотром (`spawned_shards 3`, `pool_delta streams 2`), в них по несколько тонких символов (`trade05` 6 записей / 10 с, `book05` 185); правило паузы «молчание ≥ 3 с у шарда без своей истории» (порог для свежего шарда, тест `a_pause_inside_the_shards_usual_range_is_not_a_gap_but_the_same_pause_on_a_fresh_shard_is`) на тонких символах срабатывает постоянно. Прочтение — ложные паузы на тонких символах (**догадка**: независимо по символам этих шардов не проверено; REST-сверка Bybit за 04:50–05:50 показывает 53 дыры книги из 19 400 символ-минут — роста потерь книги, соразмерного 3 124 с, в ней нет). Новый процесс на старте раскладывает символы по нагрузке (3 разреженных шарда не создаются) — см. Bybit ниже.

## Журнал

### Bybit (`soak-bybit`)

- 06:37:22 UTC накат: `rollout.sh soak-bybit /data/turbo-probe/alpha-followups-3835be7b`, бинарь 34906d42 → 1a1955dd, прежний — `/data/collect/alpha-collect-bybit.prev-34906d42`; запуск 06:37:25 (`started_ms` 1794811045), версия `3835be7b`. Пул 386 символов, 15 шардов, 30 соединений (до: 404 подписано, 18 шардов с тремя живыми `05`). Таймер сверки `fu-after60-bybit` — 07:40 UTC.
- **Приёмка +12,5 мин (06:50:04), измерено:** `with_data_1m` 386 = пул 386 = `rest_eligible` 386; `not_subscribed` 0, `rest_checked` 386; `over_cap` [], `max_shard_load` 578 (book04; на старте по REST-оценке 4 568 book00 — после первого опроса `rates_tracked` 1 145 раскладка пересчитана), `spawned_shards` 0; `pool-state.json` есть (`last_above` 386, `rates` 3 вида); `storm` — 0 / 0 / 0, `connect_budget` `alarm` нет (использовано 0 из 200 после окна); `backfill.enabled` false (у Bybit номера нет); соединений 30 = 15 × 2, шардов с одной линией или без — 0; `sink_blocks_dropped` 0, `sink_errors` 0, ring full 0, `rx_clamped` 9; пауз `gaps_1h` 1 (trade01 06:43:39, 3 010 мс при пороге 3 000), `gaps_1h_nontrade` 0.
- Лаг прихода p50 / p99 (медиана окон 60 с): **109,6 / 121,9 мс** против 109,6 / 123,9 до; максимум p99 133,1 против 194,6; `queue_lag` p99 максимум 23,8 против 78,8; задержка шардов p99 максимум 170,0 против 1 720,3 мс.
- Строка страницы Bybit красная только из-за хвоста старого процесса (в ряду `series-minutes` 05:00–06:37 паузы, `lost_1h_s` 1 429 → 821 → падает с окном часа).
- Решение: Bybit принят; откат — `/data/perf/rollback.sh soak-bybit`.

### OKX (`collect-okx`)

- 06:50:29 UTC накат: бинарь 34906d42 → 1a1955dd, прежний — `/data/collect/alpha-collect-okx.prev-34906d42`; запуск 06:50:31 (`started_ms` 1794811831). Пул 323 символа, 12 шардов, 24 соединения. Таймер сверки `fu-after60-okx` — 07:53 UTC.
- Старт: `connect_budget` `waits_total` 22, `wait_s_total` 132 (лимит 2 подключения в секунду — ожидание ≈ 6 с на подключение), `alarm` **нет** (`max_wait_s` меньше 30 с), 24 соединения за ≈ 21 с, шардов с одной линией или без — 0.
