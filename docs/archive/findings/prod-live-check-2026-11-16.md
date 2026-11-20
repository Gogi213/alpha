# Боевая проверка сборки 29d981c0: очередь BingX и `lost_ms_late` вживую (TK-578, 16.11.2026)

Проверка по данным srv `5.231.25.224` на 18:28–18:37 UTC. Только чтение; коллекторы не перезапускались, данные не удалялись, сбои в бой не вносились.
Выкладка 29d981c0 (коммит выкладки 7ecceabb), метки `/data/perf/incident-deploy/deploy-ts-*`: Bybit 15:00:29Z, OKX 15:13:13Z, BingX 15:25:58Z, Gate 15:38:26Z, Binance 15:50:57Z.

## Итог

| Что | Результат |
|---|---|
| Очередь дозапроса BingX по сроку | **Живьём не наблюдалась — события не было.** С выкладки на BingX ни одного пропуска: все счётчики `backfill` = 0, файла `backfill-bingx-<запуск>.tsv.zst` у нового запуска нет |
| `lost_ms_late` поздней паузы | **Живьём не наблюдался — поздней паузы не было.** 0 поздних строк (`lost_ms_late` / `gaps_late` / `pause_ms_late` / `lost_ids_late`) в `series-minutes.jsonl` всех 5 бирж после выкладки |
| Вотчеры `fu-watch`, `inc-binwatch`, `nx-pausewatch` | Сняты в 18:37:21Z (три транзитных юнита, таймеров нет); выходные файлы на месте |

Фактов «работает» нет ни по очереди BingX, ни по `lost_ms_late`; фактов «не работает» тоже нет. Ошибок в сборе не найдено.

## 1. Очередь BingX (`bfqueue.py`)

`bfqueue.py` — не часть коллектора, а проверочный скрипт `/data/perf/incident-deploy/bfqueue.py`: читает `backfill-bingx-<запуск>.tsv.zst` и сверяет порядок запросов со сроком выхода пропуска из окна REST. Сама очередь — в коллекторе (`GapReq::deadline_ms`). Без файла `backfill-bingx-…` проверять нечего.

- Запуск BingX на 29d981c0: юнит `collect-bingx` с 15:25:59Z, `started_ms` 1794842772867 = 15:26:12.867Z, метка запуска файлов — 1794842766 (15:26:06Z).
- Живой `/data/collect/bingx/health.json` (`ts_ms` 1794853823070 = 18:30:23Z, `process.version` 29d981c0): `backfill` — `gaps_confirmed` 0, `ids_absent` 0, `ids_backfilled` 0, `ids_confirmed` 0, `ids_lost` 0, `ids_lost_1h` 0, `open_ids` 0, `paused_s_total` 0, `rest_requests` 0, `trades_written` 0, `last_error` null.
- Каталог `/data/collect/bingx/data/2026-11-16/`: единственный файл дозапроса — `backfill-bingx-1794829630.tsv.zst` (запуск 11:47:10Z, прежняя сборка 38d14b34; последняя запись 13:14:29Z, то есть до выкладки). Файла `backfill-bingx-1794842766.tsv.zst` нет; он создаётся при первом подтверждённом пропуске.
- Ряд минут BingX: 184 строки с 15:24Z, `gaps`/`lost_ids`/`lost_ms`/`pause_ms` везде 0.
- Журнал `journalctl -u collect-bingx` с 15:00Z: 14 строк, ни одной про backfill/pause/gap/lost/queue/deadline.

Что было с соединениями BingX: с выкладки 8 событий `timeout` (с `ping_degraded`) на 6 шардах — `book02` ×2, `mark00` ×2, `mark02`, `trade00`, `trade03`, `trade04` по одному; каждое закончилось `connected` и `path_switch`. Последние — `mark00` 18:20:55.895Z и `book02` 18:20:58.170Z (`path_switch` 18:21:37.068Z и 18:21:38.088Z). Пропусков id это не дало (счётчики выше — нули).

**Единственный живой ход пути «подтверждение → REST → итог» после выкладки — на Gate** (та же сборка, но без очереди по сроку: у Gate очередь по порядку подтверждения, подтверждение 10 с; к очереди BingX это не относится). `/data/collect/gate/data/2026-11-16/backfill-gate-1794843518.tsv.zst` (заголовок `rh` … `29d981c0`), health Gate: `gaps_confirmed` 4, `ids_absent` 4, `rest_requests` 4, `ids_lost` 0, `ids_lost_1h` 0. Строки `rg` (найден → итог в файле):

| Итог в файле (UTC) | Символ | id | Найден (UTC) | Найден → итог | Статус |
|---|---|---|---|---|---|
| 16:05:02.438 | UAI_USDT | 14386031 | 16:04:51.272 | 11,166 с | absent |
| 16:17:30.078 | BLESS_USDT | 16713100 | 16:17:18.961 | 11,117 с | absent |
| 17:59:24.563 | HUMA_USDT | 3906211 | 17:59:08.295 | 16,268 с | absent |
| 17:59:25.615 | HUMA_USDT | 3906214 | 17:59:08.431 | 17,184 с | absent |

`absent` — биржа этих id не отдаёт (REST пуст), потери нет (`ids_lost` 0).

Как получить живое доказательство без вмешательства: первый же пропуск BingX создаст `backfill-bingx-1794842766.tsv.zst`; тогда `python3 /data/perf/incident-deploy/bfqueue.py /data/collect/bingx/data/<дата>/backfill-bingx-1794842766.tsv.zst` (без пропуска — «no rg rows»). Срока события нет. Для масштаба: прежний запуск BingX (11:47–15:26Z, 38d14b34, с инцидентом 12:50Z) записал 883 пропуска (`rg`) и 41 965 сделок (`rt`) в `backfill-bingx-1794829630.tsv.zst`.

## 2. `lost_ms_late` (поздние паузы)

Поздняя строка пишется, только если пауза закончилась после записи строки её минуты начала, то есть пересекла границу минуты. Проверка рядов минут `series-minutes.jsonl` (строки с `t` ≥ метка выкладки − 120 с; ключи `lost_ids_late`, `lost_ms_late`, `gaps_late`, `pause_ms_late`):

| Биржа | Строк с выкладки | Поздних строк |
|---|---|---|
| Bybit | 210 | 0 |
| OKX | 197 | 0 |
| BingX | 184 | 0 |
| Gate | 172 | 0 |
| Binance | 159 | 0 |

Поздние строки за всё время есть только у BingX — 8 штук минуты 12:50:00Z (`t` 1794833400), сборка 38d14b34, **только** `lost_ids_late` (без `lost_ms_late`): 1294, 61, 657, 387, 454, 776, 225, 230 — сумма 4084 (= потеря 4084 сделок инцидента 12:50). До выкладки 29d981c0 это единственное живое подтверждение поздних строк (по id, не по паузам).

Единственная пауза шарда после выкладки — Binance `mark00`, внутри одной минуты (ветка «пауза до строки», поздняя строка не нужна):
- `events-mark00.jsonl`: `gap` 18:21:42.360Z (`gap_ms` 3444, порог 3000), `gap_end` 18:21:45.364Z: `gap_start_ms` 1794853298696 = 18:21:38.696Z, `gap_end_ms` 1794853305102 = 18:21:45.102Z, `gap_total_ms` 6406, `end_exact` true.
- Строка минуты 18:21:00Z (`t` 1794853260): `gaps` 1, `lost_ms` 6406, `pause_ms` 6406 — совпадает с `gap_total_ms`, двойного счёта нет. Строки 18:22–18:24Z — 0.
- Страница `:8088/api/v3`, строка `binance` в 18:37Z: `lost_1h_s` 6.4.

Это проверяет учёт паузы в строке минуты, но **не** ветку `lost_ms_late`. Пауза, пересёкшая границу минуты, после выкладки не случилась ни на одной бирже; поле `lost_ms_late` в живых данных пока не появлялось. Сбой в бой не вносился (приказ задачи).

## 3. Вотчеры

Все три — транзитные юниты `systemd-run` (`/run/systemd/transient/*.service`), постоянных файлов юнитов и таймеров нет, ссылок на них нет в `/etc/systemd/system`, `/etc/cron.d`, `/usr/local/bin`, `/data/perf/rollout.sh`, `rollback.sh`, `rollout-check.sh`, `/data/turbo-probe/page` (узкий grep; широкий по `/etc`, `/root`, `/opt` оборван — слишком тяжёл).

| Юнит | Что делал | Чем покрыто в бою | Решение |
|---|---|---|---|
| `fu-watch.service` (с 05:53:41Z) | Каждые 30 с снимок health всех 5 бирж + строка страницы 8088 → `/data/perf/followups-deploy/watch-<биржа>.jsonl` (до/после выкладки) | `health.json` (живой), `series-minutes.jsonl` раз в минуту (gaps/late/lost/pause), `stats-*.jsonl` по шарду каждые 10,00 с (лаг p50/p99, queue_lag, dups, ring), `events-*.jsonl`, `pool-*.json`, `backfill-*.tsv.zst`; страница 8088 | Снят: выкладки всех 5 бирж завершены, приёмка записана (`final-*.json`, `rollout-*.log`), откатов нет; «до/после» делает сама выкладка (`pre/`, `final-*.json`). Отдельной 30-секундной истории `symbols`/`ws`-счётчиков в бою нет — она нужна только вокруг выкладки |
| `nx-pausewatch.service` (с 12:28:23Z) | Копировал из `events-*.jsonl` Bybit строки `pause_check` / `gap_end` / `gap` в `/data/perf/next-deploy/pause_check-bybit.jsonl` | Те же события уже лежат в `events-*.jsonl`; вердикты — `pause_check` в health Bybit; потеря — ряд минут и страница. Фильтр рабочий (у Binance строки `gap`/`gap_end` в `events-mark00.jsonl` того же вида) | Снят. За 6 ч 09 мин вывода нет: `pause_check-bybit.jsonl` не создавался; свой пересчёт `events-*.jsonl` Bybit с 11:21:14Z — 0 подходящих строк. Health Bybit: `pause_check` loss 0, loss_1h 0, quiet_1h 0, unverified_1h 0, paused_s_total 0; ряд минут — 210 строк, `pause_ms` 0 |
| `inc-binwatch.service` (с 15:50:54Z, `binwatch-auto.sh 1794844254`) | Следил за `ws_end` `fin_without_close_frame` Binance; повторный молодой (< 60 с) сброс `book03`/`book05` за 10 мин → `TRIGGER` → `rollback.sh collect-binance` | Сами сбросы в бою видны: `ws_end` в `events-*.jsonl` Binance (3 штуки с выкладки — все в журнале сторожа), `ws.reconnects_1h`/`timeouts_1h`, `gaps`/`pause_ms` в ряду минут, красные флаги страницы | Снят. Журнал `binwatch.log` за 2 ч 46 мин: 3 строки, все `old` (18:22:11Z `mark00` возраст 9066 с; 18:22:14Z `book09` 9069 с; 18:30:29Z `book01` 325 с), молодых сбросов 0, `TRIGGER` 0, откат не срабатывал. Автооткат — инструмент окна выкладки (она принята); остаться он мог бы только как мина: `TRIGGER` вернул бы бой Binance на бинарь до 29d981c0 с прежними аргументами (`rollback.sh` читает `/data/perf/rollout-collect-binance.env`) без решения человека |

Снято: `systemctl stop fu-watch.service inc-binwatch.service nx-pausewatch.service` в 2026-11-16T18:37:21Z, код 0. После остановки: все три `inactive` / `not-found`, файлов юнитов в `/run/systemd/transient/` нет, процессов `watch.py` / `binwatch` / `pausewatch.py` нет. Коллекторы не тронуты: `soak-bybit` (MainPID 3322467, с 15:00:31Z), `collect-okx` (3323526, 15:13:15Z), `collect-bingx` (3324537, 15:25:59Z), `collect-gate` (3325446, 15:38:28Z), `collect-binance` (3326459, 15:50:58Z) — те же PID, `active`.

Выходные файлы не удалялись: `watch-{binance,bingx,bybit,gate,okx}.jsonl` в `/data/perf/followups-deploy/` (≈3,0 МБ, 1527 строк на биржу, 05:53–18:37Z), `/data/perf/incident-deploy/binwatch.log` (254 Б), `/data/perf/next-deploy/binwatch.log`.

Перезаряд сторожа Binance при необходимости: `systemd-run --unit=inc-binwatch --nice=10 /data/perf/incident-deploy/binwatch-auto.sh <epoch начала окна>`.

Остальные «сторожа» на srv не трогались (вне задачи): `alpha-netwatch.service` (постоянный), `lanewatch.service`, `stallwatch.service` (транзитные).

## 4. Прочее, замеченное по пути (не проверялось)

- 18:21:44.868–18:22:00.983Z у Binance массовые `timeout` на шардах книг (в окне 18:20:00–18:23:40Z — 142 события типов gap/timeout/connected/path_switch), у BingX — 2 шарда (`mark00`, `book02`) 18:20:55–18:20:58Z. Потеря записана одна (Binance `mark00`, 6,4 с); причина не разбиралась.
- Страница `:8088/api/v3` в 18:37Z: `ok` False при `late_bad` True у `binance` и `gate`; у остальных трёх `ok` True. `live_bad` и `budget_bad` False у всех.
- Упавшие юниты без дела: `collect-gap-gateraw.service`, `poolfix-after60-bingx.service`, `poolfix-after60-okx.service` (`failed`); не трогались.
