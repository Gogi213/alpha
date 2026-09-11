# Границы и правила проекта

Первое, что читает исполнитель, до того как напишет строку. Всё ниже —
**решено**.

---

## Что строим

**Бота.** Фаза 1 — вердикт по профилям на реплее и бэктесте. Фаза 2 — живой
режим **той же** функцией стратегии плюс коннектор. Каждый модуль, который ты
пишешь, — модуль бота. Если то, что ты пишешь, не сможет работать на живом
сокете с бюджетом ниже — оно написано неверно, даже если тесты зелёные.

---

## Правила, которые нельзя вывести из кода

**Стек.** Rust, `rust-toolchain.toml` = `1.93.1`, MSRV `1.91.1`. Зависимости
закрыты: `tokio`, `tokio-tungstenite`, `reqwest`, `serde`, `serde_json`, `csv`,
`clap`, `anyhow`, `futures`, `chrono`, `zstd`, `hftbacktest`, `hmac`, `sha2`;
dev — `tempfile`. **Недостающая зависимость — `BLOCKED`, не установка.**

**Команды.**

```bash
cargo build --release
cargo test --release
cargo clippy --all-targets -- -D warnings
cargo fmt --check
cargo run --release -- lob <подкоманда>
```

**Гейт GC — условие закрытия таска.** Бюджет линта в репозитории **ноль**.

**Не трогать без указания в таске:** `.github/workflows/`, `deny.toml`,
`rust-toolchain.toml`, `tests/fixtures/`, `fuzz/`.

**Любой прогон против живой биржи ради проверки — не дольше 5 минут.**

---

## Горячий путь — семь запретов, каждый блокирует коммит

Горячий путь — всё от возврата из `recv` до отдачи кадра сокету: разбор,
книга, разметка, триггер, `send`. Бюджет **`p99 < 5 мс` на весь путь**,
по этапам — `PLAN.md` 3.1.

1. **Аллокация на событие** после прогрева — ноль. Счётчик на глобальном
   аллокаторе (`alloc_count.rs`) это меряет; проигрывание 10⁶ событий.
2. **`Instant::now()`, `SystemTime`, `std::time`** в горячем пути — ноль.
   Время приходит через трейт `Clock` (`ARCHITECTURE.md` A2). `levels.rs:689`
   грепает собственный исходник — делай так же.
3. **`async` с захватом состояния** в решающем потоке — нет. Один поток решений,
   канал от потока ввода-вывода (A3) — `tokio::sync::mpsc` + `blocking_recv()`,
   пока `crossbeam` нет в списке зависимостей (D04).
4. **REST в любой ветке событийного цикла** — ноль, включая редкие.
   Вызов из `select!` останавливает разбор на весь round-trip; при вложенном
   рантайме роняет процесс (`SETTLED.md` В-1).
5. **Сборка или подпись ордера после триггера** — нет. Payload собран и подписан
   **до**; триггер делает только `send`.
6. **`f64` для цены или размера** — нет. Целые тики и лоты (A1).
7. **`BTreeMap`/`HashMap` на пути события** — нет. Книга — предвыделенное
   кольцо (A4).

---

## Пять правил, которые не проигрывают

1. **Изобретённое число запрещено везде.** Измеримое — измеряется. Назначаемое
   — до данных и коммитом. Параметр без умолчания лучше правдоподобного.
2. **Секрет никогда не запрашивается, не печатается, не пишется.** Наружу имена.
3. **Расстояние — только в bps.**
4. **Шаг не закрыт, пока не существует то, что называет done-condition.** Число,
   файл или вердикт — закрывает артефакт на диске.
5. **Стратегия — одна функция `on_event<MD, B: Bot<MD>>`**, и она идёт в
   `Backtest` и в `LiveBot` без правок. Вторая реализация — *Reinvention*.

---

## Границы модулей

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `binlog/` | форматом лога | `Reader`, `Writer` по кадрам | дельты, varint, `zstd`, потолок кадра |
| `book/` | книгой | `MarketDepth` крейта, топ-50 обеих сторон | целые тики, переопорку |
| `bybit/conn` | сокетом и ресинком | `Transport` за трейтом, события с `local_ts` | переподписку, разрывы `u` |
| `bybit/ws` | разбором | типизированные события, флаг `BT` | JSON площадки |
| `bybit/rest` | REST вне пути | `PublicRest` за трейтом, таймаут 10 с | десятичные |
| `bybit/verify` | доверием к книге | три теста и вердикт | выравнивание по `u`, `out_of_range` |
| `bybit/probe`, `bybit/trade_ws` | задержкой | RTT: медиана, p95, две метки | подпись, транспорт |
| `feed/` | единым потоком | `Feed { fn next_event(&mut self) -> Option<Event> }` — бинлог или сокет | источник |
| `lob/levels` | жизнью уровня | `LevelRecord`: шесть признаков, класс | пол `H3`, 70/20, окно повторов |
| `lob/markout` | движением | `m` на четырёх горизонтах, флаг `unreachable` | полярность, база |
| `lob/costs` | деньгами | `net`, `net_fill` по наблюдению | комиссии, проскальзывание |
| `lob/strategy` | решением | `on_event<MD, B: Bot<MD>>`, готовый подписанный payload | триггер, состояние |
| `stats/` | выводом | бутстрап-t, совместный интервал произведения, разрешение сетки | Уэбб, реплики |
| `lob/final_metrics` | защитой от переобучения | DSR, PBO, CPCV, `SR0`, требуемый Шарп | формулы |
| `lob/shortlist` | перебором | сетка семи осей, `N`, заморозка, вердикт | сплит, пригодность |
| `lob/watch` | размером выборки | `n`, `G` по профилю, флаг | годность суток |
| `lob/backtest` | вердиктом на очереди | отчёт на N профилей, `fill`, пропуски | `RiskAdverseQueueModel`, RTT |
| `lob/export` | контейнером | `npy` | раскладку `Event` |
| `commands/lob/*` | CLI | подкоманда на артефакт | остальное |

---

## Швы для тестов — шесть, и это потолок

1. `Transport` — фейковый сокет.
2. `PublicRest` — фейковый REST; `worker_threads = 1`.
3. `binlog::Reader` — враждебные байты; фаззер.
4. `LevelRecord` — синтетика с известными классами.
5. Живой голден `tests/fixtures/BTCUSDT-2026-09-09.binlog` — инварианты.
6. **`Bot<MD>`** — стратегия на `Backtest` крейта. Единственный способ доказать,
   что фаза 2 получит тот же код.

Тест не на одном из шести — стоит на внутренностях, и это находка.

---

## Что уже есть и переизобретать не надо

| Нужно | Есть | Где |
|---|---|---|
| счётчик аллокаций | | `alloc_count.rs` |
| часы, `clock.csv` | | `bybit/clock.rs` |
| подпись, ключи из окружения | | `bybit/sign.rs` |
| сверка по `u`, семантика «ни разу не держала» | | `bybit/verify.rs` |
| сайдкар в ОС-потоке | | `bybit/verify_sidecar.rs` |
| RTT по WS trade, две метки | | `bybit/trade_ws.rs` |
| DSR, PBO, CPCV, `expected_sharpe_under_null` | | `lob/final_metrics.rs` |
| бутстрап-t, Уэбб, разрешение сетки | | `stats/mod.rs` |
| **совместный ресэмплинг двух серий** — расширять, не писать | `disjoint_contrast` | `lob/cells.rs` |
| журнал испытаний | `RunRow`, `append_run_row` | `lob/runs.rs` |
| пул по правилу — **верно** | `build_pool` | `commands/lob/pick/pool.rs` |
| сплит 60/40, пригодность, заморозка, `ProfileRow` с `fill`/`net_fill` | | `lob/shortlist.rs` |
| движок бэктеста на `Bot<MD>` | `backtest.rs:506` | `lob/backtest.rs` |
| экспорт `npy` | | `lob/export.rs` |
| `ROUNDTRIP_FEES_BPS = 7.5`, `GREEN_NET_BPS = 3.0` | | `lob/costs.rs` |
| `HORIZONS_MS = [100, 1000, 10000, 60000]` | | `lob/markout.rs` |

## Что построено под отменённый дизайн — не переиспользовать

`cells::distance_bucket(dist_ticks)` · гейт `G2Verdict`, `is_c1`/`is_c2` ·
`costs::select_verdict_cell` · `commands::lob::select_final_two` · `watch::is_c1`,
`TRIGGER_N_C2`, `TRIGGER_G`, `has_gap_over_6h` · `record::DAY_BUDGET_BYTES` и
гейты места · `backtest::BacktestReport` поля `c1`/`c2`/`verdict_cell` ·
`MIN_CLUSTERS`/`CONFIRM_MIN_G = 12` · **`--warmup-ms` и `--repeat-window-ms`
с умолчанием 3 600 000** (разведка: `levels=0` на пяти минутах).

Из отменённого сохраняется одно: `cells::disjoint_contrast`.

---

## Когда план оказался неверен

Правится **спека** и появляется `D##` в манифесте с тем, что доказал код. `D##`
никогда не снимает требование — невозможное требование есть вопрос владельцу.
Идея по ходу — `A##` с родителем. Отклонение от спеки без `D##` — находка оси
Спека, каким бы хорошим ни был повод.

---

# Что уже построено

## Из таска 01 — вычистка и разрез

- `src/commands/lob/` — каталог-модуль вместо одного файла: `mod.rs` (`LobCommand`, `dispatch`, общие `replay_symbol`, `ReplayStats`/`ReplayDay`, `side_name`/`outcome_name`/`death_name`, `DEFAULT_WARMUP_MS`/`DEFAULT_REPEAT_WINDOW_MS`, `day_tallies`, `#[cfg(test)] test_support`) + `pick.rs`, `record.rs`, `verify.rs`, `export.rs`, `clock.rs`, `probe.rs`, `levels.rs`, `markout.rs`, `watch.rs`, `pilot.rs`. Каждая подкоманда — свой файл, своя зона.
- **`crate::stats::G_MIN: usize = 7`** — единственная константа минимума кластеров. `MIN_CLUSTERS` и `shortlist::CONFIRM_MIN_G` больше не существуют — бери `G_MIN`.
- `select_final_two` → `survivors_above_depth_floor` — возвращает **всех** членов пула выше порога глубины, не двух. Таск 08 переделывает отбор целиком.
- `cells::distance_bucket` / `DistanceBucket` (в тиках) — удалены. Расстояние только в bps через `shortlist::DISTANCE_BOUNDS_BPS`.
- Гейты свободного места и `DAY_BUDGET_BYTES` — удалены из `record.rs`. Дискового потолка нет.
- `watch::has_gap_over_6h` — больше не предикат годности суток; таск 07 переписывает годность.
- `src/commands/lob/pick/` — подкаталог (ремонт по ревью): `mod.rs` — `PickArgs`, `PickReport`, `run_pick`/`run_pick_async`, реэкспорты; `pool.rs` — `build_pool`, исключения, `NON_CRYPTO_BASES`, `CandidateMeta`; `coverage.rs` — `coverage_top50_bps`, `eligible_baskets`, `count_eligible_trials`; `depth.rs` — `DepthSample`, `MeasuredCandidate`, `survivors_above_depth_floor`, `PickError`, `DEPTH_FLOOR_USD_E9`; `measure.rs` — сетевая оболочка (`measure_prefiltered`, `measure_one_symbol`); `order_size.rs` — `order_size_22a`; `table.rs` — `CandidateRow`, `build_candidate_table`, `write_candidate_table_csv`, `write_instruments_csv`. Все файлы ≤ 690 строк. Таск 08 работает внутри этого каталога.
- `grep -rn "H10" src/` — пуст. Модель двух кандидатов не упоминается нигде как действующая.
- `docs/plan/SETTLED.md` В-29 — предрегистрация в два этапа (до пилота / после пилота, до первой сессии сбора).
- Документы: `docs/plan/REQUIREMENTS.md` переписан; `SETTLED.md` ПЛАН-2 — эррата в строке; `docs/plan/archive/` — `OPEN_QUESTIONS.md`, `critique-*.json`, `README.md`.

## Из таска 02 — `H3` в двух режимах

- `lob::levels::H3Mode { Floor { h3_lots: i64 }, Percentile { h3_lots: i64 } }`; `LevelsConfig { mode: H3Mode, warmup_ms, repeat_window_ms }` — поле `h3_lots` напрямую больше не существует.
- `commands::lob::levels::H3ModeArg` (clap `ValueEnum`: `floor` | `percentile`); `resolve_h3_mode(root, symbol, mode, Option<i64>) -> anyhow::Result<H3Mode>`; `h3_lots_for_symbol` — чтение колонки `h3_lots` из `instruments.csv` в корне записи (колонку пишет таск 08). `markout`/`pilot`/`watch` зовут их через `super::levels::`.
- CLI `lob levels|markout|pilot|watch`: `--h3-mode` **обязателен, умолчания нет**; `--h3-lots` — `Option<i64>`, только для `percentile`; `floor` берёт `h3_lots` из `instruments.csv`, без него — ненулевой код.
- Прогон короче `repeat_window_ms`: первая строка CSV `# lob levels: debug — …` и тот же текст в stderr. Результат такого прогона — не данные.
- Проверка на разведке: `floor` (`h3_lots=1`) на `data/recon5/SOLUSDT` → `levels=531`; `percentile` → `levels=0` с `debug`.

## Из таска 03 — `net_fill` и совместный интервал

- `costs::FillObservation { day_cluster: i64, net_bps: f64, filled: bool }` — одно наблюдение (вход по уровню).
- `costs::net_fill_bps(&[FillObservation]) -> Option<f64>` — Σ netᵢ·fillᵢ / N; `costs::fill_rate(&[FillObservation]) -> Option<f64>`; `costs::format_fill_column(Option<f64>) -> String` — колонка `fill` для CSV, печатать **всегда** (проводка — таски 06/10).
- `costs::net_fill_interval(&[FillObservation], alpha: f64, replications: u32, seed: u64) -> Option<NetFillInterval { n_filled, n_total, point_bps, lower_bps, replications, seed }>` — совместный бутстрап пары `(net, fill)`, один вес Уэбба на обе серии, произведение на реплику. **`alpha` — обязательный параметр без умолчания**; `lower_bps` — то, что сравнивается с нулём.
- `cells::joint_product_interval(day_sums, alpha, replications, seed) -> Option<JointProduct>` (`pub(crate)`) — расширение `disjoint_contrast` через общий `joint_two_series_bootstrap`; второй ресэмплер не писать.
- `stats/mod.rs` не менялся: веса Уэбба и `SplitMix64` переиспользованы.
- Открыто: `filled` не различает «не успел за 2 с» и «позиция уже открыта» — причина пропуска (история 36) — таск 09/11.

## Из таска 06 — маргиналы семи осей, повторяемость 1/2/≥3, час суток

- Структура испытаний — Decision 26/26а (`SETTLED.md` В-18): `shortlist::build_profile_grid` — маргиналы по каждой оси плюс один крест инструмент × исход × расстояние по пригодным парам, id `cross:{sym}|{outcome}|{dist}`; непригодная корзина в выводе отсутствует. Полного семиосевого креста **нет** и не будет (D03).
- `shortlist::nominal_grid_size` — из длин массивов осей: маргиналы `(n+19)` + `n·15` = 179 на десять инструментов (было 178; +1 — третья корзина повторяемости).
- `shortlist::REPEAT_LABELS = ["1","2",">=3"]`, `repeat_bucket(u32)`: `0→"1"`, `1→"2"`, `_→">=3"` — `repeat_count` из `lob/levels.rs` считает прошлые рождения (0-based).
- `shortlist::hour_dependence_test(&[HourDayObservation { day, hour_utc, value }], replications, seed) -> Result<f64, HourTestError>` — wild-cluster bootstrap-t (`stats::wild_cluster_bootstrap_t`) по суточному произведению `(hour − mean)·(value − mean)`, двусторонний; H0 — нет линейной зависимости от часа; α = `stats::GATE_ALPHA`. `log_hour_test` / `total_trials` пишут в `runs.csv` тем же путём, что `log_profile_trials` — каждый тест на час есть строка `runs.csv`; число испытаний = число строк.
- `ProfileRow` не менялся; `fill`/`net_fill` считает таск 03, проводка колонки — таск 10.
- Открыто: докстрока `stats::GATE_ALPHA` привязана к отменённой поправке C1/C2 — поправить владельцу `stats/` (таск 13).

## Из таска 04 — `lob session` и `Feed` бота

- `src/feed/`: `trait Feed { fn next_event(&mut self) -> Option<Event> }` — две реализации: `feed::replay::ReplayFeed` (бинлог через `bybit::verify::FileReplayer`) и `feed::live::LiveFeed` (N соединений `bybit::conn::Connection` на одном однопоточном рантайме в одном ОС-потоке, `tokio::sync::mpsc` + `blocking_recv()` в поток решений — то же отступление от `crossbeam`, что уже стоит в `conn.rs`). Вызывающий код не различает источник. Таск 15 подключает `on_event` к этому же `Feed`.
- `feed::Event::Market { symbol: u8 (индекс в пуле, не String), local_ts_ns, parse_latency_ns: Option<i64>, payload }` — символ по индексу знает вызывающий (`session.rs` держит `Vec<SymbolState>` в порядке `LiveFeed::spawn`).
- `bybit::conn::ConnEvent::Message { local_ts_ns, parsed_ts_ns, event }` — вторая метка `Clock` сразу после разбора; из неё суббюджет «разбор» (`PLAN.md` 3.1).
- CLI: `lob session --pool-instruments <instruments.csv> --root <dir> --minutes <5..15> [--base-url] [--ntp-addr]` — весь пул одновременно, по `.binlog` на инструмент, `gaps.csv`, `clock.csv` (≥ 1 строка за сессию), `session.json { started_utc, start_hour_utc, duration_s, instruments, records_total, gaps, clock_samples, parse_p99_ns, out }`. Лимит топиков Bybit проверен по документации (futures — без числового предела, 21000 символов `args`) и печатается `feed::live::print_topic_budget`.
- Остановка по времени — через `Clock`, не `Instant::now()`; CPU/RSS раз в 30 с — фоновый ОС-поток (Windows `Get-Process`, Linux `/proc/self/status`), не в горячем пути.
- Аллокации: `write_market_event` — скретч-буфер в `SymbolState`, тест `alloc_count` на 10⁶ событий через `ReplayFeed`. `FileReplayer` (`bybit/verify.rs`) аллоцирует через `mem::take` — вне зоны, отдельная находка.
- Первый живой замер (5 мин, `debug`): `parse_p99_ns` = 251.8 мкс против калибровочного суббюджета 200 мкс (`PLAN.md` 3.1 допускает пересмотр суббюджетов по замеру; общий бюджет 5 мс — нет). Пул для прогона собран вручную из 10 строк `data/pick22a/instruments.csv` (файл ~863 строки до таска 08).
- `src/commands/lob/pick/measure.rs` — механическая правка под `ConnEvent::Message { parsed_ts_ns }` (зона таска 08, поведение не менялось).

## Из таска 05 — `lob power`, гейт G-POWER-A

- CLI `lob power [--root <PATH>] [--hour-tests <usize>]` — ноль данных; stdout одной строкой: `power: n=<usize> sr0=<f64.4> required_sharpe=<f64.4> dsr_target=<f64.2> num_obs=<usize>`. На пуле из десяти: `n=179 sr0=2.7291 required_sharpe=3.1306 dsr_target=0.95 num_obs=100`.
- `commands::lob::power::{run_power, PowerArgs, PowerSummary { n, sr0, required_sharpe, dsr_target, num_obs }}` — таск 09 сравнивает с замеренным Шарпом (G-POWER-B), таск 13 печатает в шапке.
- `lob::final_metrics::expected_sharpe_under_null_for_trial_count(n) -> Option<f64>` и `required_sharpe_for_dsr(n_trials, num_obs, dsr_target) -> Option<f64>` — обёртки над `expected_sharpe_under_null` / `dsr`, формулу не дублируют. `SR0` при допущении `V = 1` (единичная дисперсия пробных Шарпов — нулевая нормировка Bailey–López de Prado без пробных данных, в докстроке).
- `N` = `shortlist::nominal_grid_size(pool_size)` — номинал при полной пригодности; когда `instruments.csv` получит `coverage_bps` (таск 08), источником становится `build_profile_grid` по пригодным парам — таск 09 переключает.

## Из таска 07 — `lob watch` на сессиях

- Годность суток: `lob::watch::session_day_eligible(&SessionDay) -> bool` — «≥ 1 сессия суток состоялась и прошла сверку»; сессия без сверки выбрасывается целиком; единственная несверенная сессия → сутки не кластер. Старый предикат «разрыв > 6 ч» не используется.
- Маркер сверки сессии: `<session_dir>/verify-<SYMBOL>.status`, содержимое ровно `ok` — **пишет таск 09** (после `lob verify`), `watch` только читает.
- Счётчик: `lob::watch::WatchSample { n_total(), g(), is_due() }` на пару `(symbol, profile_id)`, кормится `SessionTally { session_id, day_utc, start_hour_utc, symbol, verified, n }`; `is_due()` = `n ≥ shortlist::CONFIRM_MIN_N (100)` и `G ≥ stats::G_MIN (7)` — тот же код у гейта и у отчёта (тест на общей фикстуре).
- Артефакты: `progress-<symbol>-<profile>.csv` (`day_utc, symbol, profile_id, session_start_hours_utc, sessions, n, eligible`) — строка на сутки с часами старта сессий; флаг готовности `ready-<symbol>-<profile>.flag` через `SessionReadyFlag` / `require_session_ready_flag` — `lob markout --confirmatory` обязан требовать его (проводка в `markout.rs` — см. ревью).
- `commands::lob::watch::{WatchArgs, run_watch, WatchSummary}` — обход сессий по `session.json`, реплей одного файла, классификатор профиля. Сегодня классифицирует только маргиналы `side/outcome/repeat/instrument`; `cross:`/`dist`/`size`/`life` требуют середины при рождении уровня — таск 12 кормит `WatchSample` через полную сетку.
- `lob watch` не вычисляет markout (греп-тест).
- Старая машинерия C1/C2 (`is_c1`/`is_c2`/`TRIGGER_*`/`DayTally`/`day_eligible`/`WatchState`/`ReadyFlag`) не тронута — ею ещё пользуются `cells.rs`, `pilot.rs`, `markup.rs`, `mod.rs::day_tallies`; снос — таск 14.

## Из таска 08 — `lob pick` начисто, пол `H3`

- CLI `lob pick --window-secs <u64, обязателен> --h3-k <f64, обязателен> [--instruments-out <path>=instruments.csv] [--root --candidates-out --base-url]`. Окно ≠ 3600 → первая строка `candidates.csv`/`instruments.csv` и stderr: `# debug: окно N с — результат не годится для отбора, только для отладки`; при 3600 — тишина (юнит-тест `h3::debug_window_warning`).
- `instruments.csv` (корень): прежние колонки + `h3_lots, k, median_trade_lots, window_start_utc_ms, window_secs`; `h3_lots = floor(k × median_trade_lots)` (лоты, целое), медиана — `publicTrade` тем же окном. Читатели (`levels::h3_lots_for_symbol`, `record.rs`) терпят строку `#`.
- **`k` — числа нет ни в задаче, ни в спеке, ни в плане**: обязательный флаг без умолчания; отладочный прогон шёл с `k = 1.0` (пол = медиана) как нейтральной заглушкой — **не решение**; боевое значение — владелец, до боевого прогона таска 09.
- `CLUSDT` (`CL` — нефть WTI) в `pool::NON_CRYPTO_BASES` — исключён, причина колонкой `candidates.csv` (В-1, умолчание владельца).
- Новые швы: `pool::base_coins_considered_until_pool_complete` (печать всех базовых активов, которых правило видело до десятого выжившего), `coverage::book_already_costs` (строка отчёта, Decision 26б), `h3::{h3_lots_floor, debug_window_warning, H3FloorInfo}`.
- Отладочный прогон 300 с: `data/pick-debug/20260911T030316Z/`; десять измеренных: ADA, DOGE, ENA, HYPE, IOST, NEAR, PUMPFUN, SOL, XRP, ZEC. Боевое окно 3600 с и заморозка — таск 09 после G-DEBUG, той же командой.
- **Ремонт по ревью:** `instruments.csv` (корень и `--instruments-out`) содержит **только пул** — строки `selected_for_pilot` в порядке ранга (на отладочном окне 300 с — 8: ZEC/IOST ниже порога глубины), с колонками `h3`; полный список — `docs/plan/candidates.csv`. Единственный читатель `instruments.csv` — `pick::table::instruments_csv_reader(path) -> csv::Result<Reader<File>>` (терпит `#`), им пользуются `levels::h3_lots_for_symbol`, `record::load_steps_for_symbol`, `session::load_pool`, `power::pool_size`; `pick::table::instruments_for_pool(instruments, selected) -> Vec<Instrument>` — фильтр до выживших. `lob power --root .` на нём → `n=147` (8 инструментов).

## Из таска 15 — горячий путь: `on_event`, ордер до триггера, `lob react`

- `lob::strategy::on_event<MD: MarketDepth, B: Bot<MD>>(bot: &mut B, state: &mut StrategyState) -> Result<Action, B::Error>` — **единственная** стратегия; один вызов на событие, сама не зовёт `elapse`/`wait`; `StrategyState::new(asset_no, sigma, qty, first_order_id)`; переиспользует `backtest::{entry_side, entry_price, exit_price, ENTRY_TTL_NS, HOLD_NS}`. Шов 6 доказан тестом на настоящем `hftbacktest::backtest::Backtest<HashMapMarketDepth>`. Таск 11 встраивает в бэктест-раннер, фаза 2 — в `LiveBot`.
- Ордер до триггера (D-ОРДЕР): `bybit::trade_ws::OrderSigner` — трейт-шов подписи; `refresh_ready_maker_order(...)` собирает и подписывает кадр под цену своей стороны спреда **до** триггера; `send_ready_maker_order(...)` — только `send` готового кадра; тест считает вызовы `sign` через фейк — из ветки срабатывания ноль. Ключи — только `BYBIT_API_KEY` / `BYBIT_API_SECRET` из окружения.
- `bybit::clock::MonotonicClock` — `Clock`-совместимые монотонные метки (`Instant` внутри, не `SystemTime`) — для меток этапов.
- CLI `lob react --symbol --tick-e9 --step-e9 --qty-e9 [--side buy|sell] [--recv-window-ms] [--minutes ≤5] [--root] [--out] [--host-id] [--probe-csv]`; ядро `run_react_over_feed(feed: &mut dyn Feed, ...)` — тесты на сценарном `Feed`, прод — `LiveFeed`. Печать: по этапам `разбор / книга / триггер / ордер(send) / весь путь` — `n, median_ns, p99_ns`; `G-LAT: PASS|FAIL|не объявлен` (< 1000 срабатываний → не объявлен); `horizon <ms>ms: reachable|unreachable` по `HORIZONS_MS` (D-HOR, 20 %); `host=<id> probe_rtt=median_ns=.. p95_ns=..|не измерен`. `ReactReport`/`format_report` — форма для предрегистрации (таск 09б).
- Триггер в `react` — смена лучшего тика своей стороны, не density-сигнал `levels` (иначе 1000 срабатываний за 5 минут не набрать) — осознанное упрощение замера, в докстроке модуля.
- **Ремонт по ревью (запрет 1):** `ReadyMakerOrder::new() / .rebuild(signer, …) / .frame_str() / .price_e9()` — буферы `String`/`Vec<u8>` + `[u8; 64]` hex приватные, ноль аллокаций после первого `rebuild` (тест 10⁶); `OrderSigner::sign_into(&self, …, out: &mut [u8; 64])`; `react::ReactCoreState` + `process_event` — шаг цикла, `rebuild` **только** внутри `if changed`; тест `process_event_allocates_nothing_per_event_after_warmup` (10⁶). `refresh_ready_maker_order` удалена. Открыто: реальный `Credentials::sign_into` делегирует старому `sign()` из `bybit/sign.rs` (вне зоны), который аллоцирует — ноль доказан для фейкового подписанта; буферный HMAC в `sign.rs` — таск 14.
- **Открыто:** живой `lob react` не выполнялся — тёплый аутентифицированный WS trade требует `BYBIT_API_KEY`/`BYBIT_API_SECRET` в окружении (не запрашиваются); чисел G-LAT и RTT хоста нет. Греп-тест `Instant::now()` — `strategy.rs`/`react.rs`; долг по `feed/` не закрыт (вне зоны).

## Из таска 09(а) — `lob pilot --debug`, цепочка до markout

- CLI `lob pilot --debug --pool-instruments <csv> --root <dir> --minutes <N ≤ 5>` — весь пул, цепочка `session → verify → levels (floor и percentile) → markout`; не пишет `runs.csv` и не трогает предрегистрацию. Без `--debug` — боевой путь (`--hours`), пишет `runs.csv` — **не запускался** (D-ОТЛАДКА, `k` не назначен).
- Печать по инструменту: `rate_floor= / rate_percentile=` (уровней/мин), `eaten/pulled/mixed`, `m10s(n=, mean=, lower=)`, `sharpe=`, `verify=ok|fail`; сводка `pilot-debug-summary.csv` с меткой `debug`.
- Маркер сверки `<session_dir>/verify-<SYMBOL>.status` (`ok`/`fail`) **пишет `pilot`** — читает `lob watch` (таск 07).
- Заготовки для 09(б)/10–13: `sessions_needed_for_profile`, `g0_verdict`, `power_b_gap`, `process_instrument`, `BATTLE_COUNTED_TAIL_MINUTES` (второй час §11 — фильтр ещё не применён, открыто).
- Первый сквозной отладочный прогон (3 мин, `data/pilot-debug/20260911T040154Z/`): 8/8 инструментов без дефекта, все `verify=ok`; `floor` 24–256 уровней/мин, `percentile` = 0 (прогрев); `m_10s` 0.21–1.08 bps; `pulled` 82–95 %. G-DEBUG **не объявлен** — цепочка без `profiles → backtest` (таски 10/11), объявление — 09(б).
- Открыто: `markout-<SYMBOL>.csv`/`session.json` без собственной метки `debug` в шапке (зоны 04/02); `percentile` в `--debug` берёт `h3_lots` от `floor` (результат тот же — 0 до часа прогрева).

## Из таска 10 — `lob profiles`, таблица профилей

- CLI `lob profiles --root <dir> --candidates-csv <path=docs/plan/candidates.csv> --h3-mode floor|percentile [--h3-lots] [--warmup-ms] [--repeat-window-ms] [--allow-unverified] [--out] [--now-utc] [--runs-out]` → `docs/findings/profiles-<дата>.csv`. Шапка: `# lob profiles: h3_mode=.. warmup_ms=.. repeat_window_ms=.. alpha=.. replications=.. seed=..[ debug]`. Колонки: `profile_id, n, eaten_share, pulled_share, mixed_share, {m, m_lower, raw, unreachable}×{100ms,1000ms,10000ms,60000ms}, net_bps, fill, net_fill, net_fill_lower, session_start_hours_utc`. Строка на каждый id сетки Decision 26/26а, включая `n = 0`; непригодная корзина отсутствует. `raw` — сырое движение середины без нормировки знаком, всегда.
- `commands::lob::profiles::{run_profiles(&ProfilesArgs) -> Result<ProfilesSummary { rows, out, debug }>, ProfilesArgs}` — для тасков 12/13. Тест детерминизма: снести файл, прогнать, те же числа (seed в шапке).
- Корзины размера и времени жизни считаются здесь (`SIZE_BOUNDS`, `LIFETIME_BOUNDS_MS` — по таблице спеки «Профиль»; тест синхронности с `shortlist`), `LevelRecord` их не несёт.
- **Ремонт по ревью:** `pub trait FillModel { fn filled(&self, symbol, rec: &LevelRecord, mids: &[MidSample]) -> Option<bool>; fn label(&self) -> &'static str }`, `NoFillModel` (label `none`); `run_profiles_with_fill_model(args, &dyn FillModel)` — рабочая; `run_profiles` = обёртка с `NoFillModel`. Шапка несёт `fill_model=none|backtest`; при `none` колонки `fill/net_fill/net_fill_lower` — литерал `not_measured` во всех строках. Таск 12/13 реализует `FillModel` поверх `lob::backtest`. `raw_<horizon>` — **со знаком** (`.abs()` снят), тест на зеркальную пару (равное `m`, противоположный `raw`).
- Открыто: `unreachable_*` печатает `not_measured` — живого отчёта G-LAT нет и `react.rs` не выставляет читателя; `hour_dependence_test`/`log_hour_test` не подключены к `runs.csv` (таск 12/13); `shortlist::ProfileRow`/`write_profiles_csv` не совпадают с колонками таска — свой писатель.

## Из таска 11 — `lob backtest`, вердикт на N профилей

- `lob::backtest`: `c1`/`c2`/`verdict_cell` не существуют; `BacktestReport { profiles: Vec<ProfileReport> }`, `ProfileReport { profile_id, median: ProfileRun, p95: ProfileRun, comparison: TableComparison, g4: G4Verdict }`; `drive_profile` гоняет **`strategy::on_event`** (один символ функции), движок `RiskAdverseQueueModel` не тронут; `build_backtest`. `MissLedger` — пропуски раздельно: `missed_timeout` (ордер не исполнился за 2 с) и `missed_busy` (позиция уже открыта). `BacktestReport::summary_lines()`, `ProfileReport.g4.is_pass()` — для таска 13.
- CLI `lob backtest --session-root --symbol --signals-csv --median-rtt-ns --p95-rtt-ns [--order-qty-e9] [--profiles-csv] [--out] [--pnl-out]`. RTT — **обязательные флаги без умолчания** (источник — `lob probe`/`clock.csv`, парсер не изобретался). `signals-csv`: `profile_id,side,birth_ms` — вход сигналов; разметка по осям в сигналы — таск 12.
- Артефакты: `docs/findings/backtest-<дата>.csv` — строка на `profile_id × rtt`: `signals, fills, missed_timeout, missed_busy, incomplete, mean_net_bps, fill, net_fill, net_fill_lower, table_*, diff_net_fill_bps, g4`; `backtest-<дата>-pnl.csv` — `profile_id, rtt, step_index, cum_net_bps` (две кривые: median и p95). Сравнение с таблицей профилей (`--profiles-csv`) — колонки `table_*`/`diff_net_fill_bps`.
- **Ремонт по ревью:** `read_table`/`ProfilesTableRow` читает реальный формат таска 10 (26 колонок по имени, `#`-шапка); `TableEstimate.net_fill_not_measured` — сравнение по `net_bps`, когда таблица несёт `not_measured`; `--order-qty-e9` **обязателен** (лот площадки по `order_size_22a`, не шаг книги); `--debug` — метка в шапке `# lob backtest: rtt_median_ns=.. rtt_p95_ns=.. order_qty_e9=.. alpha=.. replications=.. seed=..[ debug]` в обоих CSV; `pnl.csv` всегда с заголовком. Сквозная проверка `profiles → backtest --profiles-csv` на `session-debug` соединилась.
- Открыто: шаг опроса `on_event` 10 мс — инженерный шаг драйвера, джиттер ≤ 0.5 % от `ENTRY_TTL_NS` (R-C принял); `order_qty_e9` не читается из `instruments.csv` автоматически; `--debug` — решение вызывающего, не автоопределение.

## Из таска 12 — `lob shortlist`, заморозка, подтверждение (механизм)

- CLI `lob shortlist --root --candidates-csv --h3-mode [--h3-lots --warmup-ms --repeat-window-ms --allow-unverified --out --now-utc --runs-out] --preregistration <file> --freeze-out <file> --freeze-commit <hash>` → `docs/findings/shortlist-<дата>.md` (`lob::shortlist::write_shortlist_md`: испытания, коммит, отпечаток, порог, таблица, вердикт). Разведочная часть — через `run_profiles_with_fill_model` по каталогам сессий до границы; `runs.csv` — строка на каждый id сетки (`log_profile_trials`).
- Предрегистрация границы: файл `exploratory: d1,d2\nconfirmatory: d3,d4\n` — **write-once** (`load_or_write_boundary`), потом только чтение; сплит по календарю 60/40 по суткам.
- Заморозка — механизм: `docs/findings/shortlist-frozen.txt` (id в строке, `#` — комментарий); `commands::lob::shortlist::require_shortlist_member` — в `lob markout --confirmatory --shortlist <path>` сразу после `require_session_ready_flag`; профиль вне списка → ненулевой код (тест).
- Подтверждение: `confirmatory_table` — строка на **каждый** замороженный id (`confirmed | unconfirmed | insufficient`), профиль не исчезает; порог — нижняя граница `net_fill` (`alpha = stats::GATE_ALPHA`), `n ≥ CONFIRM_MIN_N`, `G ≥ G_MIN`.
- `ScratchRoot`/`build_filtered_root` — фильтр сессий по суткам через временный каталог (копия/линк каталогов сессий; на многодневных данных — дорого, пересмотреть).
- Отладочный прогон на `data/pilot-debug/20260911T040154Z/`: `trials=147, shortlisted=29, debug=true` → `data/shortlist-debug/` (не коммитится).
- **Открыто для таска 13:** (1) `G` (сутки на профиль) на подтверждающей не меряется — `profiles.rs` не несёт число суток на профиль, `g = 0` → сегодня всё `insufficient` (консервативно, «confirmed» не выдумывается); нужен счётчик суток на профиль (расширение `profiles.rs`/`WatchSample` таска 07); (2) `hour_dependence_test`/`log_hour_test` не подключены к `runs.csv` (`hour_tests = 0` в `total_trials`); (3) `FillModel` = `NoFillModel` → `fill*` `not_measured`, `confirmed` недостижим до `FillModel` поверх `lob backtest`.
