# 34 — Добавление инструментов в идущую запись без перезапуска

**Требования:** R89 (владелец 2026-09-12: «нужно чтобы можно было добавлять на запись без
перезапуска»; контекст — коллектор пишет отладочную восьмёрку, боевой десятке не хватает
ZEC, STORJ, LSK, SUI), В-34 (олвейс-он), В-40
**Blocked by:** —
**Зона:** `src/feed/mod.rs` (новый трейт), `src/feed/live.rs` (`LiveFeed::add`), `src/commands/lob/session.rs`
и `session/{args,pool,summary}.rs` (слежение за `instruments.csv`, новые `SymbolState`, `session.json`),
`src/feed/replay.rs` (no-op реализация), тесты в `session/tests.rs` и `feed/live/tests.rs`, `CLAUDE.md`,
`docs/COMMANDS.md`. **Не трогать:** `bybit/conn.rs` (живые подписки не меняются — новые символы идут
новым соединением), `bybit/ws.rs`, `record.rs`, горячий путь `write_market_event`/`flush_symbol_batch`.
**Волна:** 12
**Status:** pending

## Что должно заработать

Оператор дописывает строки в `<root>/instruments.csv` (тот же формат, что пишет `lob pick`:
`symbol,tick_size,min_order_qty,qty_step,…`) — и идущий `lob session --always-on` в течение
`FRAME_LOSS_WINDOW_SECS` (10 с) начинает писать новые символы: открывает `<SYMBOL>-<день>.binlog`
(первый кадр — снапшот, который биржа шлёт первым сообщением после подписки, тем же путём, что
у стартовых символов), подписывается, добавляет символы в `session.json.instruments` и
`binlog_files`, печатает строку в stderr. Удаление символов — **не в этом таске** (строку убрали —
запись продолжается; сказать в доке). Символ уже в записи — игнорируется, не дублируется.

## Как

1. `feed/mod.rs`: трейт `DynamicPool { fn add(&mut self, members: Vec<PoolMember>) -> Result<Vec<u16>, LayoutError> }`
   — индексы новых символов продолжают нумерацию (`u16`), `Feed` не меняется.
2. `feed/live.rs`: `impl DynamicPool for LiveFeed` — `plan_connections` для добавленной партии
   (тот же лимит `MAX_ARGS_CHARS`/~380 на сокет), новые ОС-потоки с `Connection` на тот же
   `tx`, тот же `Clock`/коннектор, что у стартовых (сохранить фабрику в `LiveFeed`); `StopHandle`
   останавливает и их. Никаких изменений в живых соединениях.
3. `feed/replay.rs` (и тестовые фиды): `DynamicPool` — `Ok(vec![])`/ошибка «реплей не расширяется»
   — по смыслу, одной строкой.
4. `session.rs`: `run_session_loop<F: Feed + DynamicPool>`; в `on_tick` (не чаще раза в 10 с) —
   `instruments.csv` в `--root`: если `mtime` изменился — `load_pool` → символы, которых нет в
   `states`, → `open_symbol_state` (сутки — текущие UTC, часть — следующая свободная,
   `claim_symbol_binlog`), `feed.add`, индексы обязаны совпасть с позициями в `states`
   (assert), `session.json` переписать сразу (`write_session_json`), строка stderr
   `session: добавлен SYMBOL (tick=…, step=…) — файл …`. Ошибка чтения файла или строки —
   строка в stderr, запись продолжается, повтор на следующем `mtime`.
5. Сверка (`verify_sidecar`), если она держит список символов на старте, — добавить туда же;
   если по индексу — проверить, что новый индекс не паникует.
6. Ничего в горячем пути: проверка `mtime` — один `metadata()` раз в 10 с на тике, аллокации —
   только в момент добавления (редкое событие, не на событие рынка).

## Критерии приёмки

- [ ] тест `feed/live/tests.rs`: `LiveFeed::add` с фейковым коннектором — события нового символа
      приходят с новым индексом, старые не сдвинулись, `stop` гасит и новый поток
- [ ] тест `session/tests.rs`: между двумя тиками в `instruments.csv` дописана строка → появился
      `<SYM>-<день>.binlog` с заголовком (tick/step из строки), `session.json.instruments` и
      `binlog_files` выросли, повторная запись той же строки ничего не дублирует, битая строка —
      только stderr
- [ ] тест: индекс события нового символа совпадает с позицией `SymbolState` (иначе кадр уйдёт
      в чужой файл)
- [ ] `cargo test --release --target-dir target-ci` (649 + новые), clippy `-D warnings`, fmt;
      семь запретов горячего пути — ноль аллокаций на событие после добавления (тест с
      `alloc_count`, как у существующих тестов сессии)
- [ ] живьём **не проверять на идущем коллекторе** (`data/always-on/20260912T122440Z` — старый
      бинарник, не трогать); живой прогон — `lob session --minutes 5` в `data/session-debug/<ts>`
      с двумя символами, дописать третий через минуту, показать три бинлога и строку stderr
- [ ] `CLAUDE.md` «Команды» + `docs/COMMANDS.md`: как докинуть монету (дописать строку в
      `<root>/instruments.csv`; удаление — не поддерживается)
- [ ] **GC**: сборка одна, в `target-ci`, никаких параллельных cargo
