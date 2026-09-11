# alpha — бот по плотностям стакана Bybit

<!-- autopilot:start -->

## Что это

Rust-проект: записать стакан Bybit сессиями, разметить крупные уровни, посчитать
профили (семь осей), вынести вердикт бэктестом с моделью очереди. Архитектура —
под живого бота с первого дня: стратегия `fn on_event<MD, B: Bot<MD>>` идёт в
`Backtest` и в `LiveBot` крейта `hftbacktest` без правок.

## Где план и состояние

- `docs/plan/PLAN.md` — план; в начале раздел «Точка остановки» с тем, как продолжать
- `docs/plan/BUSINESS-TASK.md` — задача владельца, редакция 3; **не редактировать**,
  дополнения — в `.autopilot/…/2026-09-11-brief.md`, раздел «Дополнения»
- `docs/plan/REVIEW-2026-09-11.md` — что в коде остаётся / чинится / сносится
- `docs/plan/RECON-2026-09-11.md` — разведка: три инструмента пула × 5 мин
- `docs/plan/SETTLED.md` — журнал решений (В-1…В-29)
- `docs/ARCHITECTURE.md` — A1–A9, обязательные к соблюдению
- `.autopilot/state.js` — состояние прогона (таски, волны, гейты); дашборд —
  `python .autopilot/sync.py`, затем `http://localhost:<порт из .autopilot/serve.pid>/dashboard.html`
- `.autopilot/2026-09-11-lob-density-ed3--wip/` — манифест, спека, границы
  (`interfaces.md` — читать исполнителю первым), эталоны, таски

## Команды

```bash
cargo build --release
cargo test --release 2>&1 | tail -30        # 448 passed, 0 failed, 5 ignored на 3cc2e62
cargo clippy --all-targets -- -D warnings   # бюджет линта ноль
cargo fmt --check
./target/release/alpha.exe lob --help       # pick record verify export clock probe levels markout watch pilot
```

Релизная сборка с нуля ~4 мин; инкрементально — секунды. Тесты офлайн. Живые
прогоны бьют в public WS Bybit без ключей; **любой тестовый прогон — не дольше
5 минут** (фаза отладки, результат — не данные). `data/` gitignored.

## Правила, которые ловят ревью

Семь запретов горячего пути — `interfaces.md`. Изобретённое число запрещено.
Расстояние только в bps. Шаг не закрыт, пока не существует названный артефакт.
Секреты — только имена переменных (`BYBIT_API_KEY`, `BYBIT_API_SECRET`).

## Грабли

- `lob record` требует `instruments.csv` в корне записи — брать из последнего `lob pick`
- `lob levels` с умолчанием `--warmup-ms 3600000` на пятиминутной записи даёт `levels=0`
  — это не баг, это прогрев; для отладки `--warmup-ms 0` (таск 02 вводит режим `floor`)
- `sync.py` печатает по-русски в кодировке консоли — mojibake в выводе нормален
- Bybit отдаёт `403` с части стран (CloudFront); с этой машины доступ есть

<!-- autopilot:end -->
