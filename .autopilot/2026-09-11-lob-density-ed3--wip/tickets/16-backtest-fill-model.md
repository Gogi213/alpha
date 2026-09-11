# 16 — `FillModel` поверх бэктеста: путь к `Confirmed`

**Требования:** R06, R07, R08, R50, R67
**Blocked by:** 10, 11, 12, 13
**Зона:** `src/commands/lob/backtest.rs` (реализация `FillModel`), `src/commands/lob/profiles.rs` (проводка), `src/commands/lob/shortlist.rs` (использование)
**Волна:** 6
**Status:** ready

## Что должно заработать

Таски 10–13 честно печатают `not_measured`/`none` там, где нужен настоящий
`fill`: модели исполнения у таблицы профилей и шорт-листа нет. Бэктест (таск 11)
её имеет — `RiskAdverseQueueModel`, вход мейкером, 2 секунды, пропуски по
причине. Этот таск соединяет их: `BacktestFillModel` реализует
`profiles::FillModel` поверх `lob::backtest`, и `lob profiles`/`lob shortlist`
получают `fill`, `net_fill`, `net_fill_lower`, `observed_sharpe` — а с ними
DSR/PBO/CPCV/джекнайф и достижимый `Confirmed`.

Выявлено таском 13 (BLOCKERS): «требует реального книжного потока сессии, не
только `mids`».

## Из брифа, дословно

> «`fill` — доля входов, реально исполнившихся за отведённые 2 секунды»

> «`net_fill` — `net`, взвешенный на `fill`. Вот это и есть «окупается»»

> «для профилей из шорт-листа — подтверждение на данных, которых они не видели»

## Разделы спецификации

Истории 19–25, 32–34; §6.

## Критерии приёмки

- [ ] `BacktestFillModel` реализует `FillModel` (`label = "backtest"`), гоняя ту же `on_event` через `lob::backtest` на потоке сессии; `filled` по настоящему исполнению очереди, `None` — если сигнал не попал в окно
- [ ] `lob profiles` и `lob shortlist` умеют работать с ней (флаг/умолчание — без изобретённых чисел: RTT и лот площадки — параметры как у `lob backtest`)
- [ ] шапка `fill_model=backtest`; `fill`/`net_fill`/`net_fill_lower` — числа; `observed_sharpe` — из тех же наблюдений, DSR/PBO/CPCV/джекнайф в шапке шорт-листа — числа
- [ ] тест на синтетическом потоке с известными исполнениями: `fill` совпадает с ожиданием; `Confirmed` достижим на фикстуре с достаточным `n`/`G`
- [ ] отладочный прогон на `data/pilot-debug/…` — `fill_model=backtest`, результат `debug`, не в `docs/findings/`
- [ ] **GC**
