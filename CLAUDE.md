# alpha — бот по плотностям стакана Bybit

<!-- autopilot:start -->

## Что это

Rust-проект: записать стакан Bybit сессиями (5–15 мин, весь пул из 10 инструментов
разом), разметить крупные уровни, посчитать профили (семь осей), вынести вердикт
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
- `docs/plan/SETTLED.md` — журнал решений (В-1…В-33; В-30 — `k` выбирает пилот, В-31 — окно «сейчас», В-32 — диск под сессии, В-33 — хвост пилота)
- `docs/findings/` — **боевые артефакты**: `recording-2026-09-11.md` (запись доказана на 3×5 мин), `pilot-2026-09-11.md` (первый пилот 30 мин — красный про рынок); `docs/plan/runs.csv` — журнал испытаний (8 строк пилота)
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
cargo test --release 2>&1 | tail -30        # 615 passed, 0 failed, 5 ignored (24 таска + пилот)
cargo test --release --test collector_bench -- --ignored --nocapture   # бенч разбора: медиана/p99, аллокаций на сообщение
cargo clippy --all-targets -- -D warnings   # бюджет линта ноль
cargo fmt --check
./target/release/alpha.exe lob --help       # 16 подкоманд, см. таблицу артефактов ниже
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
    record.rs                реализация одно-символьной записи (lob record)
    lob/                    по файлу на подкоманду CLI
      mod.rs                  LobCommand, dispatch, общие replay_symbol/day_tallies
      pick/                   build_pool, coverage, depth, order_size_22a, CSV-таблицы
      session.rs              запись всего пула разом поверх feed::live::LiveFeed
      react.rs                горячий путь: разбор → триггер → ордер, гейт G-LAT
      power.rs, profiles.rs, backtest.rs, shortlist.rs, pilot.rs, watch.rs, …
```

## Какие команды дают какой артефакт

| Команда | Артефакт |
|---|---|
| `lob pick --window-secs --h3-k` | `instruments.csv` в корне (**только пул**, отобранный ранг) + `docs/plan/candidates.csv` (все кандидаты, причина исключения по каждому) |
| `lob session --pool-instruments --root --minutes` (5..15) **или** `--pilot-minutes` (16..360, режим пилота §11; `session.json.pilot`); `debug = duration_s < 3600`; несколько сессий в сутки — части `-p2`, `-p3`… (`session.json.binlog_files`) | каталог сессии: `<SYMBOL>-<день UTC>.binlog` на инструмент, `gaps.csv`, `clock.csv`, `session.json` — это имя читают все остальные команды (`commands::lob::session_binlog_for`) |
| `lob record --symbol --root` | запись **одного** инструмента (legacy-путь, не пул) |
| `lob verify` | `verify.csv`, `verify-<SYMBOL>.status` (`ok`/`fail`) — читает `watch`/`pilot` |
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

`docs/findings/` содержит только вердикты записи и пилота; `profiles-*`/`backtest-*`/
`shortlist-*` боевых ещё нет — сбор сессиями не начинался. Пул в `instruments.csv` —
отладочный (8 из окна 300 с, `k=1.0` заглушка), боевой `lob pick --window-secs 3600`
не запускался. Боевые данные: `data/pilot-battle/20260911T224019Z/` (30 мин, gitignored).

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

## Грабли

- `--h3-mode floor|percentile` обязателен, умолчания нет — у `levels`, `markout`,
  `watch`, `profiles`, `shortlist` (проверено `--help` каждой); `pilot` этот флаг
  не берёт — он гоняет `floor` и `percentile` сам, оба режима за один прогон.
  `floor` берёт `h3_lots` из `instruments.csv`, `percentile` требует часа прогрева
  (`--warmup-ms`, умолчание 3 600 000 — на 5-минутной записи даёт `levels=0`,
  это прогрев, не баг; для отладки `--warmup-ms 0`)
- `instruments.csv` в корне записи = **уже отобранный пул** (десять на боевом окне,
  меньше на отладочном — ниже порога глубины отсеиваются), не список всех
  кандидатов; пишет `lob pick`, читают `lob session`/`levels`/`record`/`power`
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
  источник: `lob probe`/`clock.csv`, не изобретать парсер по умолчанию
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
  до `FRAME_TARGET_RECORDS` за `BufWriter` — крах теряет до батча; p99 разбора/очереди — из
  гистограммы (`LatencyHistogram`, ≤ 1.5625 %), не из `Vec`; замер до/после —
  `docs/findings/collector-2026-09-12.md`
- `sync.py` печатает по-русски в кодировке консоли — mojibake в выводе нормален
- Bybit отдаёт `403` с части стран (CloudFront); с этой машины доступ есть

<!-- autopilot:end -->
