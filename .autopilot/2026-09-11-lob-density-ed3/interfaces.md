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

## Из таска 13 — вердикт в шапке шорт-листа, DSR в вердикте

- Шапка `shortlist-<дата>.md`: `outcome: RED insufficient | RED market | GREEN-thin | GREEN  gate=G3-в value_bps=.. green_threshold_bps=3` / `trials / freeze_commit / fingerprint / threshold` (из констант) / `dsr_target=0.95 dsr=.. pbo=.. cpcv_oos_sharpe=..` / `G: .. p_grid_resolution: ..` / `jackknife_by_day: min/max/range | none`; таблица + справочные `net_fill_usd`, `green_threshold_usd` (вне гейта).
- `lob::shortlist::ShortlistVerdict { RedInsufficientPower, RedNoEdge, GreenThin, Green }` — красный по мощности печатается иначе, чем красный про рынок (R68). `decide_profile(n, g, net_fill_lower, observed_sharpe, total_trials)` — порог двигается DSR по фактическому `N`: `final_metrics::{DSR_TARGET, dsr_for_trial_count, required_sharpe_for_dsr}`; «нижняя граница > 0 без поправки» — больше не вердикт.
- `G` по суткам на профиль — `profiles.rs` колонка `g` (`ProfileAgg::days`, HEADER col 27). Тест на час — `hour_dependence_test` → `log_hour_test` в `runs.csv`; `total_trials` = `trials_from_runs_csv` (реальные строки, не номинал).
- `final_metrics::jackknife_sensitivity` — джекнайф-по-суткам (A03), механизм протестирован.
- Отладочный прогон (`data/shortlist-debug/shortlist-2026-09-11-t13.md`): `trials=147, shortlisted=29, confirmatory: пропущена` — «недостаточно данных», не красный про рынок.
- **Открыто → таск 16:** `FillModel` поверх `lob::backtest` **не реализован** — `observed_sharpe`/DSR/PBO/CPCV/jackknife печатают `none`, `Confirmed` недостижим; нужна структура `BacktestFillModel` (книжный поток сессии, не только `mids`) и подключение к `run_profiles_over`/`run_profiles_with_fill_model`.

## Из таска 16 — `BacktestFillModel`: путь к `Confirmed`

- `profiles::FillModel` += `fn prime_session(&self, symbol, binlog_path, &[LevelRecord]) {}` (умолчание — no-op, `NoFillModel` без изменений, dyn-safe): модель гоняет бэктест по сессии **один раз** (`drive_profile`), `filled` отвечает из кэша по `(side, price_tick, birth_ms)`.
- `commands::lob::backtest::BacktestFillModel::new(median_rtt_ns, p95_rtt_ns, order_qty_e9)`, `label = "backtest"`; `filled` — по настоящему исполнению `RiskAdverseQueueModel`, `None` вне окна.
- `profiles::resolve_fill_model(Option<i64> × 3) -> Result<Box<dyn FillModel>>` — все три флага или ни одного (ошибка иначе). `lob profiles` / `lob shortlist` получили `--median-rtt-ns --p95-rtt-ns --order-qty-e9` (без умолчаний). `profiles-<дата>.csv` — 28 колонок, добавлена `observed_sharpe`.
- Шапка шорт-листа: `dsr` — число (`dsr_for_trial_count` по лучшей `Confirmed`), `jackknife` — leave-one-day-out (нужно ≥ 7 подтверждающих суток, сквозной тест отсутствует); **`pbo`/`cpcv_oos_sharpe` — по-прежнему `None`**: нужен конвейер «испытания × периоды», не вместился (открыто).
- Отладочный прогон (`fill_model=backtest`, `debug`): `fill`/`net_fill` реальные (в основном ≈ 0 на 3–5 минутах), `observed_sharpe` — в основном `none` (мало исполнений на профиль); RTT для отладки — `lob probe --fixture` с фиктивным значением переменной `BYBIT_API_KEY` (не ключ) — числа отладочные, не данные.

## Из таска 09(б) — полная цепочка, G-DEBUG

- `lob pilot --debug` += `--candidates-csv` (умолчание `docs/plan/candidates.csv`), `--median-rtt-ns`/`--p95-rtt-ns` (`Option<i64>`, без умолчания — только запасной источник). RTT: `probe-<SYMBOL>.csv` → `clock.csv` (`bybit_rtt_ns`) → флаги (`resolve_backtest_rtt_ns`). Лот: `compute_order_qty_e9` = `order_size_22a` по полям `instruments.csv` + последняя цена из `levels-floor` CSV.
- Вердикт G-DEBUG: `session → verify → levels → markout → profiles → backtest прошла без дефекта на N/M` | `упала на шаге X`.
- `process_instrument(…, counted_tail_minutes: Option<f64>)` — боевой путь передаёт `Some(BATTLE_COUNTED_TAIL_MINUTES)` («считается второй час», §11), `--debug` — `None`; фильтр читает готовые `levels-*.csv` (`count_rows_with_birth_after`), шестого реплея нет.
- `stage2_preregistration_skeleton()` — печать имён полей предрегистрации этапа 2 (В-29) в конце боевого пути, без чисел.
- Регресс-тест: `--debug` не создаёт `runs.csv`.
- **G-DEBUG пройден:** живой прогон `data/pilot-debug/20260911T084259Z/`, 8 инструментов, ~2 мин: цепочка 8/8 без дефекта, `verify = ok` у всех, `runs.csv` не создан; G4 RED на всех профилях бэктеста (тонкий эдж на минутах — ожидаемо, не дефект).
- Открыто: повторный реплей инструмента (до 5 на инструмент) остаётся — `run_verify`/`run_levels`/`run_markout` возвращают только сводки; свести — таск 14 или отдельный. **Боевой пилот не запускался**: нужны `k` (`lob pick --h3-k`) и `BYBIT_API_KEY`/`BYBIT_API_SECRET` (для `lob probe`/G-LAT) от владельца.

## Из ремонта таска 15 — `lob react`: один домен часов, честная серия «весь путь»

- `feed::live::LiveFeed::spawn_with_clock<K: Clock + Clone + Send + 'static>(pool, clock)` — прод-вход с явным `Clock`; `spawn()`/`spawn_with()` без изменений (`SystemClock`, `lob session` не тронут). `run_react` делит один `MonotonicClock::start()` между `Feed` (метки `recv`/после разбора), стадиями книга/триггер/send и дедлайном — пять меток в одном монотонном домене.
- `ClockDomainFault`: отрицательная длительность любой стадии — `FAIL: clock domain`, отчёт не строится (регресс-тест, красный на прежнем коде).
- Серии: «разбор», «книга» и информационная `recv_to_book` — по **всем** событиям; **«весь путь» (`TriggerLatency.full_ns`), гейт G-LAT и `horizon_reach` — только по срабатываниям** (recv → send); `gated = triggers ≥ 1000`; горизонты печатаются только при `gated`; CSV `full_ns` пуст на строках без срабатывания. Тест: 5000 событий / 10 срабатываний → `gated = false`, `full.n = 10`.
- **Живой замер (`data/react-debug/20260911T113637Z/`, SOLUSDT, 5 мин, dry-run):** events = 9649, triggers = 63. Разбор p99 = 68.7 мкс (n = 8379); книга p99 = 222.5 мкс (калибровочный суббюджет 50 мкс — пересматривается по замеру, PLAN 3.1); триггер p99 = 0.5 мкс; send p99 = 0.2 мкс; **весь путь по 63 срабатываниям: median = 111.8 мкс, p99 = 1.77 мс** (< 5 мс, но `n < 1000`) — **G-LAT не объявлен**, горизонты не объявлены. Прежний смешанный расчёт занижал p99 в 6.4 раза. RTT хоста — не измерен (`lob probe` ставит реальные ордера — владелец). Для объявления G-LAT нужен прогон ~1.5 ч на SOLUSDT (после G-DEBUG допустим) — решение владельца.

## Из таска 17 — долг ремесла (три контекста)

- Один `trade_hit_from_record` (`commands/lob/mod.rs`, `pub(crate)`), один `is_trade_ev` (`bybit/verify.rs`, `pub(crate)`), один `best_prices` (`lob/strategy.rs`); `resolve_h3_mode`/`h3_lots_for_symbol` — в `commands/lob/mod.rs`; `read_clock_bybit_rtts` в `pilot.rs` — фильтр над `bybit::clock::read_rows`.
- `commands::lob::H3Args { h3_mode: H3ModeArg, h3_lots: Option<i64> }` (`#[command(flatten)]`) у `levels/markout/watch/profiles/shortlist`; `--h3-lots` с `--h3-mode floor` — ошибка. `commands::lob::ExecutionArgs { median_rtt_ns, p95_rtt_ns, order_qty_e9 }` у `profiles/shortlist` (`backtest` — свои обязательные флаги, `--help` не менялся).
- C1/C2 снесены: `grep "is_c1\|is_c2\|TRIGGER_N_C2\|n_c2\|g_c2" src/` пуст; `cells.rs`/`costs.rs`/`watch.rs` без гейтов G2/G3 старой модели и `WatchState`; `WatchSummary.n/g`, `ReadyFlag.n` (формат `ready.flag` изменён); `watch::DayTally { symbol, day_utc, verify_test1_violations, verify_basis_points }`; черновик `ProfileRow` и докстроки `GATE_ALPHA`/`costs.rs`/`power.rs` — по факту. Гейт G1 (`markup::run_confirmatory`) не переведён на сессионные типы — отдельная работа.
- Горячий путь: `Credentials::sign_into(&self, …, out: &mut [u8; 64])` — ноль аллокаций на реальном подписанте (тест с тестовыми значениями переменных); греп-тест запретов расширен на `feed/live.rs`, `feed/replay.rs`, `strategy.rs`, `react.rs` (+`HashMap`).
- `lob session --minutes` — 5..15 (`MIN_MINUTES`/`MAX_MINUTES`), иначе ошибка; `session.json.debug = duration_s < 3600` (`is_debug_session`). `conn.rs`: приватный трейт `Backoff` (`RealBackoff`/`RecordingBackoff`) — флаки-тест детерминирован. Тест ветки `hour_dependence_test → Ok → log_hour_test` на 7 сутках.
- `--median-lifetime-ms` у `lob markout` остался обязательным, значение не используется (не менял `--help`).
- Открыто: сквозной тест `profiles → shortlist → Confirmed` через `BacktestFillModel` не написан (нужна фикстура ≥ 100 исполнений на ≥ 7 суток с малой дисперсией; строительный блок — `backtest_fill_model_matches_known_executions_on_a_synthetic_feed`).

## Из таска 18 — динамический порог `H3` (В-30 / D05)

- `lob levels --h3-mode floor --h3-k <f64>` (`LevelsArgs.h3_k`, отдельное поле — не в общем `H3Args`): пол = `floor(k × median_trade_lots)` (та же `pick::h3_lots_floor`, что у таска 08) из `instruments.csv` на лету (`mod.rs::resolve_h3_mode_with_k`, `median_trade_lots_for_symbol`); без `--h3-k` — колонка `h3_lots`; `--h3-k` с `percentile` — ошибка. Остальные подкоманды `--h3-k` не принимают.
- `mod.rs::replay_symbol_over_configs(root, symbol, &[LevelsConfig]) -> Vec<ReplayStats>` — **один** декод бинлога, N трекеров разом; `replay_symbol` — обёртка над ней.
- `pilot::K_GRID: [f64; 5] = [2, 5, 10, 20, 50]` (предрегистрированная сетка, В-30); `k_grid_for_instrument` (один реплей на инструмент), `summarize_k_grid`, `choose_k` — наименьшее `k`, при котором медианный инструмент даёт ≥ 200 уровней за зачтённый час (G0; в `--debug` экстраполяция `rate/min × 60` с меткой `[debug]`) **и** `eaten ≥ 5 %` (G1 — `markup::G1_MIN_SHARE_NUM/_DEN`); иначе `k: не определим` — красный по построению. Печать `pilot k-grid: …` по инструментам и медиане; `percentile` — рядом.
- `stage2_preregistration_skeleton(Option<(&str, f64)>)` — заполняет `h3_mode`/`h3_k` числами при выбранном `k`; остальные поля — `<…>` до боевого пилота.
- Открыто: `--debug` не берёт готовую сессию с диска — сетка на отладке гонялась только на синтетике; боевые числа `k` — двухчасовой пилот.

## Из таска 19 — имя бинлога сессии (из слепой приёмки G4)

- `lob session` пишет `<SYMBOL>-<день UTC старта>.binlog` (`session.rs::session_binlog_path` над `record::day_file_path`); `session.json` без изменений.
- Один резолвер для всех читателей: `commands::lob::session_binlog_for(dir, symbol) -> anyhow::Result<PathBuf>` — единственный датированный файл; только недатированный `<SYMBOL>.binlog` → ошибка «запись старого формата, переименуйте в `<SYMBOL>-<дата>.binlog`»; ничего → «нет бинлога для `<SYMBOL>` в `<dir>`»; несколько датированных → перечисление. Используют `profiles.rs`, `watch.rs` (ошибка = мягкий пропуск каталога, как раньше «не этот символ»), `backtest.rs` (ошибка пробрасывается); `bybit/verify.rs` — свой префиксный поиск (слой ниже `commands`), то же сообщение на недатированный формат; `mod.rs::replay_symbol_over_configs` — то же.
- `pilot.rs`: костыль `alias_dated_binlogs_for_legacy_readers`/`stage_session_for_replay` снят; `copy_pool_instruments_csv` — только копия `instruments.csv` в каталог сессии; цепочка `--debug` работает на датированных файлах напрямую (без `replay/`).
- Тест: каталог `session` → `verify` → `levels` → `profiles` → `watch` → `backtest` без ручных шагов.
- Старые каталоги `data/session-debug/…`, `data/pilot-debug/*/session/` с `<SYMBOL>.binlog` — отвергаются с подсказкой (в `profiles`/`watch` при сканировании многих сессий — пропускаются молча, как «не этот символ»).

## Из таска 21 — окно «сейчас» (R57, из слепой приёмки)

- Окно — предрегистрированный интервал, не число: `lob::shortlist::PreregisteredWindow { start, end, exploratory, confirmatory }`, `load_or_write_window(path, &days_now, window_end)` — из файла `--preregistration` (write-once; `window_end` дописывается один раз через `--window-end`, если в файле нет конца), `window_length_days`.
- `ProfilesArgs += preregistration: Option<PathBuf>` (`--preregistration`, обязателен без `--allow-unverified` — иначе ошибка «окно не определено»), `window_end: Option<String>`. Сессии вне окна не читаются; `sessions_outside_window=<n>` печатается.
- Шапка `profiles-*.csv` и `shortlist-*.md`: `window: <start>..<end> days=<n> sessions=<n> sessions_outside_window=<n> exploratory=<d1>..<d2> confirmatory=<d3>..<d4>` | `window: debug (all sessions)` (`profiles::format_window_line`, `VerdictHeader.window`).
- Тесты: сессия вне окна не меняет ни одной строки; write-once `window_end`; шапка. `pilot.rs` — две строки умолчаний (`allow_unverified: true` → окно обходится).

## Из таска 22 — запись для пилота, части в сутках

- `lob session --pilot-minutes <16..=360>` (взаимоисключающий с `--minutes 5..=15`; ровно один обязателен; 360 = 6 ч из G0 «продление до 6 часов») — режим пилота §11; `session.json.pilot = true`, `pilot_minutes`; stderr `pilot: <n> мин`.
- Части в сутках: `session.rs::claim_symbol_binlog` через `record::claim_part` — `<SYM>-<day>.binlog`, затем `-p2`, `-p3`…; ничего не затирается; `session.json.binlog_files: Vec<BinlogPart { symbol, part, started_utc }>` накапливается при повторном открытии корня.
- `commands::lob::session_binlog_for(dir, symbol) -> anyhow::Result<Vec<PathBuf>>` — все сутки × части по порядку (сутки, затем часть; `-p2` после голого файла); читатели (`replay_symbol_over_configs`, `profiles`, `watch`, `backtest`, `verify` — свой `file_order_key`) читают части подряд как один поток (трекер продолжается, книга/реплеер сбрасываются на файл). `FillModel::prime_session`, `replay_session_binlog` принимают `&[PathBuf]`.
- `pilot::battle_counted_tail_minutes(window_minutes) = window_minutes / 2` — зачётная часть = вторая половина записи (для 2 ч — второй час §11; для 30 мин — последние 15). Константа `BATTLE_COUNTED_TAIL_MINUTES` снята.
- Живая проверка: сессия 5 мин, 8/8, 213 034 записи, 0 gaps; каталог удалён.

## Из таска 23 — сутки на уровне части, не каталога

- `commands::lob::SessionPart { path, part, day_utc, start_hour_utc }`; `session_parts_for(dir, symbol) -> anyhow::Result<Vec<SessionPart>>` — те же файлы и порядок, что `session_binlog_for`, но сутки — из имени файла части (`<SYM>-<day>[-pN].binlog`), час — из `session.json.binlog_files` по тройке (symbol, part, сутки `started_utc`), запасной путь — `start_hour_utc` каталога (запись до таска 22). Без `session.json` — ошибка (не сессия), читатели многих каталогов пропускают молча.
- `group_parts_by_day(Vec<SessionPart>) -> Vec<(day, Vec<SessionPart>)>` — единица реплея `profiles`/`watch`: трекер уровней общий на части одних суток (таск 22), чистый на каждые сутки. `profiles::replay_one_session_day` / `watch::replay_session_day` возвращают записи **по частям** — `accumulate_level` и `SessionTally` берут час старта от своей части; `FillModel::prime_session` получает пути и записи суток.
- `session_days_in_dir(dir) -> BTreeSet<day>` — сутки датированных частей любого символа (без `session.json` — пусто); на нём `profiles::distinct_session_days` (предрегистрация окна), счётчики `sessions=`/`sessions_outside_window=` (по парам каталог×сутки) и `shortlist::group_by_day` (каталог стоит под каждыми своими сутками, во времянку линкуется один раз — остальные сутки отсеет окно `profiles` внутри времянки).
- `watch`: `SessionTally.session_id = "<каталог>/<имя части без .binlog>"` — каталог с N частями даёт N различимых сессий; `lob::watch::day_format_ok` стал `pub`.
- Тесты: `session_parts_for_attributes_day_and_hour_per_part_not_per_directory`, `two_day_session_dir_yields_g_two_and_hours_per_part` (G = 2, часы `2,14`), `now_window_boundary_inside_one_directory_reads_only_the_days_in_window` (окно с границей внутри каталога — таблица как у однодневного каталога, `sessions=1 sessions_outside_window=1`), `watch_attributes_days_and_hours_per_part_inside_one_directory`, `group_by_day_lists_a_two_day_directory_under_both_days_and_links_it_once`.

## Из таска 24 — коллектор: горячий путь записи

- `bybit::ws::parse_message_into(raw: &str, out: &mut Vec<Event>) -> Result<(), ParseError>` — горячий вход разбора без `serde_json::Value` (serde-визиторы, буфер вызывающего); `parse_message` — обёртка над ним для тестов/legacy. `conn::Connection::handle_raw(events: &mut Vec<Event>, …)` (приватный, drain, буфер живёт на соединение).
- `lob session`: кадры бинлога копятся до `record::FRAME_TARGET_RECORDS = 1000` записей (как `lob record`), `File` за `BufWriter`, сброс по часовому таймеру `HOURLY_REFRESH_SECS` и на выходе; формат на диске прежний, `Reader` читает без правок. Вторая `Book` в `session.rs` снята (`conn::handle_raw` не пересылает ничего, что не прошло `apply`).
- `session.json.parse_p99_ns`/`queue_p99_ns` — из гистограммы фиксированной ёмкости с целочисленными бинами (погрешность ≤ 1.5625 % значения), не из `Vec` всех замеров — RSS плоский.
- Бенч: `tests/collector_bench.rs` — `cargo test --release --test collector_bench -- --ignored --nocapture` (медиана/p99 нс и аллокаций на сообщение по фикстурам снапшот/дельта/лента).
- Замер «до → после» — `docs/findings/collector-2026-09-12.md`: разбор p99 бенч снапшот 116 → 49 мкс; аллокаций/сообщение 328 → 8 (снапшот), 38 → 2 (дельта), 93 → 0 (лента); живой пул 8×5 мин: `parse_p99_ns` 249 → 104 мкс (бюджет 200 — впервые в бюджете), `queue_p99_ns` 670 → 299, RSS плоский, CPU 2.9 → 1.7 %, байт/запись 15.6 → 7.3 (−53 %).
- Остаток аллокаций (1–2 на книжное сообщение) — `Vec` внутри `book::Update` и владеющая пересылка `ConnEvent`; ноль — только фиксированной ёмкостью 50+50 в `book::Update` (вне зоны таска 24).

## Из таска 25 — олвейс-он коллектор

- CLI `lob session --always-on` (conflicts_with `--minutes`/`--pilot-minutes`); `session::SessionPlan { Timed { minutes, pilot_minutes }, AlwaysOn }`.
- `session.json` += `always_on`, `reconnects`, `resyncs`, `frames_failed`, `bytes_written`, `updated_utc`, `closed`, `samples: [{ts_utc, rss_bytes, cpu_pct}]`; `binlog_files` дописывается на ротации по суткам UTC; файл переписывается на старте / раз в час / на ротации / в финале через tmp+rename. `gaps.csv` kind += `write_failed`.
- `feed::Event::Tick { local_ts_ns }`; `feed::Event::Gap` += `kind: feed::GapKind { ParseFailed, SequenceGap, BookInvariant, Disconnected }`; `LiveFeed::spawn_with_ticks(pool, tick)`, `LiveFeed::stop_handle() -> StopHandle { stop(), stop_on_ctrl_c() }`.
- `record::claim_part_with<W: Write>(…, wrap: FnOnce(File) -> W)` — одна политика «первая свободная часть» для `record` и `session`; `record::FRAME_LOSS_WINDOW_SECS = 10` (окно потери, doc `FRAME_TARGET_RECORDS`); `record::ZSTD_LEVEL = 1` (замер уровней 1/3/6/9 на реальной записи: 1 → 7.245 Б/запись при 170 нс/запись доминирует 3; 6/9 — −5…6 % байт за 2.8–4.8× CPU); `record::GapKind::WriteFailed`.
- `binlog::Writer::get_ref()`, `binlog::max_frame_bytes_on_disk(records)`; `ws::ParseError::BadShape(&'static str)` (валидный JSON неверной формы — не `NotJson`).
- Ротация суток пишет синтетический снапшот из `Book` инструмента (книга вернулась в `session.rs` ради этого; гейт 10⁶ аллокаций зелёный).
- `session::resource_sample_period(elapsed_s) -> Duration` — 30 с первый час, затем `HOURLY_REFRESH_SECS`; `session.json.samples` — потолок 120 + 24/сутки, после первого часа `cpu_pct` — среднее за час.
- Живой прогон `--always-on` 300 с (`docs/findings/collector-2026-09-12.md`, «Олвейс-он»): RSS 14.0 → 14.9 МиБ (плоский после прогрева), CPU 1.71 %, 30.9 КБ/мин/инструмент, байт/запись 7.272 vs 7.30 (−0.4 %, регрессии D-ДИСК нет), parse p99 36.9 мкс, queue p99 299 мкс, gaps/reconnects/resyncs/frames_failed 0; Ctrl+C настоящим `CTRL_C_EVENT` → код 0 за 0.9 с; `verify`/`levels`/`markout` на живом каталоге на 115-й секунде читают всё, кроме последних ≤ 10 с.
- Ремонт по ревью: `session::SinkFile { truncate_to(len) }` (pub(crate)), `FrameSink::over(Box<dyn SinkFile>)`, `FrameSink::boundary_lost()` — на ошибке сброса откат `set_len`+`seek`, `buf.clear()`, при неудаче отката — следующая часть; `run_session_loop(&mut dyn Feed, &mut SessionCtx) -> anyhow::Result<SessionSummary>` — финализация (`flush_all` + `session.json closed=true`) внутри шва на любом выходе, периодические ошибки `session.json`/ротации → stderr + `gaps.csv write_failed`, цикл живёт; замер часов — после финализации; ротация только вперёд (опоздавшие события — в текущую часть), повтор неудачной ротации через `FRAME_LOSS_WINDOW_SECS`.

## Из таска 26 — маркер сверки из `lob verify`

- `commands::lob::verify::verify_and_mark(root, marker_dir, symbol) -> anyhow::Result<VerifyReport { parts: Vec<PartVerify { path, day_utc, part, summary }>, total, status: VerifyStatus { Ok, Fail }, marker }>` — единственная запись `verify-<SYMBOL>.status` (`ok`/`fail`); `pilot::process_instrument` зовёт её. Сверка по всем частям символа (`session_binlog_for`), вердикт по сумме; одна битая часть → `fail`.
- `bybit::verify::verify_file(&Path) -> anyhow::Result<VerifySummary>` — аддитивно, обёртка над `verify_one_file` (`files = 1`); `run_verify` не тронут (остался в тестах).
- stdout `lob verify`: строка на часть `verify: part=<файл> day=<YYYY-MM-DD> part_no=<n> …`, затем прежняя сводка `verify: files=…` байт-в-байт, затем `verify: status=ok|fail marker=<путь>`.
- `verify.csv` — сайдкар записи (`bybit::verify_sidecar`), не команда; `CLAUDE.md` таблица приведена к факту.

## Из таска 27 — пул по задаче (В-35)

- `lob pick`: пул = три исключения §2 → ранг по обороту → ровно десять; глубина не отсеивает. `pick::depth::DepthCheck { Ok, BelowFloor, NotMeasured }`, `depth_check(Option<&MeasuredCandidate>)`; колонка `depth_check` в `instruments.csv` (последняя) и `candidates.csv` (вместо `above_depth_floor`); `excluded_reason ∈ {btc_eth_by_name, non_crypto_base, listed_under_30d, rank_beyond_pool}`. `survivors_above_depth_floor`, `PickError` удалены. Читатели `instruments.csv` — по именам колонок, хвостовая колонка безвредна.
- Боевой пул (`lob pick --window-secs 3600 --h3-k 1.0`, 2026-09-12 13:22:59Z): SOL, ZEC, XRP, HYPE, NEAR, STORJ, DOGE, ENA, LSK, SUI; `below_floor` у ZEC/STORJ/LSK.

## Из таска 28 — коллектор на 761 инструменте

- `lob session --all-instruments` (conflicts_with `--pool-instruments`; список — `rest::fetch_all_linear_instruments`, без исключений §2). `SessionArgs.pool_instruments: Option<PathBuf>`, `all_instruments: bool`.
- `conn::{SymbolSpec, PoolConnConfig, MAX_ARGS_CHARS = 21_000, MAX_CONNECTIONS_PER_5MIN = 500, ORDERBOOK_DEPTH}` (pub(crate)); `Connection::new(connector, impl Into<PoolConnConfig>)`; книга и `resyncing` — на инструмент; маршрут по символу топика — двоичный поиск. `ConnSink::send_event(&self, symbol_idx: u16, ev)`; `ConnEvent::Disconnected { first_of_socket }` — веер на каждый инструмент сокета; `ConnEvent::Unrouted`.
- `feed::live::plan_connections(pool) -> Result<Vec<Vec<u16>>, LayoutError { EmptyPool, PoolTooLarge }>` — жадная набивка сокета до `MAX_ARGS_CHARS`; поток ввода-вывода на сокет, один поток решений, один `mpsc` (A3, D04); `LiveFeed::spawn* -> Result`. `feed::Event.symbol: u16`; `GapKind += DisconnectedSameSocket, Unrouted`; `session.json += unrouted`; `session_json_dirty` — одна запись после ротации всех инструментов.
- `ws::parse_message_into -> Result<Option<&str>, ParseError>` (символ топика), `ws::sub_pool`, `ws::pool_args_chars` (арифметика); `sub_trades` снят.
- Замер (`collector-2026-09-12.md`, «761»): 8 → 761: CPU 2.0 → 24.2 % (0.253 → 0.032 %/инстр.), RSS 17.8 → 159.5 МиБ (215 КиБ/инстр.), 6.9 КБ/мин/инстр., байт/запись 6.38, parse p99 87 мкс, queue p99 27.8 мс (долг живого бота), 7.75 ГБ/сутки.

## Из таска 29 — PBO и CPCV

- `commands::lob::shortlist`: матрица «профиль сетки × сутки окна», ячейка `net_bps`, `NaN` при `n = 0`; строки с `NaN` снимаются до расчёта (`excluded_nan_rows`). PBO — `final_metrics::pbo` с `REPORT_PBO_PARTITIONS = 8` (≥ 8 суток). CPCV — `final_metrics::cpcv_selection_oos_sharpe(trials, REPORT_CPCV_PARAMS { folds: 2, purge: 0, embargo: 0 }, select)`: отбор заново на IS-сутках каждой складки (лучший по среднему `net`), Sharpe по OOS выбранного.
- Шапка `shortlist-*.md`: `pbo=<число|n/a (причина)> cpcv_oos_sharpe=<…> (selection: …)`, `pbo_gate: none` (порога в задаче/плане нет — число без вердикта), `pbo_matrix: rows= days= excluded_nan_rows= cell=net_bps`. `VerdictHeader += pbo_na, cpcv_na, cpcv_selection, pbo_matrix`. Цена: `run_profiles_over` на каждые сутки окна.

## Из таска 30 — ось часа под олвейс-он (В-36)

- `profiles-*.csv` += `level_hours_utc` (час UTC рождения уровня, `birth_ms`) сразу за `session_start_hours_utc` (та остаётся метаданными записи); `observed_sharpe` — последняя. Тест на час — наблюдения (сутки, час рождения), кластер — сутки. `ProfileAgg.level_hours`, `hour_day_sums: BTreeMap<(i64,u32),(u32,f64)>`.

## Из таска 32 — дашборд на localhost (R87, В-38) — ревью не проведено (лимит сессии)

- `lob dashboard --root <каталог> --out <dir> [--h3-mode floor] [--watch <с>]` → `index.html` (самодостаточный, без внешних URL) + `data.json`; `tools/serve_dashboard.py <out> [--port N]` — `http.server` на 127.0.0.1. Полный реплей бинлогов на каждый пересчёт (~3.5 мин на 8 × 2.3 ч).
- `data.json`: `collector{alive, checks[{label,value,threshold,source,verdict}], …}`, `checkpoints[{minutes: 30|60|720, reached, levels_per_hour_median, eaten_share_median, m10s_*_bps, net_bps, conclusion, caveat}]`, `instruments[]`, `glossary[]`, `where_we_are{gates, pilot, audit, next}` — статический текст в `dashboard.rs`, обновлять таском, меняющим состояние. RTT — `assumed_rtt_ms = 20` (В-37).

## Из таска 34 — пул на ходу (R89, В-40)

- `feed::DynamicPool { fn add(&mut self, members: Vec<live::PoolMember>) -> Result<Vec<u16>, live::LayoutError> }` — отдельный трейт, `Feed` не менялся; индексы новых инструментов **продолжают** нумерацию пула (первый добавленный = прежняя длина). `LayoutError += StaticSource` — реплей (`ReplayFeed`) расширяться не умеет и отвечает им, не молчаливым `Ok`.
- `feed::live::LiveFeed`: фабрика соединения сохранена со старта (`ShardSpawner = Box<dyn FnMut(Vec<SymbolSpec>, &PoolMember) -> JoinHandle<()> + Send>` — тот же коннектор, те же часы, тот же общий канал); `shard_specs(members, first_index)` — одна раскладка (`plan_connections`, предел `MAX_ARGS_CHARS`) и для старта, и для партии; `spawn_io_thread` — один код ОС-потока с рантаймом и `Connection`. `add` открывает новые соединения для партии, живые сокеты не трогает, `pool_len` растёт; `StopHandle::stop()` заканчивает поток и после добавления (общий канал). Бремя вызывающего: `F: FnMut(&PoolMember) -> C + Send + 'static` у `spawn_with*` (тесты — `move`-замыкания).
- `lob session`: `run_session_loop<F: Feed + DynamicPool + ?Sized>`; `SessionCtx { pool_path: <root>/instruments.csv, pool_mtime }`, `SessionCtx::check_pool_file(feed, ts_ns)` на каждом `Event::Tick` (после `on_tick`, период `FRAME_LOSS_WINDOW_SECS`): один `metadata()`; смена `mtime` → `pool::load_pool` целиком → символы, которых нет в `states` (и без дублей в партии) → `open_symbol_state` (сутки — по `ts_ns` тика, часть — `claim_symbol_binlog`) → `feed.add` одной партией → `assert_eq!(idx, states.len())` на каждом → `binlog_files`, stderr `session: добавлен SYMBOL (tick=…, step=…) — файл …`, `write_session_json_or_log` сразу. Отказ `load_pool` (в т. ч. одна битая строка) / открытия файла — stderr, состав прежний, повтор на следующем `mtime`; отказ `feed.add` — открытые файлы частей удалены. Удаление символов не реализовано (строку убрали — запись идёт). Горячий путь не тронут: `write_market_event`/`flush_symbol_batch`/`conn.rs`/`ws.rs`/`record.rs` без правок; тест `events_of_an_added_symbol_allocate_nothing_after_warmup` — ноль аллокаций на событие добавленного.
- Тесты: `feed/live/tests.rs::add_opens_a_new_connection_with_the_next_index_and_stop_still_ends_the_feed`, `add_beyond_u16_is_a_layout_error_before_any_connection_is_opened`; `session/tests.rs::a_row_appended_to_instruments_csv_between_ticks_joins_the_recording_once` (файл с заголовком из строки, `instruments`/`binlog_files` +1, кадр индекса 1 — в файл нового, повтор строки — не дубликат, битая строка — только stderr), `a_static_feed_refuses_the_batch_and_the_recording_stays_as_it_was`; `GrowingFeed` — сценарный `Feed + DynamicPool`, `ScriptedFeed` отвечает `StaticSource`.
- Живая проверка: `lob session --minutes 5` на SOL/XRP в `data/session-debug/t34`, ZEC дописан на 52-й секунде (20:02:25Z), подхвачен на следующем тике через 10 с (`session: добавлен ZECUSDT (tick=10000000, step=10000000) — файл …\ZECUSDT-2026-09-12.binlog`, `binlog_files` started 20:02:34Z); три бинлога (SOL 171 КБ, XRP 226 КБ, ZEC 320 КБ), `instruments` = 3, 113 335 записей, gaps/reconnects 0, parse p99 141 мкс, CPU 1.0 %; `lob verify ZECUSDT` — `status=ok` (8130 обновлений, 2363 сделки).
