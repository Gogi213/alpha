# Команды, артефакты, грабли — подробно

Вынесено из `CLAUDE.md` 2026-09-12 (В-39, экономия контекста): здесь полный текст, в `CLAUDE.md` — сжатая версия и указатель сюда. Правит тот же таск, что меняет команду.

## Какие команды дают какой артефакт

| Команда | Артефакт |
|---|---|
| `lob pick --window-secs --h3-k` | `instruments.csv` в корне (**только пул** — ровно десять, ранг по обороту) + `docs/plan/candidates.csv` (все кандидаты, причина по каждому). Оба несут колонку `depth_check` (`ok`/`below_floor`/`not_measured`): глубина — проверка, не критерий отбора (В-35) |
| `lob session --pool-instruments --root --minutes` (5..15) **или** `--pilot-minutes` (16..360, режим пилота §11; `session.json.pilot`) **или** `--always-on` (В-34: без дедлайна, до Ctrl+C; новые сутки UTC — новая часть с синтетическим снапшотом; `session.json` на старте / раз в час / на ротации / на остановке (`closed`), `samples` RSS/CPU, `reconnects`/`resyncs`/`frames_failed`/`bytes_written`); `debug = duration_s < 3600`; несколько сессий в сутки — части `-p2`, `-p3`… (`session.json.binlog_files`); **пул на ходу** (T34, R89): дописать строку в `<root>/instruments.csv` (формат `lob pick`) — на ближайшем тике (≤ `FRAME_LOSS_WINDOW_SECS` = 10 с) символ получает свой `<SYMBOL>-<день>.binlog` (часть — следующая свободная), своё соединение (`feed::DynamicPool::add`, живые сокеты не трогаются), строку stderr `session: добавлен …` и место в `session.json.instruments`/`binlog_files`; повтор строки — не дубликат; битая строка/файл — только stderr, повтор на следующем изменении `mtime`; **удаление строки не поддерживается** — запись символа продолжается | каталог сессии: `<SYMBOL>-<день UTC>.binlog` на инструмент, `gaps.csv`, `clock.csv`, `session.json` — это имя читают все остальные команды (`commands::lob::session_binlog_for`) |
| `lob record --symbol --root` | запись **одного** инструмента (legacy-путь, не пул) |
| `lob verify --symbol --root` | `<root>/verify-<SYMBOL>.status` — ровно `ok`/`fail`, маркер сверки сессии, который читают `profiles`/`watch` (без `ok` сутки не читаются, fail-closed); сверка по всем частям символа (`session_binlog_for`), строка stdout на часть с сутками; маркер пишет одна функция `commands::lob::verify::verify_and_mark` — ею же `lob pilot`. `verify.csv` — сайдкар записи (`bybit::verify_sidecar`), не эта команда |
| `lob levels --h3-mode floor\|percentile [--h3-k <f64>]` (`--h3-k`: пол = `floor(k × median_trade_lots)` из `instruments.csv` на лету; только с `floor`) | `levels-<SYMBOL>.csv` — шесть признаков жизни уровня |
| `lob markout` | `markout-<SYMBOL>.csv` — движение на четырёх горизонтах |
| `lob touches --root --symbol --h3-mode floor\|percentile [--h3-lots] [--h3-k] [--warmup-ms] [--repeat-window-ms] [--out]` | `touches-<SYMBOL>.csv` (T35, В-42) — касания живых уровней (`lob::levels::TouchRecord`): касание начинается, когда живой уровень (> `H3`), **живший до кадра**, стал лучшей ценой своей стороны (рождение лучшей ценой — не касание, В-43), кончается, когда перестал ею быть или умер (`ended_by_death`, `end_ms` = смерть). Колонки: `day_utc, side, price_tick, touch_index, start_ms, end_ms, duration_ms, birth_ms` (рождение уровня, как в `levels-*.csv`), `age_ms, size_at_touch, size_max_before, traded_during, frontrun_lots` (лоты той же стороны лучше уровня на последнем кадре до касания), `round_zeros` (0/1/2/3+), `ended_by_death, stack_levels` (живых ≥ `H3` той же стороны), `dist_bps` (при рождении, как у `profiles`), `m_100ms, m_1s, m_10s, m_60s` (база — срез **как есть на `start_ms`**, В-43; знак «в сторону отскока» — минус знака смерти), `approach_1s, approach_10s` (сдвиг середины от `start_ms − N` до базы, плюс — к уровню); пустая ячейка — нет среза. Тот же реплей, что `levels`/`markout`; смерти не менялись; дашборд/профили/шортлист касаний не читают (после предрегистрации) |
| `lob watch` | `progress-<symbol>-<profile>.csv`, `ready-<symbol>-<profile>.flag` |
| `lob power` | stdout: `n= sr0= required_sharpe= dsr_target= num_obs=` — гейт G-POWER-A, до сбора данных |
| `lob profiles --candidates-csv --h3-mode` | `docs/findings/profiles-<дата>.csv` — таблица профилей сетки семи осей |
| `lob backtest --median-rtt-ns --p95-rtt-ns --order-qty-e9` | `docs/findings/backtest-<дата>.csv` + `-pnl.csv` (кривые median/p95) |
| `lob shortlist --preregistration --freeze-out --freeze-commit` | `docs/findings/shortlist-<дата>.md` + `shortlist-frozen.txt` (заморозка), предрегистрация границы 60/40 |
| `lob pilot --debug` | цепочка `session→verify→levels→markout→profiles→backtest`, объявляет G-DEBUG |
| `lob pilot` (боевой) на каталоге `lob session --pilot-minutes` | ставки уровней, `k`-сетка и выбор (В-30), G0, G-POWER-B против `lob power`, строки в `docs/plan/runs.csv`; окно — из `session.json`, зачётный хвост — вторая половина (В-33); `instruments.csv` надо скопировать в `--root` руками |
| `lob react` | стадийные латентности (разбор/книга/триггер/ордер/весь путь), объявляет G-LAT (нужно ≥1000 срабатываний) |
| `lob probe` | **ставит настоящие post-only ордера на бирже** — RTT полного цикла |
| `lob dashboard --root --out [--h3-mode floor] [--h3-lots] [--h3-k] [--watch N]` | `<out>/index.html` + `<out>/data.json` (T33, R88 — «дашборд о монетах и их плотностях», заменил T32) — только чтение `--root`, запись через `.tmp` + rename. По каждой монете пула: **плотности сейчас** (живые уровни на последнем кадре: сторона, цена, расстояние bps, размер в монетах/$/×H3, возраст, который раз на цене — 40 самых крупных), **картина за последний час** (SVG: середина цены, полоска на уровень от рождения до смерти, цвет — исход, толщина — ранг размера; фильтры исход/крупнейшие N %/жизнь от; потолок 4000 полосок), **что стало** (по исходам: n, доля, жизнь, `m` на 4 горизонтах, `net` 10 с), **где стоят и как живут** (маргиналы осей сторона/размер/расстояние/жизнь/повтор — корзины `shortlist::*_LABELS`). Разметка — `replay_symbol` + `markouts_for_level` + `costs::observation_at`, живые уровни — `LevelTracker::live_levels` (`ReplayStats.open`). Одна строка про коллектор (жив по росту бинлогов между расчётами, `closed`, записано, разрывы из живого `gaps.csv`); порог `H3` печатается с источником (`instruments.csv` каталога, метка `debug`, `k`); суток < `G_MIN` — оговорка текстом. Гейтов/аудита/«где мы» на странице нет (PLAN.md) |

`docs/findings/` содержит только вердикты записи и пилота; `profiles-*`/`backtest-*`/
`shortlist-*` боевых ещё нет — суточный олвейс-он ещё не запускался (владелец, после
вердикта экономии в `collector-2026-09-12.md`). Пул в `instruments.csv` — **боевой**
(`lob pick --window-secs 3600 --h3-k 1.0`, окно 2026-09-12 13:22 UTC, без метки
`debug`): SOL, ZEC, XRP, HYPE, NEAR, STORJ, DOGE, ENA, LSK, SUI — десять, ранг по
обороту; `depth_check` = `below_floor` у ZEC/STORJ/LSK (в пуле, В-35), `ok` у семи.
`k = 1.0` — по-прежнему заглушка (В-30: `k` выбирает пилот; `median_trade_lots` в
файле, пересчёт пола любым `k` — на лету через `lob levels --h3-k`). Идущий
олвейс-он `data/always-on/20260912T122440Z/` пишет **старый отладочный** пул из
восьми (ADA, PUMPFUN вместо ZEC, STORJ, LSK, SUI) — расхождение с боевым пулом,
решение владельца. Боевые данные: `data/pilot-battle/20260911T224019Z/` (30 мин,
gitignored).

## Грабли

- `--h3-mode floor|percentile` обязателен, умолчания нет — у `levels`, `markout`,
  `watch`, `profiles`, `shortlist` (проверено `--help` каждой); `pilot` этот флаг
  не берёт — он гоняет `floor` и `percentile` сам, оба режима за один прогон.
  `floor` берёт `h3_lots` из `instruments.csv`, `percentile` требует часа прогрева
  (`--warmup-ms`, умолчание 3 600 000 — на 5-минутной записи даёт `levels=0`,
  это прогрев, не баг; для отладки `--warmup-ms 0`)
- `instruments.csv` в корне записи = **уже отобранный пул** — ровно десять, если
  правилам §2 (BTC/ETH, некрипта, < 30 суток) прошли хотя бы десять кандидатов, —
  не список всех кандидатов; пишет `lob pick`, читают `lob session`/`levels`/
  `record`/`power`. Глубина ниже `DEPTH_FLOOR_USD_E9` ($2000/уровень) **никого не
  отсеивает** (В-35, §9): она колонка `depth_check` в обоих CSV и строка stdout;
  `excluded_reason` в `candidates.csv` несёт только три кода правил и срез ранга
  `rank_beyond_pool`
- `k` для пола `H3` — **не число, а процедура** (`SETTLED.md` В-30 / D05): `lob pilot`
  считает ставку уровней и долю `eaten` для сетки `K_GRID = [2, 5, 10, 20, 50]` (один
  реплей на инструмент) и берёт наименьшее `k`, при котором медианный инструмент
  проходит G0 (≥ 200 уровней/зачтённый час) и G1 (`eaten` ≥ 5 %). **Пилот 30 мин
  2026-09-11: `k` не определим** — `eaten` на медианном < 5 % при всех `k`; G0 edge RED
  (`net` −9 bps), G-POWER-B RED (Шарп 2.26 vs 3.06). Дальше — решение владельца:
  принять красный / 2-часовой пилот (даст `percentile`) / пересмотреть правило 70/20
- `lob probe` ставит реальные post-only ордера на бирже — не гонять без надобности
- `conn.rs` бэкофф переподключения — за приватным трейтом `Backoff`; тест переподключения
  детерминирован (`RecordingBackoff`), реального времени не ждёт
- `lob profiles`/`lob backtest`/`lob shortlist` требуют RTT (`--median-rtt-ns`/
  `--p95-rtt-ns`) и лот (`--order-qty-e9`) обязательными флагами без умолчания —
  источник: `lob probe`/`clock.csv`, не изобретать парсер по умолчанию. **До замера
  владелец назначил 20 мс (В-37):** `--median-rtt-ns 20000000 --p95-rtt-ns 20000000`,
  шапка артефакта обязана печатать `rtt=assumed(20ms, В-37)`
- `ready-<symbol>-<profile>.flag` — формат `key=value`, ключ `n` (был `n_c2` до сноса C1/C2
  таском 17); единственный читатель — `lob markout --confirmatory`
- общие CLI-структуры: `commands::lob::H3Args` (`--h3-mode`, `--h3-lots`) и `ExecutionArgs`
  (`--median-rtt-ns`, `--p95-rtt-ns`, `--order-qty-e9`) через `#[command(flatten)]`;
  `lob backtest` держит свои обязательные флаги отдельно
- бинлог сессии ищется одним резолвером `commands::lob::session_binlog_for(dir, symbol)`:
  `<SYMBOL>-<дата>.binlog`; старый недатированный `<SYMBOL>.binlog` (записи до таска 19 в
  `data/session-debug`, `data/pilot-debug/*/session`) отвергается с подсказкой переименовать
  (`profiles`/`watch` при сканировании многих сессий такой каталог молча пропускают)
- сутки (`day_utc`) и час старта в `profiles`/`watch`/`shortlist` — у **каждой части**
  (таск 23, `commands::lob::session_parts_for`): сутки из имени файла, час из
  `session.json.binlog_files`; каталог с частями за D и D+1 — два кластера суток, окно
  «сейчас» фильтрует по суткам части
- GC на пилоте 30 мин: NTP offset 79 мс (порог 5), parse p99 400 мкс (порог 200), RSS
  4.9→18.4 МиБ не плоский — техдолг до сбора; CPU 3 %, gaps 0 — ок
- коллектор (T24): `ws::parse_message_into` без `serde_json::Value`, `lob session` копит кадры
  до `FRAME_TARGET_RECORDS`; p99 разбора/очереди — из гистограммы (`LatencyHistogram`,
  ≤ 1.5625 %), не из `Vec`; замер до/после — `docs/findings/collector-2026-09-12.md`
- пул на ходу (T34): `lob session` следит за `<root>/instruments.csv` — один `metadata()` на
  тик, файл перечитывается только при смене `mtime` (`load_pool` целиком: одна битая строка —
  весь файл отклонён, stderr, состав прежний); новые символы — файл части текущих суток UTC
  и новое соединение (`LiveFeed::add` через сохранённую фабрику; раскладка та же
  `plan_connections`/`MAX_ARGS_CHARS`); индексы обязаны продолжить `states` (assert);
  удаление символов не реализовано; `cp instruments.csv <root>/` для `levels`/`pilot`
  безвреден — все символы уже пишутся
- олвейс-он (T25): кадр уходит на диск одним `write_all` (`FrameSink`) и не реже
  `record::FRAME_LOSS_WINDOW_SECS = 10` с по тику рантайма (`feed::Event::Tick`) — файл
  всегда на границе кадра, `verify`/`levels`/`markout` читают живой каталог, пока коллектор
  пишет (у формата нет трейлера), не видят только последние ≤ 10 с; `session.json` между
  часовыми записями — стартовый (`records_total = 0`), не «нет данных»; `samples` — раз в
  30 с первый час, потом раз в час (`resource_sample_period`); `record::ZSTD_LEVEL = 1` по
  замеру, не менять без бенча `collector_bench` (`--ignored`); Ctrl+C доходит до процесса
  только из настоящей консоли — потомки агентских/сервисных оболочек наследуют «Ctrl+C
  игнорировать» (`SetConsoleCtrlHandler(NULL, TRUE)`), тогда остановить его штатно нельзя
- `lob dashboard` перечитывает **все** бинлоги каталога при каждом расчёте (8 инструментов ×
  5.5 ч — 15 с после перевода `markout::base_before`/`future_asof` на двоичный поиск в T33; до
  этого 25 с на инструмент на 2 ч): `--watch N` спит N с **после** расчёта; страница сама
  перечитывает `data.json` раз в 30 с. `--h3-mode` здесь единственный с умолчанием (`floor`):
  с `percentile` страница на 30-й минуте была бы пустой. Порог — из `instruments.csv` **каталога
  записи**: у идущего олвейс-она он отладочный (`h3_lots = 1`, окно 300 с) — «крупный уровень»
  там любая заявка в топ-50, поэтому живых уровней ровно 100, а полосок в час десятки тысяч;
  страница это печатает (`h3_source`), сортирует живые по размеру и рисует крупнейшие 20 %;
  строже — `--h3-k 10` (K_GRID В-30) или перезапуск коллектора на боевой десятке. «Жив/нет» —
  по росту бинлогов между двумя расчётами: первый честно печатает «не знаю». `data.json` при
  отладочном пороге ~6 МБ (полоски). Сервер `tools/serve_dashboard.py` слушает только
  127.0.0.1, порт свободный (печатается) или `--port`; страница открывается и с `file://`
  (данные встроены в `index.html`, `fetch` тогда молча не работает)
- `sync.py` печатает по-русски в кодировке консоли — mojibake в выводе нормален
- Bybit отдаёт `403` с части стран (CloudFront); с этой машины доступ есть

<!-- autopilot:end -->
- докинуть монету в идущую запись (T34): строки в `<root>/instruments.csv`; **несколько монет —
  одной записью файла**: каждая партия (одно изменение файла) — своё соединение и ОС-поток,
  четыре строки по одной за четыре тика = четыре сокета вместо одного. Подхват — по смене
  `mtime`: запись с сохранением метки (`cp -p`) не увидится; одна битая строка отклоняет весь
  файл (stderr), правьте и сохраняйте снова. Ёмкость канала событий считается на старте по
  числу символов и после добавления не растёт
