# TK-337 — приёмка уборки локальной машины (Судья, 21.10.2026)

| пункт решения | да/нет | на чём проверено |
|---|---|---|
| worktree A/B/C удалены, D (9) оставлены | да | `git worktree list` = 10 (основное + 9) |
| B: незакоммиченное сохранено | да | `data/tk337/dirty-{tk231,tk268,alpha-tk223,alpha-tk300}.patch/.status` + `dirty-alpha-tk223-untracked/` |
| 8 веток без копии — тег archive на origin до удаления | да | `git ls-remote --tags origin`: 8/8 `archive/<имя>` есть |
| удалённые ветки не теряют коммитов | да | 98/98 хешей `data/tk337/branches-deleted.txt` содержатся в `refs/remotes/origin/*` или `refs/tags/archive/*` (`git for-each-ref --contains`) |
| alpha.rar оставлен | да | `../alpha.rar` 2 995 335 296 байт на месте |
| target-ci, fuzz/target, alpha-tk049ps удалены | да | `ls` — нет |
| отчёт до/после | да | `docs/findings/local-cleanup-2026-10-21.md` §5: C: 30→36 ГБ |

Итог: принято. Хвост: сессии коллектора 12–14.09 — единственная копия в alpha.rar (см. решение) — CEO.
