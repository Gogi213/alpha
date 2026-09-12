# 35b — Фронтран за секунду до касания, сметённое отдельно, завал в окне (В-45)

**Требования:** В-45 (ревью T36: корзина фронтрана `0` пуста по построению — 0 из 44 086 касаний)
**Blocked by:** 37 (одна сборка за раз)
**Зона:** `src/lob/levels.rs` (+ `levels/tests.rs`), `src/commands/lob/touches.rs` (колонка `swept_lots`,
пометка `within_touch`), `src/lob/touch_axes.rs` (без изменений границ), `src/commands/lob/dashboard.rs`
(фронтран из нового поля, `swept` в тултип, «k — заглушка» рядом с завалом), `interfaces.md`, `docs/COMMANDS.md`.
**Не трогать:** `session.rs`, `replay.rs`, `feed/`, `profiles/`.
**Волна:** 12
**Status:** done

## Что должно заработать

1. `TouchRecord.frontrun_lots` — лоты той же стороны впереди уровня на **последнем кадре с меткой
   ≤ `start_ms − HORIZONS_MS[1]`** (секунда до касания). Реализация без аллокаций: в `Live` два слота
   `(ms, better_lots)` — «старый» и «новый»; на каждом наблюдении, если `ts − новый.ms ≥ HORIZONS_MS[1]`,
   старый ← новый, новый ← (ts, better); иначе новый.lots ← better. На старте касания фронтран =
   старый слот (значение с кадра в 1–2 с до касания — сказать в doc), если старого нет — новый.
2. `TouchRecord.swept_lots` — прежняя величина (впереди уровня на последнем кадре до касания).
3. `stack_levels` — живые уровни ≥ `H3` той же стороны **в окне последней корзины расстояния**
   (`DISTANCE_BOUNDS_BPS` последняя граница, 25 bps) от цены уровня, включая его самого.
4. `lob touches` CSV: `+swept_lots`; для каждого горизонта колонка `within_touch_<h>` = `HORIZONS_MS[i] ≤
   duration_ms` (ячейка ≈ 0 по построению).
5. Дашборд: фронтран по новой величине (корзина `0` теперь достижима), `swept` в тултипе, у завала при
   `k = 1.0` подпись «k — заглушка (В-30)», в средних по осям `m_h` с `within_touch` не участвуют.

## Критерии приёмки

- [x] тест трекера: `frontrun_is_sampled_a_second_before_the_touch_and_swept_is_the_last_frame` —
      5 лотов за 1,5 с до касания и 1 лот на последнем кадре → `frontrun_lots = 5`, `swept_lots = 1`
      (ноль впереди на кадре до касания невозможен по определению касания: уровень уже был бы лучшим);
      обратный случай → `0` / `4`; `frontrun_never_reads_a_frame_later_than_a_second_before_the_touch`
      (переворот слота в кадре касания, уровень моложе секунды); `stack_counts_levels_within_25_bps_only`
      (250 тиков от 100 000 включительно, 251 — нет; `stack_window_ticks` 100 → 0, 400 → 1)
- [x] `alloc_count`: `touching_frames_allocate_nothing` зелёный без правок (слоты — `Copy` в `Live`,
      «завал» — `BTreeMap::range`, буфер `touched` тот же)
- [x] `lob touches` на `data/session-debug/t34` SOL (18 касаний): `frontrun = 0` — 2 из 18 (11 %),
      корзины долей `0` / `(0,0.5)` / `[0.5,inf)` = 2 / 4 / 12; `swept = 0` — 0 из 18 (медиана 1550 лотов,
      фронтран медиана 2369; фронтран > сметённого у 15 из 18 — ликвидность впереди за секунду до касания
      обычно больше той, что смёл последний шаг); `stack_levels` 25–26 (было 48–50 на всей стороне);
      `within_touch` 100 мс / 1 с / 10 с / 60 с = 10 / 6 / 2 / 0
- [x] дашборд: `a_touch_without_frontrun_lands_in_the_zero_bucket_and_swept_is_shown_separately` —
      строка корзины `0` с `n = 1`, `sw = 0.3` в метке, `m 10 с` внутри касания не печатается; на t34
      (`k = 1.0`) `k_stub = true` у всех трёх монет, `stack_median` 26 / 34 / 30
- [x] `cargo test --release --target-dir target-ci`: 685 passed (было 680), clippy `-D warnings` ноль,
      `cargo fmt --check` чисто; одна сборка
- [x] `interfaces.md` «Из таска 35» дополнен В-45; `docs/COMMANDS.md` — `touches` и `dashboard`
