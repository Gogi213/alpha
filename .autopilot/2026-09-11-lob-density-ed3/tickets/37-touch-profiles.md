# 37 — Профили по касаниям: таблица и число испытаний

**Требования:** R90, «делаем идею» (2026-09-13); В-42, В-43, В-44; статистический контракт задачи §6–§7
**Blocked by:** 36 (`lob::touch_axes` заведён там)
**Зона:** новый `src/commands/lob/touch_profiles.rs` (подкоманда `lob touch-profiles`), `src/lob/touch_axes.rs`
(если нужны добавления), `src/lob/runs.rs` (строка испытаний), `src/commands/lob/mod.rs` (регистрация),
`CLAUDE.md`, `docs/COMMANDS.md`. **Не трогать:** `profiles/` (сетка смертей), `shortlist`,
`levels.rs`, `session.rs`.
**Волна:** 12
**Status:** done

## Что должно заработать

`lob touch-profiles --root <каталог сессий> --h3-mode … --candidates-csv …` → `docs/findings/
touch-profiles-<дата>.csv`: строка на профиль касания — маргиналы осей В-44 плюс один крест
исход × возраст, по инструменту и по пулу. Колонки: `profile_id`, `n`, `share_bounced`, `m` на
четырёх горизонтах (среднее, bps, знак «в сторону отскока») с бутстрап-интервалом (`stats::
wild_cluster_bootstrap_t`, кластер — сутки, как у смертей), `n_days`, `level_hours_utc`.
`net`/`net_fill` здесь **нет** — они у сделки-отскока в бэктесте (T38); эта таблица — отбор
кандидатов (markout отбирает, бэктест выносит вердикт — задача §«Зачем бэктест»).

Число испытаний: сумма корзин маргиналов + клеток креста, пишется в `docs/plan/runs.csv`
(`lob::runs::append_run_row`) — тот же учёт, что у сетки смертей, чтобы DSR в шортлисте видел
и эти испытания. Шапка CSV печатает `h3`, окно, `rtt=assumed(20ms, В-37)` не нужен (RTT в
markout не входит) — не печатать лишнего.

Сутки/части — `session_parts_for`/`session_days_in_dir` как у `profiles`; маркер `verify-*.status
= ok` обязателен для суток (fail-closed, как у `profiles`).

## Критерии приёмки

- [x] фикстура двух суток с касаниями обеих сторон и обоих исходов → CSV с ожидаемыми строками,
      `n` по корзинам сходится с числом касаний; сутки без `verify ok` не читаются
- [x] интервал: на фикстуре с известным сдвигом середины `m` в границах; `n_days` = 2
- [x] `runs.csv` получает строку с числом испытаний = корзины + крест; повторный запуск —
      вторая строка (журнал, не перезапись)
- [x] живьём: `data/always-on/20260912T201737Z` (только чтение) — если суток с `verify ok` нет,
      прогнать `lob verify` на копии? **нет** — на живом каталоге маркер пишет только `lob verify`
      владельца; для живой проверки взять `data/session-debug/t34` (SOL, ZEC, XRP, 5 мин):
      `lob verify` там уже есть для ZEC — показать таблицу по ZEC
- [x] `cargo test --release --target-dir target-ci`, clippy, fmt; одна сборка
- [x] `CLAUDE.md`/`docs/COMMANDS.md`/`interfaces.md` «Из таска 37»
- [x] **GC**: горячий путь не тронут

## Сделано (2026-09-13)

- `lob touch-profiles --root --h3-mode [--h3-k] [--warmup-ms] [--repeat-window-ms] [--allow-unverified] [--out] [--now-utc] [--runs-out] [--preregistration] [--window-end]` → `docs/findings/touch-profiles-<дата>.csv`; `src/commands/lob/touch_profiles.rs` + `touch_profiles/tests.rs` (5 тестов), сетка — `lob::touch_axes::{TOUCH_AXES, touch_profile_grid, touch_grid_size, touch_marginal_id, touch_cross_id, POOL_SCOPE}` (+1 тест), журнал — `lob::runs::log_touch_profile_trials` (+1 тест).
- Отклонения от текста тикета: `--candidates-csv` не заведён — покрытие книги ни в одну ось касаний не входит (оси В-44 без расстояния; корзины подхода — сдвиг середины, не позиция в книге), пул читается из `instruments.csv` корня; интервал — `costs::net_fill_interval` с `filled = true` (тот же приём, что `m_lower` у `profiles`; `stats::wild_cluster_bootstrap_t` даёт p-значение, не интервал, и отказывает при `G < 7` — критерий «`n_days = 2`, `m` в границах» им не выполним), верхняя граница — та же функция на наблюдениях с обратным знаком; `--preregistration` необязателен — без файла шапка печатает `window: not preregistered <первые>..<последние> days= sessions=` словами (на `t34` одни сутки — `split_calendar` окна не построит); `--root` принимает и сам каталог сессии (`session.json` в нём), не только корень с подкаталогами.
- Живьём: `data/session-debug/t34`, ZEC (`verify ok`, 5 мин, `h3_lots = 4`) — 75 касаний, 16 отскочили (21 %), `m_10s` отскоков +3.07 bps, проели −1.02; все возрастом `< 10 мин`, все первые; интервал на одних сутках вырожден (один кластер) — наблюдение, не вердикт. Журнал живой проверки — во времянку (`--runs-out`), `docs/plan/runs.csv` не тронут: сессия отладочная (`session.json.debug = true`).
- 680 тестов (673 + 7), clippy `-D warnings` ноль, `fmt --check` чисто; `levels.rs`/`replay.rs`/`session.rs`/`profiles/` (кроме видимости `pub(super)` → `pub(crate)` у шести функций) не тронуты.

