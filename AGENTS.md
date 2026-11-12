# alpha

Rust + Python. Исследование и инфраструктура данных для крипто-бирж: коллекторы рынка по вебсокету, бэктесты,
поиск торговых закономерностей. Бот будет размещён в роботе ФК «Викинг» (документация — MCP `viking-docs`).

Работа идёт в bb по pstack: координатор — главный тред, исполнители и проверяющие — дочерние треды bb
(`.claude/skills/poteto-mode/SKILL.md`, модели — `.claude/skills/poteto-mode/references/models.md`).
Постоянные приказы владельца — `.claude/preferences.md` (обязательны для всех тредов).

**Child threads (исп / пров / чит): English only, no chatter.** Everything you write — progress lines, messages, commit-free notes, the final report — is in English. Quote the owner's words verbatim in Russian. Do not narrate: no "now I will…", no restating the brief, no summaries between steps. Write a one-line progress note only when the state changes (step done, blocker found). Final report: status, branch + SHA, results with numbers, deviations — nothing else. Repo docs (`docs/**`) stay in Russian.

## Сейчас (12.11.2026)

- **Цель G-022 — свои датасеты коллекторами по вебсокету:** Binance → Bybit → OKX → Bitget → Huobi → BingX, по одной;
  пул от 300 тыс. $/сутки, на Binance и Bybit без топ-10 по объёму. План, правила и где мы —
  `docs/plan/G-022-collectors-plan-2026-11-12.md` (§4 шаги, §7 что осталось).
- Binance принят (509 символов, пишет), дашборд здоровья принят: http://5.231.25.224:8088/.
  Bybit пишет 389 символов, не принят (таймауты). Лестницы нагрузки Binance/Bybit — без отчёта.
  Код — ветки `tk527-binance-ws`, `tk532-bybit-ws` (`tools/collector/`, ядро `wscore/` + адаптер на биржу), в `master` не слиты.
- Удаление старых скачанных датасетов на сервере и Storage Box (В-131, В-132) — идёт, отчёта нет.
- Исследование схождений П-40 (`docs/research/P-40-*`) — на паузе.
- Прошлые состояния и история — `docs/archive/claude-md-history-*.md` (грепом); архив команды rpv — тег `archive/rpv-2026-11-12`.

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

Ствол — `master`. Ветка = одна тема, свой worktree. Слияние в `master` — после зелёного гейта и вердикта проверяющего
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
