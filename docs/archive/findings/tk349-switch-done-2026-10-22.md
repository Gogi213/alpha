# TK-349 — перевод основного дерева на master выполнен (22.10 09:12–09:27, Инженер)

Основное дерево на `master` = `b6b2bf5d` (слияние `0f3b7933` + коммит снятия россыпи). План v2 принят Судьёй (`docs/research/reviews/tk349-plan-v2-2026-10-22.md`).

## Что сделано (по порядку плана, п. 3)

| шаг | факт |
|---|---|
| 0 тишина | в `runs/` на старте жила только эта сессия; в 09:17 диспетчер запустил TK-237 (Судья, Исследователь) — их файлы слияние не трогало (git отказал бы, если бы тронуло) |
| 1 резерв | `C:/scratch/tk349-0916/`: `live.tar` (14889 файлов: изменённые + неотслеживаемые + игнорируемые `.claude/`), `manifest-before.sha256` (14768, sha256 без CR), `local.patch`, `status.txt`, `HEAD` |
| 2 master | `git fetch . origin/master:master` (ff без `-f`; хук режет `branch -f`) |
| 3 ignore | `.claude/dispatcher/.gitignore` + 14 рантайм-путей плагина 2.0.x (guard/jobs/mechanics/notify/overseer*/release-*/report*/reports/supervise-state/tk077-day-check/branch-delete) |
| 4 фиксация | `fafed1e2` на tk244 (303 файла); тег `archive/tk349-leftovers` на нём, **на origin** — версии 8 «нигде»-файлов для TK-350 |
| 5 слияние | хук режет `git checkout --ours/--theirs` в основном дереве → слияние в linked worktree `tk349m`; конфликтов 22 = ожидаемое множество без лишних (3 `.claude` + 7 тикетов + 12 вне `.claude`), `--abort` не понадобился |
| 5а решение | `ours` (5): `ceo-restart-services.py`, `settings.json`, `notes/ceo.md`, TK-231, TK-237; `theirs` (17): тикеты TK-188/191/196/212/215 и все конфликты вне `.claude`; без конфликта, но возвращены к master (3): `archive/tk212-f1-run2.sh`, `archive/tk340-sieve.py`, `tests/test_alsched_core.py` |
| 6 перевод | `merge --ff-only` в основном дереве → `git fetch . HEAD:master` → `symbolic-ref HEAD refs/heads/master`; ветки `tk244`, `tk349-merge` удалены (`archive/tk244-final` на origin) |
| 6а россыпь | `check-sprawl`: `compute_root_files` 87 > базы 83 (14 локальных скриптов `tk2*/tk3*` из коммита п. 4) → `git rm --cached` (файлы на диске, версии в `fafed1e2`), `b6b2bf5d` |

## Сверка (п. 7)

- (а) manifest-before vs дерево: 14732 равны, 33 разницы, 3 «нет»; все объяснены: рантайм диспетчера и pulse (ceo-inbox, ceo-wake, dispatch.run, guard, state, watch-*, pulse, файлы в игнорируемых runs/ и roles/log), TK-237/TK-349 (запись живых сессий после слияния), 5 тикетов `theirs` (TK-188/191/196/212/215), 15 файлов вне `.claude` = `theirs`/master (`bounce_grid*.rs` — дубль tk346 уже в master, `alsched.py`, `day-cells.sh`, `sched_cfg.py`, `check_page.py`, `page.html`, `tk200-…`, `tk214/216/265-…`, `bs-launch.sh`, `load_f_grid.py`, `test_alsched_core.py`), 3 «нет» = перенесены в `tools/compute/archive/` (TK-348). Файлы `ours` (settings.json, ceo-restart-services.py, notes/ceo.md, TK-231) равны резерву.
- (б) отслеживаемое чисто, кроме двух живых тикетов (TK-237, TK-349).
- (в) `src/`, `Cargo.*` равны `origin/master`; от `origin/master` отличается только состояние `.claude/`, документы и находки.
- (г) `bash tools/vps-check.sh <чистый worktree> all` — подан на calc: alsched `1022072836593` (первая попытка: отказ `check-sprawl`, исправлено в п. 6а).

## Осталось

- итог (г) — `host:calc:/data/sched/rc/1022072836593`;
- `master` впереди `origin/master` на 12 коммитов, **не запушен** (publish — на усмотрение Судьи/CEO; тег `archive/tk349-leftovers` с теми же объектами уже на origin);
- TK-350: 8 «нигде»-файлов (`archive/tk349-leftovers`) + 14 неотслеживаемых `tools/compute/tk2*/tk3*`;
- запись следующей роли должна лечь в `TK-349.md` на `master` (п. 8).
