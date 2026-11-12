# alpha

Rust + Python. Исследование и инфраструктура данных для крипто-бирж: коллекторы рынка по вебсокету, бэктесты,
поиск торговых закономерностей. Бот будет размещён в роботе ФК «Викинг» (документация — MCP `viking-docs`).

Работа идёт в bb по pstack: координатор — главный тред, исполнители и проверяющие — дочерние треды bb
(`.claude/skills/poteto-mode/SKILL.md`, модели — `.claude/skills/poteto-mode/references/models.md`).
Постоянные приказы владельца — `.claude/preferences.md` (обязательны для всех тредов).

**Child threads (исп / пров / чит): English only, no chatter.** Everything you write — progress lines, messages, commit-free notes, the final report — is in English. Quote the owner's words verbatim in Russian. Do not narrate: no "now I will…", no restating the brief, no summaries between steps. Write a one-line progress note only when the state changes (step done, blocker found). Final report: status, branch + SHA, results with numbers, deviations — nothing else. Repo docs (`docs/**`) stay in Russian.

## Сейчас (13.11.2026)

- **G-022 — коллекторы бирж по вебсокету на Rust** (В-149..В-152): Binance → Bybit → OKX → Bitget → Huobi → BingX; цель — хороший коллектор,
  не непрерывный сбор (В-152). Передача дел: `docs/plan/G-022-handoff-2026-11-13.md` (читать первым).
- Rust-коллектор Binance + Bybit — ветка `g022-turbo` (`src/collector/`), в `master` не слит; работает фоном на srv (`soak-binance`, `soak-bybit`,
  данные `/data/turbo-probe/`), страница здоровья http://5.231.25.224:8088/. Python-коллекторы остановлены.
- Binance: маршрут srv → Токио двухрежимный, задержки не лечатся кодом — `docs/findings/binance-route-problem-2026-11-13.md`; через Софию не подключаем (В-153).
- Подготовка OKX, Bitget, HTX, BingX готова (ветки `okx-ws-prep`, `g022-prep-3`, отчёты `docs/findings/*-ws-prep-2026-11-12.md`).
- П-40 — на паузе. История — `docs/archive/claude-md-history-*.md`; архив rpv — тег `archive/rpv-2026-11-12`.

## Где что

- Решения владельца: реестр `.claude/decisions.md`, ранние — `docs/plan/SETTLED.md` (оглавление `SETTLED-index.md`);
  оба только грепом по номеру. `docs/plan/BUSINESS-TASK.md` — не редактировать.
- Исследование: скилл `.claude/skills/alpha-research/` (обязателен перед любым торговым выводом), протоколы
  `docs/research/P-<NN>-*.md`, журнал `docs/research/JOURNAL.md`, проверки `docs/research/reviews/`, реестр `docs/plan/EXPERIMENTS.md`.
- Разборы и замеры — `docs/findings/<тема>-<YYYY-MM-DD>.md`; сырое и промежуточное — `data/` (gitignored).
- `docs/ARCHITECTURE.md` (A1–A9), `docs/COMMANDS.md` (команды, грабли, ранбуки машин).

## Машины

ssh с этого ПК — всегда с `-i` и `-o UserKnownHostsFile=...` (кириллица в HOME).

- **srv `5.231.25.224`** (Debian 13, 8 vCPU, 62 ГБ, NVMe 394 ГБ): счёт, закачка, коллекторы G-022 (`/data/bnws`, `/data/bbws`,
  метрики `/data/health/<биржа>.json`), сборки. Долгие процессы — `systemd-run`, не висящая ssh-сессия.
- **VPS София `13.140.29.171`** (4 ядра, 7,9 ГБ): запасные сборки (`CHECK_HOST=vps`); счёт — нет (кроме В-101). Сторож места режет сборки при свободном < 23 ГБ.
- **Storage Box** `u677479@u677479.your-storagebox.de`, порт 23, вход по ключу; урезанный набор команд (ls, du, df, rsync/sftp).
- Не использовать: calc `89.163.242.211` (не наш, В-102), Steam Deck (В-104). Сервер `139.99.91.22` — старый коллектор, остановлен.
- ПК владельца: без тяжёлого счёта; cargo запрещён хуком.

## Команды

```bash
bash tools/vps-check.sh <рабочее дерево> all        # test + clippy -D warnings + fmt на srv (CHECK_HOST=vps — София)
bash tools/vps-check.sh <рабочее дерево> test <фильтр>
cargo fmt --check                                   # единственное cargo, что можно локально
```

Полный список и грабли — `docs/COMMANDS.md`.

## Git

Ствол — `master`. Ветка = одна тема, свой worktree. Слияние в `master` — после тестов изменённого модуля и вердикта проверяющего; CI GitHub (GC + Quality, полные тесты) запускается push'ем ветки, его не ждём — красный CI чиним отдельной задачей
на другой модели (`.claude/skills/poteto-mode/playbooks/landing.md`). Законченная ветка — тег `archive/<имя>` и удаление;
теги `archive/*` не удалять. Без force-push и переписывания `master`. В коммит — только свои файлы.

## Правила кода

- Горячий путь стратегии: ноль аллокаций на событие после прогрева, время только через трейт `Clock`, без `async` с
  захватом состояния в потоке решений, без REST в событийном цикле, ордер собран до триггера, без `f64` для цены/размера,
  без `BTreeMap`/`HashMap` на пути события. Расстояния — в bps.
- Структура `src/`: `binlog/` · `book/` · `bybit/` · `feed/` · `lob/` (единственная стратегия `lob::strategy::on_event`) ·
  `stats/` · `commands/`. Тесты модуля — `<модуль>/tests.rs`.
- Шаг не закрыт без названного артефакта на диске. Заменённый документ — в `docs/archive/` тем же коммитом, ссылки поправлены.
- Секреты — только имена переменных (`BYBIT_API_KEY`, `BYBIT_API_SECRET`), значения не печатать.

## Грабли

- `lob probe` ставит реальные ордера.
- На серверах никаких `find`/`du` по большим каталогам вне `systemd-run`: оборванная ssh-сессия оставляет их жить.
- Bybit отдаёт 403 с части стран; с этого ПК и srv доступ есть.
- Вывод команд — через `tail`/`grep`; длинные логи читает отдельный тред, а не координатор.
