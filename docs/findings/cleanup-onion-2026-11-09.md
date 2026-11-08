# Уборка «как лук» (В-112, TK-508) — 09.11.2026

Судья. Режем всё, что не служит П-40 и не сырьё (владелец: «о да весь мусор под нож»). НЕ трогаем: сырьё стаканов
(бокс, `/data/stakan`), П-40 (`tk493-xdl9`, `tk500-p40core`, TK-495/496/499/500/501/504), реестр решений, теги `archive/*`.

## Слой 1 — мешает работе сейчас (сделано 09.11 02:24–02:31)

| что | сделано | почему / проверка |
|---|---|---|
| E-001…E-005 (calc: ssh-обходы, claim, alsched ×3) | `errors.py retire --decision В-102` | calc больше не наш (В-102); `grep ^status .claude/errors/E-00*` → 8 retired, 1 open |
| E-007 (tk433-oi, пампы) | retire `В-108` | пампы закрыты и удалены (В-108) |
| E-008 (tk463-bench3, смарт-мани) | retire `В-82` | смарт-мани убран из дерева, код в `archive/smartmoney-2026-10` |
| E-006 (`tickets.py _has_number`, CSV) | **оставлено open** | живой баг гейта чисел плагина, не calc |
| E-012, E-013 (ложный гейт чисел) | оставлены open | живые, класс гейта чисел |
| Планировщик Windows `rpv-tk217-uncut` | удалён (`Unregister-ScheduledTask`) | разовый триггер 12.10 21:09, цель `data/tk217-stand4` не существует; TK-217 done |
| Планировщик `tk079-day-flag` | удалён | разовый триггер 09.10 02:00, TK-079 done; флаг никто не читает (`git grep tk079-day-clean` — только тикеты/отчёт) |
| `rpv-supervise-3131fcb1` (`C:\vdev\verger`), `-37b6bcc9` (`role-play-vibing`), `-fe78a0b4` (`screenshot`, Disabled) | **не трогал** | чужие проекты, не alpha |
| `rpv-supervise-5376d7eb` (alpha), `alpha-pulse` | оставлены | живые (диспетчер alpha, экран прогресса) |
| 32 временных worktree `%TEMP%/e011-*/{before,after}` | `git worktree remove --force` → 0 | остатки снятой проверки E-011 (В-111) |
| Ветки, влитые в master: pr79, tk332-merge, tk334, tk339, tk341, tk346, tk348, tk353, tk481-gate | тег `archive/<имя>` (новые: pr79, tk348, tk481-gate; остальные уже были, sha совпадают) + удаление локально | 38 → 29 локальных веток; origin не трогал |
| TK-497/498/503/505/506 | уже `done` | закрывать нечего |
| TK-486 (blocked, calc) | → CEO | владелец тикета engineer; закрыть — `tickets.py stop` CEO (calc мёртв, В-102) |
| TK-475 (needs_owner, чистка ПК) | → CEO | покрывается этой уборкой (В-112) — CEO закрыть со ссылкой на TK-508 |

### Правки `.claude/rpv.json` — для CEO (`tickets.py config set`)

| ключ | сейчас | надо | почему |
|---|---|---|---|
| `watched_aliases` | `["calc"]` | `[]` | calc не наш (В-102); сторож гоняет по нему `job_owners_cmd`/`stray_cmd` впустую |
| `job_state_cmd` | `sh /data/sched/jobstate.sh {id}` | не трогать до замены (ключ требует непустую строку); при пустом `watched_aliases` не исполняется | `/data/sched` — calc |
| `job_owners_cmd` | `sh /data/sched/jobowners.sh` | то же | то же |
| `guard_remote_roots`, `guard_forbidden_hosts` | список с Deck/calc/`/opt/alpha-*` | удалить руками из файла (нет в закрытом списке `config set`) | плагин 2.0.24 их **не читает** (CHANGELOG:141 «больше не читаются») — мёртвые ключи |

## Слой 2 — мёртвая инфраструктура в репо (09.11 ~03:25, ветка `tk508-onion`)

**Снято:** `tools/compute/` на `master` — 1664 файла (скрипты calc/alsched/Deck/benchrun, `tk*` закрытых тикетов,
`tests/` старых конвейеров, `archive/`, `gate/`, `shim/`, банки псевдотеста). Снятое целиком лежит под тегом
`archive/tools-compute-2026-11` (= `master` до среза), коммит среза — в ветке `tk508-onion`, в `master` не влит.

**Оставлено и почему:** `statarb/` (код П-40 и стат-арбитража, правит ветка `tk500-p40core`), `build-pgo.sh`
(на него ссылается `tools/p40core`, В-32), `calibrate.sh` (правило В-178 в CLAUDE.md), `_env.sh`, `_lib/`.

**Сверка, что П-40 не задет:** `git grep tools/compute/` по `tk500-p40core` в `tools/p40core`, `tools/compute/statarb`,
`src/statarb`, `docs/research/P-40*` — ссылки только на `statarb/` и `build-pgo.sh`; `sys.path`-импорты скриптов
`statarb/` — внутри `statarb/`; `git diff master...tk500-p40core -- tools/compute` вне `statarb/` — только новые
`tests/test_statarb_*` (на `master` их нет, конфликта нет); `git merge-tree tk508-onion` с `tk493-xdl9` и
`tk500-p40core` — без конфликтов. CI (`.github/workflows`) `tools/compute` не запускает; в `src/` — только
упоминания в комментариях.

**Не сделано в слое 2 (дальше):** `docs/COMMANDS.md` — разделы calc/alsched/коллектор/форма в `docs/archive/`;
CLAUDE.md «(прежнее) СОСТОЯНИЕ» и абзацы про calc/Deck — правит только CEO (передано комментарием); слияние
`tk508-onion` в `master` — после сверки CEO (В-39).

## Слой 3 — старый код, ветки, грязное дерево (начат 09.11 ~03:27)

**Снято:** 17 worktree — `.claude/worktrees/wf_*` ×11 и `agent-a6d0…` (ветки `worktree-*` удалены), `../alpha-tk306`,
`alpha-tk363`, `alpha-tk366`, `alpha-tk482-green`, `alpha-tk482-red` (ветки `tk306`, `tk363-bank-d20`, `tk482-e010-red`
не удалены — к сверке веток). Все были чистые (`git status --porcelain` пусто); голова каждого — тег `archive/wt-<имя>`.

**Оставлено:** `../alpha-tk482-prev2` — правка `src/statarb/cli.rs` не закоммичена, разобрать; `.claude/worktrees/TK-484`,
`TK-486` — тикеты не закрыты (TK-486 — у CEO); `TK-493`, `TK-500` — П-40; `TK-508` — эта уборка.

**Дальше:** локальные ветки (невлитые — тег + `-d`), грязное дерево основного каталога (ветка `tk479-statarb`, ~300 строк
`git status`), список веток origin — CEO на согласие владельца.

### Слой 3, шаг 2 (09.11 03:30–03:40)

**Локальные ветки сняты (12):** `pr83`, `tk284`, `tk296`, `tk306`, `tk316`, `tk341b`, `tk347`, `tk359`, `tk363-bank-d20`,
`tk482-e010-red`, `tk484-pipeline`, `tk487-convert` — тикеты stopped/done, ни одна не предок `tk493-xdl9`/`tk500-p40core`;
перед `-D` голова = тег (`archive/<имя>`; для `tk347` старый тег на другом коммите — новый `archive/tk347-branch`).
Worktree `TK-484` (чистый, TK-484 done) и `../alpha-tk482-prev2` (detached 70599685, однострочная правка `read_funding`
— гейт E-010 снят В-111; в живых ветках код переписан в `parse_funding`) сняты; правка — `docs/archive/cleanup-2026-11-09/alpha-tk482-prev2-70599685.patch`, голова — `archive/wt-alpha-tk482-prev2`.

**Оставлены:** `master`, `tk479-statarb` (основная копия, сюда пишет диспетчер), `tk493-xdl9`, `tk500-p40core` (П-40),
`tk486-speed` (TK-486 у CEO; ветка — предок обеих веток П-40), `tk508-onion`.

**Ветки origin (80):** список `docs/archive/cleanup-2026-11-09/origin-branches-2026-11-09.txt` (имя · влита в origin/master ·
тег). 48 влиты (43 без тега — коммиты есть в master), 32 не влиты (5 с тегом). Удаление на origin — CEO с согласием
владельца; не влитые без тега перед удалением — `git fetch` + тег `archive/<имя>`.

**Грязное основное дерево (`tk479-statarb`):**
- откатаны 11 файлов: `src/commands/lob/{mod,parts,replay,seconds,tests}.rs`, `touches/minute_flow.rs` (стены/посекундный
  срез — закрыты В-56, «без посекундных»), `tools/compute/tk366w/tk387/tk402/tk410*` (закрытые тикеты); diff до отката —
  `docs/archive/cleanup-2026-11-09/main-tree-src-tools-66dcefd2.patch` (627 строк);
- 71 неотслеживаемый `tools/compute/tk2xx–tk4xx*` + `tk426-windows.csv` (пампы, В-108) упакованы в
  `main-tree-untracked-tools.tgz` (72 файла); **удалить их хук не дал** (`git clean -f` в основной копии — только CEO);
- оставлено: `tools/compute/statarb/calc.sh` (M, стат-арбитраж), `CLAUDE.md`, `EXPERIMENTS.md`, `JOURNAL.md` (пишет CEO),
  `.claude/**` (живое состояние диспетчера: 119 неотслеживаемых тикетов, ошибки, цели), неотслеживаемые вердикты
  `docs/research/reviews/tk38x–tk487*`, `docs/findings/{local-cleanup,statarb-xdl9-plan,tk481-merge}`, `P-33` — планка
  Судьи, их закоммитить, а не удалять; `Python/` (166 МБ, Python 3.15) — чей, не выяснено, не трогал.

**CEO:** (1) в основной копии `git clean -f -- tools/compute/tk* tk426-windows.csv` (архив выше); (2) закоммитить
`.claude/tickets|errors|goals` и вердикты docs (или решить, что они живут вне git); (3) `Python/` — чей; (4) origin — список.

## П-40 не задет

`git worktree list`: `TK-493 a89d2e80 [tk493-xdl9]`, `TK-500 [tk500-p40core]` на месте; статусы TK-495/496/501 waiting,
TK-500 in_progress, TK-499/504 done — без изменений.
