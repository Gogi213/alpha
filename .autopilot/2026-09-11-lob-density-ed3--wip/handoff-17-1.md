СДЕЛАНО: Пункт 1 (сдвоенный код) — закрыт целиком: один `trade_hit_from_record`
(`commands/lob/mod.rs`, дети `watch.rs`/`profiles.rs` зовут через `super::`),
один `is_trade_ev` (стал `pub(crate)` в `bybit/verify.rs`, его зовут
`commands/lob/mod.rs` и `feed/replay.rs` — оба уже зависели от `bybit::verify`,
новых рёбер зависимости нет), `best_prices` уже был в одном экземпляре
(`lob/strategy.rs`) — снял только устаревшую докстроку, утверждавшую про
несуществующий приватный близнец в `backtest.rs`. `read_clock_bybit_rtts`
(`pilot.rs`) переписан фильтром над `bybit::clock::read_rows`. `resolve_h3_mode`/
`h3_lots_for_symbol`/`H3ModeArg`/`H3FloorRow` переехали из `levels.rs` в
`commands/lob/mod.rs`.
Пункт 2 — `H3Args` закрыт целиком: общая структура во `flatten` в
levels/markout/watch/profiles/shortlist, `--help` не изменился по смыслу
(проверено вручную), плюс новая ошибка «`--h3-lots` с `--h3-mode floor`» и
тест на неё (`resolve_h3_mode_rejects_h3_lots_with_floor`, `mod.rs`).
`ExecutionArgs` закрыт для profiles/shortlist (flatten, `Option<i64>`×3,
поведение то же); **backtest.rs сознательно не флаттенит** — см. РЕШЕНИЯ.
Пункты 3, 4, 5 — не начаты.
Дерево зелёное на каждом шаге: `cargo test --release` → 572 passed, 0 failed,
5 ignored (было 571 на входе); `cargo clippy --all-targets -- -D warnings` →
чисто; `cargo fmt --check` → чисто.

ФАЙЛЫ: изменены `src/commands/lob/mod.rs` (новые `H3Args`/`ExecutionArgs`/
`resolve_h3_mode` со сновой проверкой/`h3_lots_for_symbol`/`H3ModeArg`/
`H3FloorRow`/`trade_hit_from_record`, +1 тест), `levels.rs`, `markout.rs`,
`watch.rs`, `profiles.rs`, `shortlist.rs`, `pilot.rs` (флаттен H3Args везде,
ExecutionArgs в profiles/shortlist, литералы структур в тестах и в `pilot.rs`
переведены на вложенные поля), `feed/replay.rs` (зовёт `bybit::verify::
is_trade_ev`, докстрока поправлена), `bybit/verify.rs` (`is_trade_ev` →
`pub(crate)`), `lob/strategy.rs` (докстрока `best_prices`). Пункты 3–5 файлов
не трогали: `lob/watch.rs` (C1/C2), `bybit/sign.rs`, `feed/live.rs`,
`feed/replay.rs` (греп-тест горячего пути), `strategy.rs`/`react.rs`
(греп-тест), `commands/lob/session.rs` (валидация `--minutes`), `bybit/
conn.rs` (флаки теста), `lob/profiles.rs` (фикстура ≥7 суток для
hour_dependence_test).

РЕШЕНИЯ:
- `is_trade_ev` — общий дом `bybit::verify`, не `binlog` и не
  `commands/lob/mod.rs`, как предлагал таск буквально: докстрока `binlog::
  Record` прямо говорит «модуль не толкует биты `ev` — это дело пишущего»,
  а `commands/lob` → `bybit` было бы разворотом слоёв (bybit ниже commands).
  И `commands::lob::mod`, и `feed::replay` уже зависели от `bybit::verify`
  (`FileReplayer`) — новых рёбер зависимости не добавил.
- `ExecutionArgs` НЕ флаттенится в `backtest.rs`, хотя таск называет его в
  списке трёх файлов. Реальный контракт CLI разный: у `profiles`/`shortlist`
  тройка `Option<i64>`, опциональна, все три или ни одного
  (`resolve_fill_model`); у `backtest` та же тройка — голый `i64`,
  обязательные флаги, это видно в `Usage:` (`--median-rtt-ns <..>` без
  скобок). Общая структура с `Option<i64>` убрала бы это требование из
  `Usage` строки backtest — заметная перемена `--help` по смыслу, которую
  критерий приёмки прямо запрещает; общая структура с обязательными полями
  сломала бы NoFillModel-путь profiles/shortlist (сегодня оба живут без
  этих флагов). Оставил как есть — три поля объявлены прямо в
  `BacktestArgs`, без изменений. Докстрока `ExecutionArgs` в `mod.rs`
  объясняет это решение на месте, чтобы следующий не полез переделывать.
- `pilot.rs` не получил `H3Args`/`ExecutionArgs`-флаттен: его `PilotArgs`
  не имеет полей `h3_mode`/`h3_lots` вообще (гоняет обе H3-ветки внутри
  одного прогона) и своей пары `median_rtt_ns`/`p95_rtt_ns` без
  `order_qty_e9` (лот считает `compute_order_qty_e9` из `instruments.csv`) —
  не та форма, что у трёхполейной `ExecutionArgs`.
- Артефактный критерий («те же числа `pilot-debug-summary.csv`») не
  перепроверял живым прогоном: таск и `CLAUDE.md` не велят живые прогоны
  ради отладки, `run_pilot_debug` всегда зовёт `run_session` (сеть) — нет
  флага «взять уже записанное». Не тронул ни одной формулы/числа — только
  расположение функций и форму CLI-структур; `process_instrument`/
  `run_profiles`/`run_levels` и т.п. остались побайтово теми же телами
  функций, и фикстурные тесты на них (в т.ч. `process_instrument_reports_
  rates_shares_and_verify_marker`) зелёные без изменений.

ТУПИКИ: пробовал сделать `resolve_h3_mode` принимающей `&H3Args` вместо
`(mode, h3_lots)` — отказался: `pilot.rs` строит `H3ModeArg::Floor`/`None` не
из распарсенного `H3Args`, а как есть; сигнатура-как-было проще и не требует
создавать временные `H3Args` там, где их не было.

ДАЛЬШЕ: пункт 3 (снос C1/C2) — начинать с `grep -rn "is_c1\|is_c2\|TRIGGER_N_C2\|
n_c2\|g_c2" src/`, интерфейсы.md «Из таска 07» называет живых потребителей
(`cells.rs`/`pilot.rs`/`markup.rs`/`mod.rs::day_tallies`) — для каждого
проверить, сам ли потребитель отменённый (тогда сносить вместе) или его
нужно перевести на сессионные аналоги таска 07 (`SessionTally`/
`session_day_eligible`). `WatchSummary.n_c2/g_c2` → `n/g` — задевает
диспетчер печати в `mod.rs::dispatch`. `shortlist::ProfileRow`/
`write_profiles_csv`/`read_profiles_csv` — проверить греп-тестом, что после
таска 11 их не читают, прежде чем удалять.
