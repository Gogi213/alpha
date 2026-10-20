# Уборка локальной машины — инвентарь (TK-337), 21.10.2026

Статус: **выполнено 21.10 по решению Судьи** (`docs/research/reviews/tk337-cleanup-decision-2026-10-21.md`); итог — раздел 5. Диск C: 476 ГБ, занято 446, свободно 30 (до).
Сырьё: `data/tk337/` (inventory.tsv, inv4.tsv, br6.tsv). Слияние считалось против **origin/master** (локальный `master` отстаёт на 299 коммитов — ему верить нельзя).

## 1. Worktree (git worktree list: 44 шт. кроме основного; суммарно ≈2,8 ГБ)

| группа | размер МБ | предложение |
|---|---|---|
| A. влито в origin/master, чисто: tk048-base(39), base(41), head(42), bis-4610ea34(41), bis-93287fde(41), bis-c64c886d(41), kt9(63), tk153(46), tk200m(58), tk255(70), tk266(70), tk272(143), tk274(84), tk278(88), alpha-m297(64), alpha-tk205doc(52), alpha-tk206(52), alpha-tk291(65), alpha-tk299(65), alpha-tk300b(66), alpha-tk300c(66), alpha-tk303(98), alpha-tk304(66), alpha-tk309(82), alpha-tk311(67), alpha-tk312(57), alpha-tk319(57), alpha-wt-tk209(51) | 1775 | **удалить** `git worktree remove` (ветку не трогать, см. п.2) |
| B. влито, но есть незакоммиченное: tk231(63), tk268(79), alpha-tk223(50), alpha-tk300(65) | 257 | сохранить `git diff` в `data/tk337/dirty-*.patch` → удалить; решение Судьи (tk231: plan_cells.jsonl, tk268: tk293-registry-union.py, tk223: alsched.py+sched_cfg.py+t_tmp.py, tk300: src/lob/levels.rs) |
| C. не влито, тикет done, ветка запушена на origin: tk279(80), alpha-tk210(58), alpha-tk312s(66) | 204 | удалить worktree (ветка и origin остаются) |
| D. тикеты waiting (живые): tk284(60), tk289(76), tk296(64), tk316(66), tk332m(70), tk332p(70), tk334(70), alpha-tk306(66), alpha-tk315(63) | 605 | **оставить** |

Основное дерево (`tk244`, 275 изменённых/неотслеженных) — не трогаю. Каталоги `alpha-tk049ps` (42 МБ), `alpha-wt162` — копии без записи в worktree list → кандидаты на удаление (Судья).

## 2. Локальные ветки (109)

- **74 влиты в origin/master** → удалить локально (потеря нулевая). Ветки, занятые worktree, — после его удаления.
- **23 не влиты, но запушены** (есть на origin) → удалить локально можно.
- **2 с тегом archive/*** (tk175-abc, tk251) → удалить.
- **8 только локально, не влиты, без тега**: tk163-cut, tk200-f2, tk223b, tk229-pyr-group, tk257, tk259, tk260, tk300b — **единственные копии**. Предлагаю: поставить `archive/<имя>` (теги не удалять) и тогда удалить ветку; либо оставить. Решение Судьи.
- `master` (устарел на 299) и `tk244` (текущая) — оставить.

## 3. Вне worktree и вне Git

| что | МБ | предложение |
|---|---|---|
| `C:/visual projects/alpha.rar` (16.09, архив старого дерева) | 2857 | **кандидат №1**; единственная ли копия чего-либо — решает Судья/владелец |
| `~/.claude/projects/C--visual-projects-alpha` (2544 транскрипта сессий, 06.09–21.10) | 2513 | история сессий, не «наше проекта»; удаление необратимо (`/resume`, конспекты ролей) — **оставить**, пока Судья/владелец не скажут; можно старше N дней |
| `target-ci/` (сборочный кэш cargo; сборки на этой машине запрещены) | 1537 | **удалить** |
| `fuzz/target` (сборка fuzz, gitignored) | 335 | **удалить** |
| Temp: 308 каталогов/файлов tk*/alpha* (tk201old 296, tk048w 84, tk279-head 75, tk127… 61, tk272j 60 …) | 1367 | **удалить** (после `git worktree prune`; 3 worktree tk048* — группа A) |
| `data/` проекта (gitignored; tk298 55, tk237 42, tk305 41 …) | ≈200 | **оставить**, пока Судья не пометит; ссылки живых тикетов не проверял |
| `.claude/dispatcher/` (dispatch.run.log 30, runs 14) | 49 | оставить (рабочие файлы диспетчера) |
| `tmp91`, `tmp-tk*`, `.tmp-*`, `.pytest_cache` и пр. | <20 | можно удалить, мелочь |

## 4. Итого к освобождению (если все «удалить» одобрены)
worktree A+C ≈ 1,98 ГБ (+B 0,26) · target-ci 1,5 · fuzz/target 0,33 · Temp 1,37 · alpha.rar 2,86 (по решению) ⇒ **≈ 7,5–8,3 ГБ**; транскрипты сессий (+2,5 ГБ) — только по слову владельца.

Шаги после решения: patch-файлы для B → `git worktree remove` (A,B,C) → `git worktree prune` → теги archive/* для «единственных» веток → `git branch -d/-D` → удаление каталогов → замер `df`, дописать «после» сюда.

## 5. После уборки (21.10, инженер)

**Диск C: свободно 30 → 36 ГБ** (занято 446 → 440; освобождено ≈6 ГБ: worktree ≈2,2 ГБ + каталоги 3,2 ГБ + ветки/объекты не считались).

- **Worktree удалено 35** (`git worktree remove`): A 28, B 4 (tk231, tk268, alpha-tk223, alpha-tk300), C 3 (tk279, alpha-tk210, alpha-tk312s; ветки на origin остались). Перед удалением: каждый A — чист и HEAD ⊂ origin/master, C — чист и HEAD на origin, B — незакоммиченное сохранено в `data/tk337/dirty-<имя>.patch|.status` (+ `dirty-alpha-tk223-untracked/` — t_tmp.py). tk231 и alpha-tk300: `git diff HEAD` пуст (правки только stat/CRLF), содержимого терять нечего. Осталось 10 (основное + 9 живых D: tk284, tk289, tk296, tk316, tk332m, tk332p, tk334, alpha-tk306, alpha-tk315).
- **Каталоги удалены:** `target-ci` 1537 МБ, `fuzz/target` 407 МБ, `tmp91` 14, `alpha-tk049ps` 42, `alpha-wt162` 0, `.pytest_cache`, `tmp-tk*`, `.tmp-*`; Temp: 304 записи `tk*`/`alpha*` (≈1,2 ГБ, в т.ч. 2 снимка-«клона» без коммитов tk127-…, tk279-head). Список — `data/tk337/clean3.log`.
- **Не тронуто:** Temp `tk100-g4/g5/g7` — worktree другого репозитория (`C:/visual projects/rpv`, плагин), не alpha; `alpha.rar` (единственная копия сессий 12–14.09); `data/` проекта; транскрипты; `.claude/dispatcher/`; основное дерево (его `git status` не менялся); `tmp/` (ignored, 0 МБ, в решении не значился).
- **Теги `archive/*` на origin (8, проверены `git ls-remote --tags`):** archive/tk163-cut 4be37911, archive/tk200-f2 864e1cf2, archive/tk223b ebe54366, archive/tk229-pyr-group 27af98f7, archive/tk257 f491425a, archive/tk259 f6c40c3b, archive/tk260 5d02016d, archive/tk300b c8577143. Список — `data/tk337/tags-pushed.txt`.
- **Ветки:** удалено локально 98, из них 8 «единственных» — после тега (`-D` после проверки: tip ⊂ origin/master, или содержится в ветке origin, или в теге archive/*; список с хешами — `data/tk337/branches-deleted.txt`, восстановление `git branch <имя> <хеш>`). Остались: `master` (устарел), `tk244` (текущая) и 9 веток живых worktree.
