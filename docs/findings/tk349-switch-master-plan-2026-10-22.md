# TK-349 — план перевода основного дерева на master (22.10, Инженер; на приёмку Судье)

Замер 22.10 08:55–09:05 по `origin/master` (d62ca84d), не по локальному `master` (cef9f846 — отстаёт на 562 коммита,
fast-forward возможен). Основное дерево — ветка `tk244`, на 3 коммита Судьи впереди `origin/master` (только
`docs/research/reviews/tk348-merged*.md` и блокнот).

## 1. Что мешает `git switch master` (числа)

| класс | шт. | что это |
|---|---|---|
| неотслеживаемые, лежат на пути (в master есть) | 79 | 67 — байт в байт как в master; **12 — другое содержимое**: тикеты TK-188/191/196/212/215/231/237, `docs/findings/tk214-…`, `tk216-…`, `tk265-…`, `tools/compute/bs-launch.sh`, `tools/registry/load_f_grid.py` |
| изменённые отслеживаемые (53) → совпадают с master | 17 | master уже содержит эту правку (слияния веток) — безопасно |
| изменённые → правки только здесь (master = HEAD) | 19 | git перенесёт сам |
| изменённые → **правки с обеих сторон** | 17 | `.claude/{settings.json, roles/engineer.md, roles/notes/ceo.md, dispatcher/ceo-restart-services.py}`, `src/commands/lob/bounce_grid.rs`, `bounce_grid/{sets.rs,tests/admit.rs}`, `tools/compute/{alsched.py,day-cells.sh,sched_cfg.py,tk212-f1-run2.sh,tk308-sieve.py,tk340-sieve.py,tests/test_alsched_core.py}`, `tools/launch/{check_page.py,page.html}`, `docs/findings/tk200-f-by-month-…` |
| удалён локально | 1 | `data/tk178-ra-run.sh` (из-за него `vps-check` падает на tar) |
| в HEAD, нет в master → switch их **удалит** | 59 | 57 в `tools/compute`, 2 в `docs/research` (скрипты П-23/П-24 и др., вычищены в TK-348 в master) |
| в master, нет в HEAD → switch создаст | 763 | |

Живые файлы диспетчера (`ceo-inbox.md`, `*.log`, `runs/`, тикеты, `overseer.json`) пишутся каждую минуту — на момент замера
менялись TK-348/349, `notes/judge.md`, `overseer.json`. Окно тишины нужно.

## 2. Правило для `.claude/` (п. 1 задачи)

Одно правило вместо «часть в git, часть нет»:

- **Общее состояние команды — в git, коммитит автор записи в конце хода**, в ствол (`master`): `.claude/tickets/**`,
  `.claude/roles/**` (устав, блокноты, журнал), `.claude/decisions.md`, `.claude/goals/`, `.claude/errors/`,
  `.claude/brief/`, `.claude/settings.json`, `.claude/rpv.json`, `CLAUDE.md`. Это уже делает Судья («[Судья] блокнот…»);
  неотслеживаемыми они остались только потому, что основное дерево стояло на ветке-теме и коммиты шли мимо ствола.
- **Рантайм диспетчера — не в git** (`.claude/dispatcher/.gitignore`, дополнить): `guard.log`, `jobs.jsonl`,
  `mechanics.log`, `notify.log`, `overseer.json`, `overseer.log`, `overseer.run.log`, `release-auto.log`,
  `release-journal.jsonl`, `report.log`, `reports/`, `supervise-state.json`, `tk077-day-check.txt`, `branch-delete.json`
  (машинные, переписываются, читателя на другой машине нет). `ceo-restart-services.py` — код, остаётся в git.
- Ветка основного дерева — `master` и только он; ветка-тема живёт в `.claude/worktrees/<имя>` (как tk346/tk348).
  Хук на `git switch` в основном дереве не нужен: дереву одна ветка.

## 3. Порядок переключения без перезаписи живых файлов (п. 2)

Идея: **не `switch`, а «коммит живого → слияние master в tk244 → `master` быстро вперёд → HEAD на master»**. Рабочие файлы,
которых не касается слияние, не трогаются вообще; конфликты решаются явно, а не молча.

0. **Окно тишины.** Остановить диспетчер штатным способом (устав README), убедиться: `runs/` без живых сессий, `git status`
   дважды подряд с интервалом 60 с даёт одно и то же. Сессию Судьи/Инженера не запускать.
1. **Резерв (scratch на этой машине, вне репо):** `C:/scratch/tk349-<ts>/`: `tar` всех изменённых + неотслеживаемых +
   игнорируемых файлов `.claude/` (`git ls-files -m -o`, + `.claude/dispatcher`), `manifest-before.sha256` (sha256 каждого
   файла рабочего дерева, без `.git`), `HEAD`, `git diff > local.patch`, `git status --porcelain > status.txt`.
2. `git fetch origin && git branch -f master origin/master` (локальный `master` был отстающим предком — безопасно).
3. Дополнить `.claude/dispatcher/.gitignore` (п. 2); `git rm --cached data/tk178-ra-run.sh`-эквивалент: файл
   удалён намеренно (`git add -u data/`).
4. **Фиксация живого:** `git add -A` (после нового ignore) → один коммит на `tk244` «[Инженер] живое состояние перед
   переводом на master (TK-349)». Рабочее дерево после этого = HEAD, нечего терять.
5. `git merge --no-commit origin/master`. Конфликты (ожидаются 12 add/add + до 17 правок с обеих сторон) решать скриптом:
   - `.claude/**`, `CLAUDE.md` → `git checkout --ours` (живое);
   - остальное (`src/`, `tools/`, `docs/`) → `git checkout --theirs` (master = принято Судьёй), локальная версия остаётся в
     резерве. Исключение: файл, чья локальная версия — непринятая работа (проверка ниже) → отдельным тикетом, не молча.
   Файлы, которые слияние удаляет (59), — в резерве; это решение TK-348.
6. `git commit` (слияние) → `git branch -f master HEAD` (быстро вперёд от `origin/master`, `merge-base --is-ancestor`) →
   `git symbolic-ref HEAD refs/heads/master` (дерево не меняется). Ветку `tk244` удалить: тег `archive/tk244-final` уже
   на origin (TK-348).
7. **Сверка:** (а) `sha256sum -c` для каждого пути из `manifest-before.sha256`, лежащего под `.claude/**` и в списке «ours»
   — ноль расхождений (кроме файлов, которые диспетчер успел дописать — тогда сверять префикс/строки); (б) `git status`
   пуст по отслеживаемому; (в) `git diff origin/master --stat` — только состояние `.claude/` и 3 файла Судьи;
   (г) `bash tools/vps-check.sh <чистый worktree master> all`.
8. Запустить диспетчер, дождаться одного хода роли, убедиться, что запись в `TK-349.md` легла на `master`.
9. Откат (до п. 6): `git merge --abort`; после: `git reset --hard <коммит п. 4>` + `git symbolic-ref HEAD refs/heads/tk244`;
   полный откат — распаковка резерва.

## 4. Происхождение локальных правок не-`.claude` (замер: блоб рабочего файла сверен со всеми 100 ветками)

- **Дубль ветки tk346 (не слита):** `bounce_grid.rs`, `bounce_grid/sets.rs`, `bounce_grid/tests/admit.rs` — блоб равен
  `tk346`. Отбрасываются (`theirs`); работа цела в ветке `tk346` (b0a3ae74, на origin).
- **Старые ветки (устарели):** `sched_cfg.py`, `tests/test_alsched_core.py` = `tk223b/c`; `tk308-sieve.py` = `tk341*/tk347`;
  `tools/registry/load_f_grid.py` = `tk203…tk211`; `docs/findings/tk214-…/tk216-…/tk265-…` = версия до правок master. → `theirs`.
- **Нигде — непринятая локальная работа (8 файлов):** `tools/compute/{alsched.py, day-cells.sh, tk212-f1-run2.sh,
  tk340-sieve.py, bs-launch.sh}`, `tools/launch/{check_page.py, page.html}`, `docs/findings/tk200-f-by-month-…`. Для
  них шаг 5 — не `theirs`: перед слиянием вынести в ветку `tk349-leftovers` (коммит п. 4 уже её содержит — ветку тегнуть
  `archive/tk349-leftovers`), в дереве после слияния — версия master; Судья решает по каждому, нужна ли правка.
  Для 4 `.claude/…` из «обеих сторон» (`settings.json`, `roles/engineer.md`, `roles/notes/ceo.md`,
  `ceo-restart-services.py`) — `ours`.
- Решение Судьи: ставить ли `tk346` в слияние перед переключением (В-39: хвост, без владельца).

## 5. Критерий приёмки (из тикета)

Основное дерево на `master`; sha живых файлов из резерва = рабочим (п. 7а); Судья принял план **до** исполнения (п. 3
исполняет Инженер в окне тишины).
