# Крейт hftbacktest 0.9.4: баг `PartialFillExchange` и локальная правка (F3/F4 этапа F, 2026-09-20)

**Итог одной строкой.** В `hftbacktest 0.9.4` нога, исполненная сделкой **ровно до нуля**,
не удаляется из книги обмена и при следующей сделке по той же цене роняет весь прогон
(`InvalidOrderStatus`); без правки модель очереди по объёму (`--queue-model prob:<n>`, F3, В-78)
на живых сутках не работает. Правка — `>` → `>=` в двух местах крейта; подключена локальной
копией (`vendor/hftbacktest` + `[patch.crates-io]` в корневом `Cargo.toml`), других изменений
в крейте нет.

## 1. Как нашли

F4 (частичная позиция) начал считать исполненное по ордерам крейта (`qty − leaves_qty`) и в
синтетическом тесте наткнулся на `BacktestError::InvalidOrderStatus`: нога 1.0 лота исполнилась
сделкой 1.0 по своей цене, а следующая сделка по этой же цене уронила `elapse`. На `risk-adverse`
ветки нет — баг живёт только в `PartialFillExchange`.

Затем баг воспроизведён **на живых данных** (счётная машина `13.140.29.171`, бинарник `b0122d2`,
сутки 16–18.09, 20 монет, `--order-qty-mult 2`, `--min-age-secs 900`):

```bash
bin/alpha-b0122d2 lob bounce-grid --root root \
  --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 \
  --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
  --order-qty-from-pool --order-qty-mult 2 --threads 3 \
  --h3-mode notional --h3-usd 10000 --min-age-secs 900 \
  --stop-form pct2 --take-form 1to1 --day 2026-09-16 --day 2026-09-17 --day 2026-09-18 \
  --symbol 1000BONKUSDT … --symbol ENAUSDT --queue-model prob:3 --out-dir /tmp/prob-big
```

Прогон прошёл 11 монет (десятки кругов) и упал на двенадцатой:
`Error: форма #0: order status is invalid to proceed the request`.

## 2. Причина (файл и строки)

`hftbacktest-0.9.4/src/backtest/proc/partialfillexchange.rs`,
`check_if_sell_filled` (стр. 155) и `check_if_buy_filled` (стр. 195):

```rust
let exec_qty = if filled_qty > order.leaves_qty {   // было строгое «>»
    self.filled_orders.push(order.order_id);        // иначе заявка не удаляется из карты
    order.leaves_qty
} else {
    filled_qty
};
```

При `filled_qty == order.leaves_qty` (сделка ровно выедает остаток заявки) условие ложно:
`exec_qty = leaves_qty`, заявка получает `status = Filled`, но **не** попадает в `filled_orders`,
то есть остаётся в `orders`/`buy_orders`/`sell_orders` обмена. Следующая сделка по той же или
меньшей цене снова зовёт `fill()`, а он на `Status::Filled` возвращает `InvalidOrderStatus`
(там же, стр. 216–221) — ошибка поднимается через `elapse` и роняет весь прогон.

Почему проявляется не всегда: равенство — редкое событие, а большинство наших исполнений
приходит обновлением лучшей цены (путь (3) из `build_backtest`, он заявку удаляет корректно);
на 111 кругах пробного прогона падения не было, на 20 монетах × 3 суток — было.

## 3. Правка

`vendor/hftbacktest/` — копия `hftbacktest 0.9.4` (MIT, © nkaz001,
https://github.com/nkaz001/hftbacktest), только `src/` и `Cargo.toml` (примеры не копировались);
в `src/backtest/proc/partialfillexchange.rs` оба места стали `filled_qty >= order.leaves_qty`
(с комментарием «ЛОКАЛЬНАЯ ПРАВКА alpha»). Вторая, косметическая правка — `#[allow(dead_code)]`
на `Value::get_string` (`src/backtest/data/npy/parser.rs`): без неё сборка крейта даёт
`dead_code`-предупреждение в выводе `cargo clippy`, а правило проекта — ноль предупреждений.
Корневой `Cargo.toml`:

```toml
[patch.crates-io]
hftbacktest = { path = "vendor/hftbacktest" }
```

Смысл правки: при равенстве исполняется ровно остаток, и заявка штатно удаляется из карты
обмена — то же, что происходит при `filled_qty > leaves_qty` (там `exec_qty` тоже равен
`leaves_qty`). Логика модели очереди не меняется: расхождение возможно только в момент
«остаток исполнен целиком», где поведение обязано совпадать.

## 4. Проверка

- `cargo build --release --target-dir target-ci` — крейт собирается из `vendor/hftbacktest` ✓.
- `cargo test --release --target-dir target-ci --lib` — 925 passed, 0 failed, 8 ignored
  (прежние 917 плюс тесты F5, которые в тот момент были в дереве) — правка ничего не сломала.
- Живая проверка: тот же самый прогон (20 монет × 3 суток, `prob:3`), который упал на
  11-й монете, после правки — см. §5 (результат прогона).

## 5. Результат живого прогона после правки

Тот же набор (20 монет в списке, сутки 16–18.09, `prob:3`, `--order-qty-mult 2`,
`--min-age-secs 900`), бинарник `bin/alpha-patched` (дерево с патчем):

```
bounce-grid: форм 4 · символов 16 (без маркера 3, без касаний 1) · символ-суток 43 ·
             кругов 567 · /tmp/prob-fixed/rounds.csv
```

Прогон дошёл до конца (до правки падал `InvalidOrderStatus` на 12-й монете списка), круги
считались, ошибок нет. Значит именно этот путь и был причиной; `--queue-model prob:<n>`
пригоден для сеток F8/F10 (В-78).

## 6. Что это значит для плана

- `--queue-model prob:<n>` теперь пригоден для сеток F8/F10 (В-78: частичное исполнение — норма).
- Правка живёт в репозитории, а не в реестре cargo: сборка на любой машине воспроизводит её
  без сети и без ручных шагов (`cargo` сам подставит `vendor/hftbacktest` по `[patch.crates-io]`).
- Обновление крейта до новой версии — отдельное решение: тогда патч проверяется заново (ветка
  `filled_qty >= leaves_qty`), и при исправлении в апстриме локальная копия удаляется.

План — `dev-plan-2026-09-20.md` §3 F3/F4; реестр — `EXPERIMENTS.md` (M15–M17 о замерах;
это техническая правка, не эксперимент).
