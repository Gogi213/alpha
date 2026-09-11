СДЕЛАНО: Пункт 4 (горячий путь) — закрыт целиком: `bybit::sign::Credentials::
sign_into` — буферный HMAC (несколько `update`, без склейки payload в
`String`), hex прямо в `[u8; 64]`; ноль аллокаций доказано тестом на
настоящем `Credentials::from_env()` с тестовыми переменными окружения под
`ENV_LOCK` (не на фейке); `impl OrderSigner for Credentials::sign_into`
делегирует ему, не `sign()`. Греп-тест горячего пути добавлен в
`feed/live.rs`/`feed/replay.rs` (ноль случаев — банится широко:
`Instant::now`/`SystemTime::now`/`f64`/`HashMap`); `strategy.rs`/`react.rs`
расширены баном `HashMap<`/`HashMap::` (не голым словом — там легитимно
живёт `HashMapMarketDepth` крейта `hftbacktest` в тестах); `f64` там не
банится — обязателен интерфейсом `MarketDepth`, не изобретённое число.
Пункт 3 (снос C1/C2) — закрыт целиком: `grep -rn "is_c1|is_c2|
TRIGGER_N_C2|n_c2|g_c2" src/` пуст. `lob/cells.rs` ужат до одного
переиспользуемого шва (`joint_two_series_bootstrap`/`disjoint_contrast`/
`joint_product_interval` — весь гейт G2/`is_c1`/`is_c2` снесён, у
`disjoint_contrast` сейчас нет вызывающего в крейте, оставлен `pub` по
прямому указанию `interfaces.md`, дан свой тест на синтетике).
`costs.rs` — снесены `VerdictCell`/`G3Verdict`/`select_verdict_cell*`/
`decide_g3*` (дублировали ныне `backtest::G4Verdict`/`decide_g4`, нет
вызывающих). `lob/watch.rs` — снесены `WatchState`/`ObserveOutcome`/
`ProgressRow`+`progress.csv`(старый)/`gap_day_share_ppm` (нулевые
вызывающие вне файла — таск 07 уже заменил их `WatchSample`/сессионным
`progress-*.csv`); `is_c1`/`is_c2`/`TRIGGER_N_C2`/`TRIGGER_G`/
`C2_MIN_REPEAT` снесены; `DayTally` ужат до `{symbol, day_utc,
verify_test1_violations, verify_basis_points}` (n_c1/n_c2/has_gap_over_6h
не читал никто живой); `tally_day` — 4 параметра вместо 7;
`ReadyFlag.n_c2` → `n` (и его текстовый формат `ready.flag`, тот же приём
у `WatchError` — снесены неиспользуемые варианты `DuplicateDay`/
`SubsetViolation`). `commands/lob/mod.rs::day_tallies` — снял чтение
`gaps.csv` целиком (никто не читал `has_gap_over_6h` дальше). `commands/
lob/markout.rs` — `--median-lifetime-ms` остался обязательным флагом той
же CLI-формы (не менял `--help`), но значение больше никуда не идёт
(`_median`, с комментарием почему). `WatchSummary.n_c2/g_c2` → `n/g`
(задело `mod.rs::dispatch`, печать `watch:` теперь `n=... g=...`).
`shortlist::ProfileRow`/`PROFILES_HEADER`/`write_profiles_csv`/
`read_profiles_csv` — снесены (нулевые читатели после таска 10/11,
проверено грепом). Докстрока `stats::GATE_ALPHA` переписана по факту
(больше не привязана к отменённому G2, названы реальные потребители).
`costs.rs:1`/`:28` докстрока переписана без C1/C2/G3. `power.rs:126` —
проверил: тест `power_reads_trial_count_from_instrument_pool_not_a_constant`
и `shortlist::nominal_grid_is_29_plus_150_for_ten_instruments` уже
согласованы с текущей формулой (179 = 29+150 после таска 06) — докстрока
не стала, менять нечего.
Пункт 5 — НЕ начат (все пять подпунктов: `--minutes` 5..15,
`session.json.debug`, флаки `conn.rs`, фикстура ≥7 суток
`hour_dependence_test`, сквозной тест `profiles → shortlist`).

ФАЙЛЫ: изменены `src/bybit/sign.rs` (+`sign_into`+3 теста), `src/bybit/
trade_ws.rs` (`impl OrderSigner for Credentials::sign_into`, докстрока),
`src/feed/live.rs`/`src/feed/replay.rs` (+греп-тест), `src/lob/strategy.rs`/
`src/commands/lob/react.rs` (греп-тест расширен), `src/lob/cells.rs`
(переписан целиком, ужат ~1760→~400 строк), `src/lob/costs.rs` (снесён
G3-блок, докстрока), `src/lob/watch.rs` (переписан целиком: снесена старая
C1/C2-машинерия, `DayTally`/`ReadyFlag` ужаты/переименованы, сессионная
часть — таск 07 — не тронута), `src/lob/markup.rs` (фикстуры тестов под
слимнутый `DayTally`/`ReadyFlag.n`), `src/lob/shortlist.rs` (снесён
`ProfileRow`-блок), `src/commands/lob/mod.rs` (`day_tallies` без
`gaps.csv`, докстрока, печать `watch:`), `src/commands/lob/markout.rs`
(вызов `day_tallies`, `_median`, тесты `ReadyFlag.n`), `src/commands/lob/
watch.rs` (`WatchSummary.n/g`, тесты), `src/stats/mod.rs` (докстрока
`GATE_ALPHA`). Дерево зелёное на каждом шаге: `cargo test --release` →
554 passed, 0 failed, 5 ignored (было 572 на входе — минус тесты дохлого
кода, не минус покрытие живого); `cargo clippy --all-targets -- -D
warnings` → чисто; `cargo fmt --check` → чисто.

РЕШЕНИЯ:
- `disjoint_contrast`/`DisjointContrast` оставлены `pub fn`/`pub struct`
  (не `pub(crate)`) сознательно: без этого у функции нулевой вызывающий
  в крейте и `dead_code` уронил бы `clippy -D warnings` в non-test сборке
  библиотеки — `pub` в `pub mod cells` считается частью API крейта и
  исключён из анализа мёртвого кода в бинарном/либ-крейте, что и
  проверено эмпирически (`cargo clippy` чист). `DisjointContrast`
  переименован с `n_c2`/`mean_c2` на `n_a`/`mean_a` (как у `JointProduct`)
  — того требовал буквальный греп-критерий, заодно честнее: это больше
  не ячейка C2, а безымянный ряд A обобщённого шва.
- Полная миграция гейта G1 (`markup::run_confirmatory`, живой,
  используется `commands/lob/markout.rs --confirmatory`) на сессионные
  типы (`SessionTally`/`SessionDay`) НЕ сделана и не начата: G1 нужны
  сырые `LevelRecord`/`MidSample` на сутки, а `SessionTally` несёт только
  агрегаты (`n`, `verified`) — переезд потребовал бы придумать новый тип
  «сессионные сутки с сырыми записями», которого нигде в спеке/
  interfaces.md нет, это архитектурное решение по объёму больше одного
  пункта долга (сам код внутри `lob/watch.rs` прямым текстом называл это
  «отменённым дизайном, снос — таск 14» — но per-day записи `verify.csv`/
  `ready.flag`, которые G1 читает, никто явно не отменял, только счёт по
  ячейкам C1/C2 внутри них). Вместо переезда — вычищена только
  C1/C2-специфика (`is_c1`/`is_c2`/триггер/мёртвый `WatchState`), сама
  G1-цепочка (`day_eligible`/`ready.flag`/verify-доля) оставлена как есть,
  тем же поведением, тем же составом дней на входе.
- `ready.flag` текстовый формат меняет ключ `n_c2` → `n` (единственное
  расхождение с «CSV/JSON не меняет колонок» — это не CSV/JSON, а
  собственный key=value формат, и буквальный критерий грепа не оставлял
  выбора: поле у структуры и его сериализация — один код). Внешних
  читателей формата, кроме этого же крейта, не существует (сборки данных
  gitignored, флаг сегодня руками не пишется — только тестами/будущим
  инструментом).
- `--median-lifetime-ms` в `lob markout` остался обязательным флагом
  (не убирал — это видимое изменение `--help`/контракта команды, что
  ticket прямо запрещает), но его значение висит непрочитанным
  (`_median`) — задокументировано на месте.
- Артефактный критерий (`pilot-debug-summary.csv` те же числа) не
  перепроверялся живым/реплей-прогоном — той же причине, что у
  предшественника (handoff-17-1.md): `run_pilot_debug` всегда зовёт
  `run_session` (сеть), заменяемого «взять уже записанное» флага нет.
  Проверено статически: `pilot.rs` не импортировал ничего из снесённого
  (`is_c1`/`is_c2`/`DayTally`/`WatchState`/`ProfileRow`), а
  `costs::net_fill_interval`/`cells::joint_product_interval` (единственное,
  что `pilot.rs` реально использует из тронутых модулей) — те же формулы,
  тот же `JointProduct` с теми же именами полей, ни один байт логики
  вычисления не менялся.

ТУПИКИ: пробовал банить голое `f64`/`HashMap` в `strategy.rs`/`react.rs`
по образцу `levels.rs` — отказался: `f64` там обязателен интерфейсом
`hftbacktest::MarketDepth` (лучшая цена — не изобретённое число, а
контракт внешнего крейта, A9), а голый `HashMap` ловил бы легитимный
`HashMapMarketDepth` в тестовом модуле; заменил на `HashMap<`/`HashMap::`
— не матчит имя типа крейта, матчит настоящее использование
`std::collections::HashMap`.

ДАЛЬШЕ: пункт 5, все пять подпунктов с нуля. `lob session --minutes`
валидация 5..15 — `commands/lob/session.rs`, `clap` уже даёт `PilotArgs`-
подобный `#[arg(long)]`, нужен `value_parser`/ручная проверка с понятной
ошибкой вне диапазона. `session.json.debug` — `SessionSummary`/структура
JSON в `commands/lob/session.rs`, поле `debug: bool` при `duration_s <
3600`, круговой тест на запись/чтение. Флаки `bybit::conn.rs::
connect_then_close_without_forwarding_a_message_does_not_reset_backoff` —
искать реальный `Instant`/`sleep` в проверяемом пути, завести
фейковые часы/детерминированный планировщик (шов уже должен быть —
`Clock`, A2). Фикстура ≥7 суток для `profiles.rs::hour_dependence_test →
Ok → log_hour_test` — тест сейчас не доходит до ветки `Ok` на короткой
фикстуре (нужно ≥7 кластеров-суток для `stats::G_MIN`). Сквозной тест
`profiles → shortlist` через `BacktestFillModel` до `Confirmed` —
синтетика на шве 6 (`Bot<MD>` на `Backtest`), первый тест перед
реализацией, шов ещё не написан.
