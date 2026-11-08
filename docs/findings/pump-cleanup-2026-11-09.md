# Пампы G-017: удаление всего связанного (TK-503, В-108) — 09.11.2026

Слово владельца (В-108, 09.11 00:18): «все что связано с пампами мы удаляем и потом заново сделаем если надо».
Всё из git восстановимо из тегов ниже (`git show <тег>:<путь>`, `git checkout <тег> -- <путь>`); теги `archive/*` не удалять.

## 1. София (13.140.29.171)

- `pumpdl-manifest.service` (transient, `/opt/alpha-pump-dl/bin/manifest.sh`, был active running) — остановлен; других юнитов `pump*`,
  таймеров и строк crontab нет (`systemctl list-units --all 'pump*'` → 0 units после остановки; процессов `alpha-pump` нет).
- `/opt/alpha-pump-dl` (4,2 ГБ: `out` 4,2 ГБ, `bin`, `lists`, `log`, `test`) — удалён; свободно на `/` было 22 ГБ → стало 26 ГБ.

## 2. Ветки git

| ветка | голова | тег | worktree |
|---|---|---|---|
| `tk490-pumpdata` | 196e3813 | `archive/tk490-pumpdata` | `.claude/worktrees/TK-490` (чистый) — снят |
| `tk492-pumponset` | 3fc57a5d | `archive/tk492-pumponset` | `.claude/worktrees/TK-492` (чистый) — снят |

Обе ветки удалены локально; в origin их не было (`git ls-remote --heads origin` — ни одной `pump`/`tk490`/`tk492`).
Голова `tk492-pumponset` — 3fc57a5d (позже @3660f1c4 из CLAUDE.md, коммит «XdAcc … Result вместо panic!»), ядро `src/pumponset/` — только там, в `master` его не было.

## 3. Рабочее дерево (ветка `tk479-statarb`), тег `archive/pump-g017-2026-11` = f783c3cd (до удаления)

Удалено одним коммитом:
- `docs/findings/pump-*` (кроме `pump-exclude-2026-09-23.csv`, см. ниже), папки `pump-review-png-2026-10-28/`, `pump-v10-png-2026-10-28/`, `pump-v11-png-2026-10-28/`;
- `docs/archive/pump-*`, `docs/archive/P-TK433-pump-v9-2026-10-28.md`;
- протоколы `docs/research/P-TK419/420/421/426/433-pump-*`, `docs/research/P-34-pump-onset-binance.md`, `docs/research/P-34-params.json`;
- проверки `docs/research/reviews/tk433-pump-end-plan-2026-10-28.md`, `docs/research/reviews/tk491-*` (пять файлов);
- материалы владельца `docs/research/sources/pump-*-owner-notes-*` (пять файлов);
- скрипты счёта пампов `tools/compute/tk413…tk449*` (тикеты TK-413…TK-449 — все про пампы/дампы, G-016/G-017) и БД признаков `tools/g017db/` (TK-449).
  Код (`src/`, `tools/`, `.github/`, хуки) на удалённые скрипты не ссылается (`git grep` — пусто).

Итого в коммите: 478 файлов (240 доков + 238 скриптов).

**Оставлено намеренно:**
- `docs/findings/pump-exclude-2026-09-23.csv` — список «прокиды» стратегии стен (В-92 SETTLED), единственный источник для
  `docs/findings/strategy-config-2026-09-27.md:75,208`; к G-017 не относится.
- `tools/compute/tk440-cube.sh`, `tk442-forward.py`, `tk447-features.py` — пампы, но в рабочем дереве у них незакоммиченные правки
  (+142/−16 строк), в теги не попали; удалить — потерять правки без копии. Решает CEO (удалить или сперва закоммитить в архив).
- Свечи/данные TK-411/412 (G-016, не пампы), Binance-свечи TK-450/453 (G-018), всё П-40 (`tk493-xdl9`), данные стаканов — не трогали.

**Ссылки:** на удалённые файлы ещё ссылаются тексты (тикеты TK-4xx, `docs/plan/EXPERIMENTS.md`, `docs/research/JOURNAL.md`,
`CLAUDE.md` «СОСТОЯНИЕ», старые отчёты) — это история; путь остаётся читаемым через тег `archive/pump-g017-2026-11`.
`CLAUDE.md`, `EXPERIMENTS.md`, `JOURNAL.md` сейчас с чужими незакоммиченными правками — не правил (CLAUDE.md пишет только CEO).

## 4. Тикеты

TK-490 (engineer, blocked), TK-491 (researcher, waiting), TK-492 (engineer, blocked) — записи со ссылкой на В-108 и этот отчёт;
закрыть статусом — CEO (закрытие чужих тикетов не в праве Судьи).

## 5. Steam Deck

Недоступен и не включается (В-104): `~/alpha-pump/` (бинарь `alpha-pumponset-v11`, кэш `pmc2/`, `out/`) и юниты `pump-*` там **не тронуты** —
удалить, когда Deck будет в сети и будет слово включить.
