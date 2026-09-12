# alpha — бот по плотностям стакана Bybit

<!-- autopilot:start -->

## Что это

Rust-проект: записать стакан Bybit олвейс-он коллектором (В-34: сутки — часть,
весь пул разом; сессии 5–15 мин остались режимом отладки), разметить крупные уровни, посчитать профили (семь осей), вынести вердикт
бэктестом с моделью очереди. Архитектура — под живого бота с первого дня: одна
стратегия `lob::strategy::on_event<MD: MarketDepth, B: Bot<MD>>` идёт и в
`Backtest`, и в `LiveBot` крейта `hftbacktest` без правок (шов доказан тестом на
настоящем `hftbacktest::backtest::Backtest`).

## Где план и состояние

- `docs/plan/PLAN.md` — план; в начале раздел «Точка остановки» с тем, как продолжать
- `docs/plan/BUSINESS-TASK.md` — задача владельца, редакция 3; **не редактировать**,
  дополнения — в `.autopilot/…/2026-09-11-brief.md`, раздел «Дополнения»
- `docs/plan/REVIEW-2026-09-11.md` — что в коде остаётся / чинится / сносится (РВ-0)
- `docs/plan/RECON-2026-09-11.md` — разведка: три инструмента пула × 5 мин
- `docs/plan/SETTLED.md` — журнал решений (В-1…В-34; В-30 — `k` выбирает пилот, В-31 — окно «сейчас», В-32 — диск под сессии, В-33 — хвост пилота, В-34 — олвейс-он коллектор)
- `docs/findings/` — **боевые артефакты**: `recording-2026-09-11.md` (запись доказана на 3×5 мин), `pilot-2026-09-11.md` (первый пилот 30 мин — красный про рынок), `collector-2026-09-12.md` (коллектор до/после и олвейс-он 5 мин: байт/запись 7.27, CPU 1.7 %, zstd 1); `docs/plan/runs.csv` — журнал испытаний (8 строк пилота)
- `docs/ARCHITECTURE.md` — A1–A9, обязательные к соблюдению (раскладка дерева там —
  черновик, устарела; актуальное дерево модулей — ниже)
- `.autopilot/state.js` — состояние прогона (таски, волны, гейты); дашборд —
  `python .autopilot/sync.py`, затем `http://localhost:<порт из .autopilot/serve.pid>/dashboard.html`
- `.autopilot/2026-09-11-lob-density-ed3/` — манифест, спека, границы
  (`interfaces.md` — карта того, что построено таском за таском; читать исполнителю
  первым), эталоны, таски

## Команды

```bash
cargo build --release
cargo test --release 2>&1 | tail -30        # 647 passed, 0 failed, 5 ignored (30 тасков + пилот; +2 ignored бенча collector_bench)
cargo test --release --test collector_bench -- --ignored --nocapture   # бенч разбора: медиана/p99, аллокаций на сообщение
cargo clippy --all-targets -- -D warnings   # бюджет линта ноль
cargo fmt --check
./target/release/alpha.exe lob --help       # 17 подкоманд, см. таблицу артефактов ниже
# дашборд «Монеты и плотности» (T33, R88): страница в data/dashboard/, сервер печатает порт и адрес
./target/release/alpha.exe lob dashboard --root data/always-on/<ts> --out data/dashboard [--watch 120] [--h3-k 10]
python tools/serve_dashboard.py data/dashboard        # → http://127.0.0.1:<порт>/index.html; открыть в браузере
# вотчер живёт с копии бинарника (data/dashboard/alpha-dashboard.exe), чтобы не держать target*/release/alpha.exe

# олвейс-он коллектор (В-34): запуск — до Ctrl+C; instruments.csv скопировать в --root для levels/pilot
mkdir data/always-on/<ts> && cp instruments.csv data/always-on/<ts>/
./target/release/alpha.exe lob session --pool-instruments instruments.csv --root data/always-on/<ts> --always-on
# остановка — Ctrl+C один раз в его терминале: сброс писателей, clock.csv, session.json closed=true, код 0
# (второе нажатие — аварийный выход 130; `timeout`/kill — не Ctrl+C: финального session.json не будет)
```

Релизная сборка с нуля ~4 мин; инкрементально — секунды. Тесты офлайн (сеть только
за `#[ignore]`-тестами на живом REST/WS — их не гоняет `cargo test`). Живые прогоны
бьют в public WS Bybit без ключей. До гейта G-DEBUG (пройден 2026-09-11) любой
тестовый прогон был ограничен 5 минутами — фаза отладки, результат не данные;
после G-DEBUG часовые окна и двухчасовой пилот разрешены планом. `data/` gitignored
— ни один артефакт под ним не коммитится, включая `docs/plan/candidates.csv`,
которое коммитится отдельно как заморозка пула.

## Структура

```
src/
  main.rs, lib.rs        точка входа, реэкспорт crate root
  alloc_count.rs          счётчик аллокаций на глобальном аллокаторе — гейт GC
  binlog/                 формат лога: Reader/Writer по кадрам, дельты varint, zstd
  book/                   реконструкция книги, реализует MarketDepth крейта, целые тики
  bybit/                  адаптер площадки — единственное место, знающее про Bybit
    conn.rs                 сокет за Transport, ресинк по u, local_ts на recv
    ws.rs                   разбор сообщений, флаг BT
    rest.rs                 REST вне событийного пути (A9), таймаут 10 с
    verify.rs               сверка книги с REST по u, три теста, вердикт «ни разу не держала»
    verify_sidecar.rs        сверка отдельным ОС-потоком, не блокирует рантайм
    trade_ws.rs              WS trade, RTT двумя метками, подпись и отправка ордера
    sign.rs                  HMAC-SHA256, ключи только из окружения
    clock.rs                 дисциплина часов против NTP/serverTime, clock.csv
    probe.rs                 замер RTT полного цикла — реальные ордера
  feed/                   единый источник событий для стратегии и для записи
    mod.rs                   trait Feed { fn next_event(&mut self) -> Option<Event> }
    replay.rs                Feed поверх бинлога (реплей)
    live.rs                  Feed поверх N сокетов bybit::conn, один ОС-поток
  lob/                    чистая логика без ввода-вывода
    levels.rs                жизнь уровня, шесть признаков, пол/перцентиль H3
    markout.rs               движение цены на HORIZONS_MS = [100,1000,10000,60000] мс
    costs.rs                 net, net_fill; ROUNDTRIP_FEES_BPS=7.5, GREEN_NET_BPS=3.0
    cells.rs                 disjoint_contrast — единственный совместный бутстрап-интервал
    shortlist.rs              сетка семи осей, заморозка, вердикт, DSR в вердикте
    final_metrics.rs         DSR/PBO/CPCV, SR0, требуемый Шарп — чистые функции
    watch.rs                  счётчик выборки n/G по профилю, годность суток
    strategy.rs                on_event<MD, B: Bot<MD>> — единственная стратегия
    runs.rs                    журнал испытаний (append_run_row)
    export.rs                   экспорт npy
    markup.rs                   классификация (устаревшая часть, годная)
  stats/                  bootstrap-t на весах Уэбба, разрешение сетки — крейт-агностично
  commands/
    record.rs                Recorder, run_record; подмодули record/{errors,gaps,paths,steps}.rs
    lob/                    по файлу на подкоманду CLI; тесты каждого модуля — в <модуль>/tests.rs
      mod.rs                  дерево модулей, LobCommand, dispatch, test_support
      h3.rs, replay.rs, parts.rs, names.rs   порог H3 и CLI-аргументы; реплей бинлога → уровни/середина; части и сутки сессии; имена сторон/исходов
      pick/                   build_pool, coverage, depth, order_size_22a, CSV-таблицы
      session.rs              SessionCtx, run_session; session/{args,pool,sink,summary,resources}.rs
      pilot.rs                run_pilot; pilot/{k_grid,metrics,gates,inputs,chain}.rs
      profiles.rs             run_profiles; profiles/{axes,accumulate,coverage,sessions,table}.rs
      dashboard.rs            данные страницы; dashboard_page.html — сама страница
      react.rs                горячий путь: разбор → триггер → ордер, гейт G-LAT
      power.rs, backtest.rs, shortlist.rs, watch.rs, …
```

Тесты вынесены из исходников: `#[cfg(test)] mod tests;` → `<модуль>/tests.rs` (для `mod.rs` —
`<каталог>/tests.rs`). Правка логики читает только исходник; `include_str!` в тестах — с `../`.

## Какие команды дают какой артефакт

| Команда | Артефакт |
|---|---|
| `lob pick --window-secs --h3-k` | `instruments.csv` в корне (**только пул** — ровно десять, ранг по обороту) + `docs/plan/candidates.csv` (все кандидаты, причина по каждому). Оба несут колонку `depth_check` (`ok`/`below_floor`/`not_measured`): глубина — проверка, не критерий отбора (В-35) |
| `lob session --pool-instruments --root --minutes` (5..15) **или** `--pilot-minutes` (16..360, режим пилота §11; `session.json.pilot`) **или** `--always-on` (В-34: без дедлайна, до Ctrl+C; новые сутки UTC — новая часть с синтетическим снапшотом; `session.json` на старте / раз в час / на ротации / на остановке (`closed`), `samples` RSS/CPU, `reconnects`/`resyncs`/`frames_failed`/`bytes_written`); `debug = duration_s < 3600`; несколько сессий в сутки — части `-p2`, `-p3`… (`session.json.binlog_files`) | каталог сессии: `<SYMBOL>-<день UTC>.binlog` на инструмент, `gaps.csv`, `clock.csv`, `session.json` — это имя читают все остальные команды (`commands::lob::session_binlog_for`) |
| `lob record --symbol --root` | запись **одного** инструмента (legacy-путь, не пул) |
| `lob verify --symbol --root` | `<root>/verify-<SYMBOL>.status` — ровно `ok`/`fail`, маркер сверки сессии, который читают `profiles`/`watch` (без `ok` сутки не читаются, fail-closed); сверка по всем частям символа (`session_binlog_for`), строка stdout на часть с сутками; маркер пишет одна функция `commands::lob::verify::verify_and_mark` — ею же `lob pilot`. `verify.csv` — сайдкар записи (`bybit::verify_sidecar`), не эта команда |
| `lob levels --h3-mode floor\|percentile [--h3-k <f64>]` (`--h3-k`: пол = `floor(k × median_trade_lots)` из `instruments.csv` на лету; только с `floor`) | `levels-<SYMBOL>.csv` — шесть признаков жизни уровня |
| `lob markout` | `markout-<SYMBOL>.csv` — движение на четырёх горизонтах |
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

## Правила, которые ловят ревью

Семь запретов горячего пути (ноль аллокаций на событие после прогрева,
`Instant::now()`/`SystemTime` только через трейт `Clock`, `async` с захватом
состояния в потоке решений, REST в событийном цикле, сборка/подпись ордера после
триггера, `f64` для цены/размера, `BTreeMap`/`HashMap` на пути события) —
полный текст и обоснование в `.autopilot/2026-09-11-lob-density-ed3/interfaces.md`.
Изобретённое число запрещено — измеримое измеряется, назначаемое — коммитом.
Расстояние только в bps, никогда в тиках. Шаг не закрыт, пока не существует
названный артефакт на диске. Секреты — только имена переменных (`BYBIT_API_KEY`,
`BYBIT_API_SECRET`), значение никогда не печатается и не пишется в артефакт.

## Экономия контекста (В-39)

Один ревьюер по трём осям для тасков без вердикта/контракта/предрегистрации/горячего
пути; три — только для них. Закоммитил тикет → память и передача → клир. Правки —
точечным `Edit` (не скриптами со старым+новым текстом), файлы читать секциями, вывод
команд через `tail`/`grep`, страницу проверять `read_page`/`get_page_text`, скриншот —
когда текст не отвечает. Длинный текст/HTML — отдельным файлом через `include_str!`
(`src/commands/lob/dashboard_page.html`), не строкой в Rust. Усилие: `high` по умолчанию,
выше — только на дизайн и ревью вердикта. Коннекторы Figma/Elicit/Mobbin выключены.
**Исполнители — по одному:** на этой машине живёт коллектор; параллельные `cargo build` в
нескольких worktree грузят CPU на 100 % и RAM до OOM (2026-09-12: пять сборок разом — владелец
остановил). Один агент → одна сборка → слить → следующий; инкрементально в `target-ci`.

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
