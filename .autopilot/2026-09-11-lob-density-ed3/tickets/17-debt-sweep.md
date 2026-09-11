# 17 — Долг ремесла: триаж `concerns` перед сдачей

**Требования:** R71, R73, R79, R80 (качество кода как обещание бота)
**Blocked by:** 16
**Зона:** `src/**` — точечно, по списку ниже
**Волна:** 7
**Status:** ready

## Что должно заработать

Phase 8 триажа: находки ревью, которые повторились в трёх и более тасках или
дёшевы и опасны, закрываются одним проходом, чтобы следующая сессия не
встретила их в первый же день. Поведение команд и артефактов **не меняется**.

## Список (приоритет сверху вниз; при потолке контекста — HANDOFF после зелёного)

1. **Сдвоенный код** (3+ таска): `trade_hit_from_record`/`is_trade_ev` — один `pub(crate)` в `commands/lob/mod.rs` (или `binlog`), используют `feed/replay.rs`, `commands/lob/watch.rs`, `bybit/verify.rs`; `best_prices` — один владелец (`backtest.rs` или `strategy.rs`); `read_clock_bybit_rtts` в `pilot.rs` → фильтр над `bybit::clock::read_rows`; `resolve_h3_mode`/`h3_lots_for_symbol` из `commands/lob/levels.rs` → `commands/lob/mod.rs` (общий код подкоманд).
2. **Args-структуры**: общая `H3Args` (`--h3-mode`, `--h3-lots`) с `#[command(flatten)]` в `levels/markout/pilot/watch/profiles/shortlist`; общая `ExecutionArgs` (`--median-rtt-ns`, `--p95-rtt-ns`, `--order-qty-e9`) в `profiles/shortlist/backtest`. `--h3-lots` вместе с `--h3-mode floor` — ошибка, не молчание.
3. **Снос C1/C2**: `lob/watch.rs` (`is_c1`/`is_c2`/`TRIGGER_*`/`DayTally`/`day_eligible`/`WatchState`/`ReadyFlag` — что ещё используется `cells.rs`/`pilot.rs`/`markup.rs`/`mod.rs::day_tallies`, переводится на сессионные аналоги таска 07 или удаляется вместе с потребителем, если потребитель сам отменённый); `WatchSummary.n_c2/g_c2` → `n/g`; `shortlist::ProfileRow`/`write_profiles_csv`/`read_profiles_csv` — удалить, если после таска 11 их никто не читает; докстрока `stats::GATE_ALPHA` — по факту; докстрока `costs.rs:32`; докстринг теста `power.rs:126`.
4. **Горячий путь**: `bybit/sign.rs` — `sign_into(&mut [u8; 64])` без аллокации (HMAC в буфер, hex в массив), `Credentials::sign_into` не делегирует старому `sign()`; alloc-тест на реальном `Credentials` с тестовыми ключами из окружения теста (не из реального). Греп-тест горячего пути (`Instant::now`/`SystemTime`/`f64`-цена/`HashMap`) на `feed/live.rs`, `feed/replay.rs`, `strategy.rs`, `react.rs` — по образцу `levels.rs:689`.
5. **Мелочи с тестами**: `lob session --minutes` — валидация 5..15 (ошибка вне диапазона); `session.json` — поле `debug: true` при `duration_s < 3600`; флаки `conn.rs::connect_then_close_without_forwarding_a_message_does_not_reset_backoff` — убрать зависимость от реального времени (фейковые часы/детерминированный планировщик); фикстура ≥ 7 суток для ветки `hour_dependence_test → Ok → log_hour_test` в `profiles.rs`; сквозной тест `profiles → shortlist` через `BacktestFillModel` до `Confirmed` на синтетике.

## Критерии приёмки

- [ ] `grep -rn "is_c1\|is_c2\|TRIGGER_N_C2\|n_c2\|g_c2" src/` — пусто
- [ ] один `trade_hit_from_record`, один `is_trade_ev`, один `best_prices` в `src/`
- [ ] `H3Args`/`ExecutionArgs` — по одному определению, `--help` подкоманд не изменился по смыслу
- [ ] `sign_into` реального подписанта — ноль аллокаций (тест `alloc_count`)
- [ ] все команды дают те же артефакты (прогон `lob pilot --debug` на `data/pilot-debug/20260911T084259Z/session` в реплее — те же числа в `pilot-debug-summary.csv`, кроме порядка колонок)
- [ ] **GC**
