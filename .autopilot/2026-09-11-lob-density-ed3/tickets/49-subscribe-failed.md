# 49 — Отказ подписки биржей (A8.3)

**Требования:** директива владельца 2026-09-17 («коллектор при любых обстоятельствах»), подтикет A8.3
в `docs/plan/dev-plan-2026-09-17.md` §Дополнения; порядок — A8.3 → A8.2 → A8.1b → A8.4–A8.6 → A8.7.
**Blocked by:** — (независим от A8.1)
**Зона:** `src/bybit/ws.rs` (`Event::SubscribeFailed`, разбор ответа, `topic_symbol`),
`src/bybit/conn.rs` (`ConnEvent::SubscribeFailed`, привязка к инструменту в `handle_raw`),
`src/feed/mod.rs` + `src/feed/live.rs` (класс `GapKind::SubscribeFailed`), `src/commands/record.rs`
(путь одно-символьной записи), `src/commands/record/gaps.rs` (вариант `GapKind`), `src/commands/lob/
verify.rs` (`GapTally` — шов), `src/commands/lob/session.rs` + `session/summary.rs` (счётчик),
`src/commands/lob/backtest.rs` + `session/sink.rs` (неполные матчи), тесты, `CLAUDE.md`,
`docs/COMMANDS.md`, `docs/plan/dev-plan-2026-09-17.md`.
**Не трогать:** `bybit/rest.rs`, `trade_ws.rs` (приватный поток), `lob/*` (логика разметки), горячий
путь `write_market_event` (только ветка «служебное — не записывать»).
**Волна:** 13
**Status:** done (2026-09-18)

## Что должно заработать

1. Биржа отвечает на подписку `{"success":false,…}` — отказ **виден**: строка `gaps.csv` класса
   `subscribe_failed` (`ts_utc, symbol, kind, detail`) и счётчик `session.json.subscribe_failed`.
2. Отказ привязан к **инструменту топика**, а не к сокету: биржа отвечает на каждый несогласованный
   топик отдельно и называет его в `ret_msg`; соседи по той же партии подписок продолжают писать.
   Топик не разрешился в инструмент пула — событие уходит на первый инструмент сокета (как события
   без символа), молчания нет.
3. `verify` считает класс **швом покрытия** (В-59): сутки не роняет, попадает в `seams=`.
4. Успешный ack (`success:true`), `pong` и отказ по чужой операции (`op != subscribe`) остаются
   служебными (`Event::Other`) и потери не изображают.

## Замер (сделан до кода, 2026-09-18)

Живой сокет Bybit, партия подписок с несуществующим символом (`ZZZFAKEUSDT`), сырой ответ:

```
{"success":false,"ret_msg":"error:handler not found,topic:orderbook.50.ZZZFAKEUSDT",
 "conn_id":"da7tqdmknoaf049d2rrg-6they","req_id":"","op":"subscribe"}
```

Факты замера: ответ **на каждый несогласованный топик отдельно**; топик — в `ret_msg` (поля `args`
нет); на `.200` и `publicTrade` того же несуществующего символа биржа **не** пожаловалась (отказ
топик-специфичен); до правки это сообщение читалось как `Other` и не оставляло следа нигде.
Замер снят временным `eprintln` в ветке `TopicKind::Other` (напечатал сырой текст), который **убран**
перед коммитом.

## Как

1. `ws.rs`: `Event::SubscribeFailed { topic: Option<String>, ret_msg: String }`; разбор служебного
   сообщения (`SubscribeAck`: `op`/`success`/`ret_msg`, поля заимствуются — разбор без аллокаций,
   строки собираются только на отказе); `failed_topic` вынимает топик из `ret_msg`; `topic_symbol`
   (публичный) берёт символ топика тем же `split_topic`, что маршрут события.
2. `conn.rs`: `ConnEvent::SubscribeFailed { local_ts_ns, topic, ret_msg }`; в `handle_raw` до ветки
   рынка — привязка по топику (`topic_symbol` → `route.slot_of`), «топик не разрешился» → индекс
   сокета; `session.productive = true` (ответ доказывает, что сокет жив, — иначе бэкофф не сбросится).
3. `feed`: `GapKind::SubscribeFailed`; `live.rs` кладёт `Event::Gap` с деталью «подписка не
   состоялась: `<topic>` — `<ret_msg>`», `depth: None` (отказ — про инструмент, не про поток).
4. `record/gaps.rs`: вариант `GapKind::SubscribeFailed` (serde snake_case → `subscribe_failed`);
   `verify.rs`: класс в `seams`; `session.rs`: счётчик `subscribe_failed` + строка `gaps.csv`;
   `summary.rs`: поле `#[serde(default)]`; `record.rs`: тот же путь для одно-символьной записи.

## Критерии приёмки

- [x] тест `ws`: сырой ответ с живого сокета разбирается в `SubscribeFailed` с топиком и `ret_msg`;
      отказ без топика — событие без привязки; успешный ack/`pong`/чужой `op` — `Other`;
      `topic_symbol` для `orderbook.*`/`publicTrade.*` и `None` для чужого топика
- [x] тест `conn`: сокет с двумя инструментами — отказ по топику второго приходит **индексом 1**,
      снапшот первого доходит следующим событием (сосед жив); отказ по топику вне пула — индекс 0
- [x] тест `session`: `FeedGapKind::SubscribeFailed` → `subscribe_failed=1`, строка `gaps.csv` класса
      `subscribe_failed` с именем инструмента и текстом биржи, книга инструмента недоверена
      (`synced = false`); тест `record`: имя класса в файле — `subscribe_failed`; тест `verify`:
      класс в `seams`, не в `losses`
- [x] **живой прогон** 2026-09-18 (локально `data/sub-probe2`, 2 инструмента: HYPEUSDT и
      `ZZZFAKEUSDT`, один сокет, 51 с, остановка файлом `stop`): строка
      `2026-09-17T20:34:44Z,ZZZFAKEUSDT,subscribe_failed,"подписка не состоялась:
      orderbook.50.ZZZFAKEUSDT — error:handler not found,…"`, `session.json.subscribe_failed = 1`,
      `gaps = 1`, `connect_failed = 0`, `closed = true`, в строке сводки stderr
      `connect_failed=0 subscribe_failed=1`
- [x] `cargo test --release --target-dir target-ci` — **795 passed, 0 failed, 5 ignored**; clippy
      `-D warnings` и `fmt --check` чисто
- [x] доки: `CLAUDE.md` (швы включают `subscribe_failed`, состояние A8), `docs/COMMANDS.md` (строка
      `lob session` — счётчик, строка `lob verify` — швы), `docs/plan/dev-plan-2026-09-17.md`
      (A8.3 сделан + заметка), `//!`/доккомментарии в коде
- [x] **GC**: одна сборка за раз, `--target-dir target-ci`

## Что осталось за тикетом

- **Смоук на сервере и деплой** — вместе с остальными подтикетами A8 (A8.7).
- Отказ подписки **не переписывает** пул и не снимает инструмент: он виден в `gaps.csv`/счётчике, но
  сам не лечится — повтор подписки произойдёт только на переподключении сокета (то же поведение, что у
  разрыва `u`: ресинк живого сокета). Если владелец захочет автоматику «символ без данных N минут →
  снять из пула» — это отдельное решение, не изобретённое здесь.
