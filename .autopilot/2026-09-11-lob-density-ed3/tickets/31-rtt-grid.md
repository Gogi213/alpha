# 31 — RTT 20 мс на всё: шапка артефактов печатает `assumed`, не `measured`

**Требования:** R28–R31 (§4 `fill`, `net_fill`), дополнение владельца 2026-09-12 (В-37)
**Blocked by:** 29, 30
**Зона:** `src/commands/lob/backtest.rs`, `src/commands/lob/profiles.rs` (шапка), `src/commands/lob/shortlist.rs` (шапка вердикта), `CLAUDE.md`. Не трогать: `session.rs`, `feed/`, `bybit/`, `pick/**`.
**Волна:** 12
**Status:** ready (после 29 и 30)

## Что должно заработать

Владелец назначил задержку круга 20 мс (медиана и p95) до замера — В-37 — и
отверг сетку задержек: «да зачем сетка задержек пусть будет 20 на все».
Единственное, что остаётся сделать в коде: артефакты обязаны честно печатать
происхождение числа.

## Из брифа, дословно

> «ну допустим задержка на глаз 20 мс, пока у нас нет сервера байбита, просто
> примем за факт»

> «да зачем сетка задержек пусть будет 20 на все»

## Критерии приёмки

- [ ] `lob profiles`/`lob backtest`/`lob shortlist`: флаг `--rtt-source assumed|probe:<путь>` (обязательный, без умолчания); шапка печатает `rtt=assumed(median=20ms,p95=20ms, В-37)` либо `rtt=measured(probe-<SYMBOL>.csv: median=…,p95=…)`; вердикт `Confirmed` при `assumed` печатается как `Confirmed(rtt assumed)`
- [ ] `docs/plan/runs.csv` — строки испытаний несут `rtt_source`
- [ ] тест: шапка с `assumed` и с `probe`; без флага — ошибка
- [ ] `CLAUDE.md`: команда с `--median-rtt-ns 20000000 --p95-rtt-ns 20000000 --rtt-source assumed`
- [ ] сетки задержек нет — не добавлять
- [ ] `cargo test --release`, clippy `-D warnings`, fmt — чисто
- [ ] **GC**
