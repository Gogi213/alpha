# alpha — бот по плотностям стакана Bybit

## Что это

Rust-проект: олвейс-он коллектор пишет стакан Bybit по пулу инструментов (В-34), разметка
крупных уровней, профили по семи осям, вердикт бэктестом с моделью очереди. Архитектура —
под живого бота с первого дня: одна стратегия `lob::strategy::on_event<MD: MarketDepth,
B: Bot<MD>>` идёт и в `Backtest`, и в `LiveBot` крейта `hftbacktest` без правок.

## Где план и состояние

- `docs/plan/PLAN.md` — план; раздел «Точка остановки» — как продолжать
- `docs/plan/BUSINESS-TASK.md` — задача владельца, редакция 3; **не редактировать**
  (дополнения — `.autopilot/2026-09-11-lob-density-ed3/2026-09-11-brief.md`, «Дополнения»)
- `docs/plan/SETTLED.md` — журнал решений В-1…В-39 (В-30 `k` выбирает пилот, В-34 олвейс-он,
  В-37 RTT 20 мс assumed, В-38 точки 30 мин/1 ч/12 ч, В-39 экономия контекста)
- `docs/findings/` — боевые артефакты (запись, пилот 30 мин, коллектор, аудит); `docs/plan/runs.csv`
- `docs/ARCHITECTURE.md` — A1–A9, обязательны; `docs/COMMANDS.md` — полная таблица команд и грабли
- `.autopilot/state.js` — таски/волны/гейты; `.autopilot/2026-09-11-lob-density-ed3/` — манифест,
  спека, `interfaces.md` (карта построенного — читать исполнителю первым), тикеты

## Команды

```bash
cargo build --release --target-dir target-ci          # target/release/alpha.exe занят коллектором
cargo test --release --target-dir target-ci 2>&1 | tail -5   # 665 passed, 0 failed, 5 ignored
cargo clippy --release --target-dir target-ci --all-targets -- -D warnings   # ноль
cargo fmt --check
./target-ci/release/alpha.exe lob --help              # 18 подкоманд, таблица — docs/COMMANDS.md
# олвейс-он коллектор (В-34): с копии бинарника, чтобы не держать target*; instruments.csv скопировать в --root
cp target-ci/release/alpha.exe data/always-on/alpha-collector.exe
./data/always-on/alpha-collector.exe lob session --pool-instruments instruments.csv --root data/always-on/<ts> --always-on
# штатная остановка (В-41): файл <root>/stop — на ближайшем тике сброс писателей, session.json closed=true
touch data/always-on/<ts>/stop
# докинуть монеты в идущую запись (T34): дописать строки в <root>/instruments.csv — подхват ≤ 10 с,
# свои <SYMBOL>-<день>.binlog и одно соединение на партию (несколько монет — одной записью файла);
# удаление строки не поддерживается (запись идёт)
grep '^ZECUSDT,' instruments.csv >> data/always-on/<ts>/instruments.csv
# дашборд «Монеты и плотности» (T33): вотчер живёт с копии data/dashboard/alpha-dashboard.exe
./target-ci/release/alpha.exe lob dashboard --root data/always-on/<ts> --out data/dashboard [--watch 120] [--h3-k 10]
python tools/serve_dashboard.py data/dashboard        # → http://127.0.0.1:<порт>/index.html
```

Релизная сборка с нуля ~4 мин, инкрементально — секунды. Тесты офлайн (сеть — только за
`#[ignore]`). `data/` gitignored; `docs/plan/candidates.csv` коммитится отдельно.

## Структура

```
src/
  main.rs, lib.rs, alloc_count.rs   вход; счётчик аллокаций — гейт GC
  binlog/      формат лога: Reader/Writer, дельты varint, zstd
  book/        книга, MarketDepth крейта, целые тики
  bybit/       адаптер площадки: conn (сокет, ресинк), ws (разбор), rest (вне событийного пути),
               verify + verify_sidecar (сверка с REST), trade_ws, sign, clock, probe
  feed/        trait Feed; replay (бинлог), live (N сокетов, один ОС-поток)
  lob/         чистая логика: levels, markout, costs, cells, shortlist, final_metrics, watch,
               strategy (единственная стратегия), runs, export, markup,
               touch_axes (корзины осей касаний В-44 — единственное место, T36/T37)
  stats/       bootstrap-t на весах Уэбба
  commands/
    record.rs              Recorder, run_record; record/{errors,gaps,paths,steps}
    lob/mod.rs             дерево модулей, LobCommand, dispatch, test_support
    lob/h3.rs, replay.rs, parts.rs, names.rs   порог H3 и CLI-аргументы; реплей → уровни/середина;
                           части и сутки сессии; имена сторон/исходов
    lob/session.rs         SessionCtx, run_session; session/{args,pool,sink,summary,resources}
    lob/pilot.rs           run_pilot; pilot/{k_grid,metrics,gates,inputs,chain}
    lob/profiles.rs        run_profiles; profiles/{axes,accumulate,coverage,sessions,table}
    lob/dashboard.rs       данные страницы (уровни + блок касаний, T36); dashboard_page.html — сама страница (include_str!)
    lob/touches.rs         run_touches — касания живых уровней → touches-<SYMBOL>.csv (T35, В-42)
    lob/pick/, react.rs, power.rs, backtest.rs, shortlist.rs, watch.rs, …
```

Тесты каждого модуля — в `<модуль>/tests.rs` (`#[cfg(test)] mod tests;`; для `mod.rs` —
`<каталог>/tests.rs`); `include_str!` в тестах — с `../`.

## Артефакты — коротко (полностью: `docs/COMMANDS.md`)

`lob pick` → `instruments.csv` (пул, ровно десять, `depth_check` — проверка, не отбор, В-35) +
`docs/plan/candidates.csv`. `lob session` → `<SYMBOL>-<день>.binlog`, `gaps.csv`, `clock.csv`,
`session.json`. `lob verify` → `verify-<SYMBOL>.status` (`ok`/`fail`, без `ok` сутки не читаются).
`lob levels`/`markout`/`touches`/`watch` → CSV на инструмент (`touches` — касания живых уровней,
markout от среза как есть на `start_ms` со знаком «в сторону отскока», T35/В-43). `lob profiles`/`backtest`/`shortlist` →
`docs/findings/*-<дата>.*` (RTT и лот — обязательные флаги; В-37: `--median-rtt-ns 20000000
--p95-rtt-ns 20000000`, шапка `rtt=assumed(20ms, В-37)`). `lob pilot` → `k`-сетка, G0,
G-POWER-B, строки `runs.csv`. `lob react` → G-LAT. `lob probe` — **реальные ордера**.
`lob dashboard` → `index.html` + `data.json` (с T36 — блок «Касания: цена дошла до плотности»: исход
отскочила/проели на касании с `m` «в сторону отскока», оси В-44 из `lob::touch_axes`, крест исход × возраст,
метки касаний на картине часа; касания — из `ReplayDay.touches` того же реплея).

Состояние: боевой пул `instruments.csv` — SOL, ZEC, XRP, HYPE, NEAR, STORJ, DOGE, ENA, LSK, SUI
(`k = 1.0` — заглушка, В-30). Идущий олвейс-он `data/always-on/20260912T122440Z/` пишет **старую
отладочную восьмёрку** с отладочным порогом (`h3_lots = 1`) — решение владельца; боевых
`profiles-*`/`backtest-*`/`shortlist-*` ещё нет.

## Правила, которые ловят ревью

Семь запретов горячего пути (ноль аллокаций на событие после прогрева, время только через
трейт `Clock`, без `async` с захватом состояния в потоке решений, без REST в событийном цикле,
сборка/подпись ордера до триггера, без `f64` для цены/размера, без `BTreeMap`/`HashMap` на
пути события) — полный текст в `interfaces.md`. Изобретённое число запрещено — измеримое
измеряется, назначаемое — коммитом/решением В-##. Расстояние только в bps. Шаг не закрыт,
пока нет названного артефакта на диске. Секреты — только имена переменных (`BYBIT_API_KEY`,
`BYBIT_API_SECRET`), значения не печатаются и не пишутся.

## Экономия контекста (В-39)

Один ревьюер по трём осям для тасков без вердикта/контракта/предрегистрации/горячего пути;
три — только для них; материал ревьюеру — дифф и тикет. Закоммитил тикет → память и передача →
клир. Правки — точечным `Edit`, файлы читать секциями, вывод команд через `tail`/`grep`,
страницу проверять `read_page`, скриншот — когда текст не отвечает. Длинный текст/HTML —
отдельным файлом (`include_str!`). Усилие `high`; выше — только дизайн и ревью вердикта.
**Исполнители — по одному:** на машине живёт коллектор, параллельные `cargo build` в
нескольких worktree дают CPU 100 % и OOM (2026-09-12) — один агент → сборка → слить → следующий.

## Грабли — коротко (полностью: `docs/COMMANDS.md`)

- `--h3-mode floor|percentile` обязателен у `levels`/`markout`/`watch`/`profiles`/`shortlist`
  (умолчание `floor` есть только у `dashboard`); `percentile` требует часа прогрева
- `k` для пола `H3` — процедура пилота (В-30), не число; пилот 30 мин: `k` не определим, G0/G1/
  G-POWER-B красные — дальше решение владельца
- `lob probe` ставит реальные ордера — не гонять без надобности
- бинлог сессии ищется одним резолвером `commands::lob::session_binlog_for`; сутки и час — у
  каждой части (`session_parts_for`)
- олвейс-он: кадр на диске не реже 10 с, читатели не видят только хвост ≤ 10 с; `session.json`
  между часовыми записями — стартовый; остановка — файл `<root>/stop` (Ctrl+C из оболочек агента не доходит)
- `lob dashboard` перечитывает все бинлоги при каждом расчёте (10 × 1.5 ч — 13 с); порог — из
  `instruments.csv` каталога записи; «жив/нет» — по росту бинлогов между двумя расчётами;
  `data.json` старого формата (без `touches`) базой для «жив» не считается — первый расчёт новым
  бинарником честно печатает «не знаю». Блок касаний: `stack_levels` не показывается при метке
  `# debug` в `instruments.csv`; при заглушке `k = 1.0` он печатается как «49 из 50» — вырожден
  порогом, не отладкой (В-43)
- Bybit отдаёт `403` с части стран; с этой машины доступ есть
