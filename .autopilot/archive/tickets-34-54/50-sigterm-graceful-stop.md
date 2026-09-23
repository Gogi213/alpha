# 50 — SIGTERM = штатная остановка (A8.2)

**Требования:** директива владельца 2026-09-17 («коллектор при любых обстоятельствах»), подтикет A8.2
в `docs/plan/dev-plan-2026-09-17.md` §Дополнения; порядок — A8.3 ✔ → **A8.2** → A8.1b → A8.4–A8.6 → A8.7.
**Blocked by:** — (независим от A8.1/A8.3)
**Зона:** `src/feed/live.rs` (`StopHandle::stop_on_signals`, `wait_for_stop_signal`), тесты
`src/feed/live/tests.rs`, `src/commands/lob/session.rs` (вызов + текст сообщений), `CLAUDE.md`,
`docs/COMMANDS.md` (строка `lob session`, ранбук «Коллектор на сервере», грабли),
`docs/plan/dev-plan-2026-09-17.md`.
**Не трогать:** `bybit/*`, `lob/*`, `commands/record.rs` (одно-символьная запись — свой цикл, отдельный
путь; у неё SIGTERM появится, только если владелец назовёт это нужным).
**Волна:** 13
**Status:** done (2026-09-18)

## Что должно заработать

`systemctl stop`, `systemctl restart`, перезагрузка машины и `kill` по умолчанию посылают SIGTERM.
До правки коллектор его не обрабатывал: процесс умирал, запись обрывалась на границе кадра (данные
целы — `FrameSink` пишет кадр одним `write_all`), но `session.json` оставался с `closed=false`, и
штатным путём (сброс писателей, финальная сводка) остановка не закрывалась. Теперь SIGTERM идёт
**тем же путём**, что файл `<root>/stop` (В-41) и Ctrl+C: `StopHandle::stop()` → `Item::Stop` в общем
канале → `None` от `Feed` → `finalize()` → `session.json closed=true`.

## Как

1. `StopHandle::stop_on_ctrl_c` → `stop_on_signals`: отдельный ОС-поток со своим однопоточным рантаймом
   (как было), внутри — `wait_for_stop_signal`, который на Unix берёт
   `tokio::signal::unix::signal(SignalKind::terminate())` и ждёт первый из двух сигналов
   (`tokio::select!` над `ctrl_c()` и `term.recv()`); строка с названием сигнала печатается оператору.
   Если обработчик SIGTERM не встал (`Err`) — остаётся Ctrl+C, как было, но без молчания.
2. Второе нажатие Ctrl+C — по-прежнему аварийный выход кодом 130 (после регистрации обработчика Ctrl+C
   сам процесс не убивает).
3. `cfg(unix)`: на Windows SIGTERM нет, ветка не компилируется, поведение прежнее (Ctrl+C или файл `stop`).
4. Шов для теста: `stop_on_signals_ready(Option<Arc<AtomicBool>>)` ставит метку **после** регистрации
   обработчика SIGTERM; продовый вход зовёт без метки.

## Критерии приёмки

- [x] тест `feed/live/tests.rs::sigterm_reaches_the_stop_handle_like_ctrl_c` (`#[cfg(unix)]`): тест
      поднимает ручку с меткой готовности, дожидается её, посылает `kill -TERM` себе и требует
      `Item::Stop` из канала — тот же выход, что Ctrl+C и файл `stop`
- [x] цепочка «`Item::Stop` → `None` → `closed=true`» покрыта существующим сценарным тестом сессии
      (`zero_events_still_writes_session_json_and_stop_closes_it`); отдельного дубля не заводил
- [x] **unix-ветка типо-проверена на Linux-цель** (локально она под `cfg` не компилируется, а сервер —
      Linux): `cargo +1.93.1 check --target x86_64-unknown-linux-musl` на копии той же функции
      (scratch-крейт с одним `tokio`) — ноль ошибок и предупреждений; полный кросс-чек проекта упирается
      в C-код `zstd-sys` без кросс-компилятора, поэтому проверялась ровно сигнальная часть
- [x] `cargo test --release --target-dir target-ci` — **795 passed, 0 failed, 5 ignored** локально
      (на Linux 796: тест под `cfg(unix)`); clippy `-D warnings` и `fmt --check` чисто
- [x] доки: `CLAUDE.md` (команды, грабли, состояние A8), `docs/COMMANDS.md` (строка `lob session`,
      ранбук — `systemctl stop`/`restart` штатны, грабли Ctrl+C), `docs/plan/dev-plan-2026-09-17.md`
      (A8.2 сделан + заметка)
- [x] **GC**: одна сборка за раз, `--target-dir target-ci`

## Что осталось за тикетом

- **Живой `kill -TERM` на сервере** (смоук-процесс во втором корне → `session.json closed=true`) —
  в A8.7 вместе с остальными сценариями; оттуда же единственная проверка ветки на настоящем Linux.
- В проде до деплоя A8.7 живёт бинарник `3614321`: там SIGTERM по-прежнему рвёт запись (данные целы,
  `closed` не выставляется) — останавливать файлом `stop`.
- `SetConsoleCtrlHandler(NULL, TRUE)` (наследование «Ctrl+C игнорировать») на Windows не менялось:
  SIGTERM там нет, штатная остановка — файл `stop`.
