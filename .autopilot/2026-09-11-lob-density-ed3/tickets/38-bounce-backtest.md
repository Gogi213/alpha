# 38 — Бэктест сделки-отскока: net_fill по касаниям

**Требования:** R90, «делаем идею» (2026-09-13); В-44 (сделка-отскок), В-37 (RTT assumed), задача
§«Зачем бэктест» (markout отбирает, бэктест выносит вердикт), A6 (одна стратегия `on_event<MD, B>`)
**Blocked by:** 37
**Зона:** `src/lob/strategy.rs` (план сделки как данные, одна функция `on_event`), `src/lob/backtest.rs`
(сигналы касаний, стоп/тейк/тайм-аут), `src/commands/lob/backtest.rs` (флаг `--touches`, выход
`docs/findings/bounce-backtest-<дата>.csv` + `-pnl.csv`), тесты, `CLAUDE.md`, `docs/COMMANDS.md`.
**Не трогать:** `levels.rs`, `session.rs`, `feed/`, `bybit/`; сетку смертей и её бэктест —
поведение без изменений (тест шва на настоящем `hftbacktest::backtest::Backtest` остаётся зелёным).
**Волна:** 12
**Status:** pending

## Что должно заработать

Сделка-отскок В-44 как **план сделки — данные, не вторая стратегия**: `on_event<MD, B>` остаётся
одной функцией, `StrategyState` получает `TradePlan { entry: Limit(price), stop: Market при сделке
на price, take: Limit(price), deadline_ns }`; нынешняя сделка по смерти уровня — тот же тип плана
(вход по лучшей, выход по горизонту) — доказать тестом, что старый бэктест даёт те же числа.

Сигнал касания: `t0_ns = start_ms`, сторона — по уровню (бид-уровень → покупка). План: вход лимит
`P + 1` тик (аск: `P − 1`), стоп — рыночный выход при сделке на `P − 1` тик (аск: `P + 1`), тейк —
лимит `вход + (вход − стоп)` (R 1:1), дедлайн `HORIZONS_MS[3]` (60 с) → рыночный выход; вход,
не исполненный к концу касания (`end_ms`) — снимается, `MissReason::EntryTimeout`; позиция занята —
`PositionBusy`. RTT — `--median-rtt-ns`/`--p95-rtt-ns` (В-37, шапка `rtt=assumed(20ms, В-37)`),
модель очереди — та же `RiskAdverseQueueModel`, лот — `--order-qty-e9`. Издержки — `costs`:
мейкер на входе и тейке, тейкер на стопе и тайм-ауте (`MAKER_FEE_BPS`/`TAKER_FEE_BPS`).

Выход: `bounce-backtest-<дата>.csv` — строка на профиль касания (те же `profile_id`, что T37):
`n_signals`, `n_filled`, `fill`, `miss_entry_timeout`, `miss_position_busy`, `n_stop`, `n_take`,
`n_timeout`, `net` (среднее, bps), `net_fill`, интервал `net_fill` (`costs::net_fill_interval`,
кластер — сутки), кривые PnL median/p95 RTT в `-pnl.csv`. Вердикт — как у смертей: нижняя
граница `net_fill > 0` на отложенной выборке; здесь печатается только таблица, отбор/шортлист
касаний — следующий таск после результата.

## Критерии приёмки

- [ ] тест: старый бэктест (сигналы смертей) через `TradePlan` даёт побайтно те же
      `bounce`-независимые числа, что до таска (фикстура из `lob/backtest/tests.rs`)
- [ ] тест шва на настоящем `hftbacktest::backtest::Backtest` — зелёный без правок
- [ ] тесты плана отскока: (а) цена пришла к `P+1`, исполнилась, ушла на 2 тика → тейк, `net` =
      2 тика минус мейкер×2 − проскальзывание; (б) сделка на `P−1` → стоп по рынку, тейкер;
      (в) вход не исполнен к `end_ms` → `EntryTimeout`; (г) 60 с без тейка/стопа → рыночный выход
- [ ] `lob backtest --touches` на `data/session-debug/t34` (ZEC, `verify ok`) → CSV + PnL, шапка с
      `rtt=assumed(20ms, В-37)`; показать 5 строк
- [ ] `cargo test --release --target-dir target-ci`, clippy, fmt; одна сборка
- [ ] `CLAUDE.md`/`docs/COMMANDS.md`/`interfaces.md` «Из таска 38»
- [ ] **GC**: `on_event` — без аллокаций на событие (план — `Copy`-структура), `f64` цены —
      только на границе с крейтом, как сейчас
