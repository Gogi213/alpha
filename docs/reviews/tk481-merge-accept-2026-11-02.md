# TK-481 — приёмка слияния tk479-statarb в master (Судья, 2026-11-02)

Вердикт: **принять**. master e3e4d5aa → b54a5d73 (fast-forward, не пушен).

| пункт заказа | да/нет | на чём проверено |
|---|---|---|
| тесты statarb на calc/VPS | да | лог calc `/data/sched/logs/1102093723143.log`: `test result: ok. 10 passed; 0 failed` по statarb::tests |
| check-sprawl не выше master | да | `git ls-tree` корень tools/compute: e3e4d5aa 741, b54a5d73 741 |
| слияние в master | да | b54a5d73 — голова master; src/Cargo с принятой головы TK-479 e6177d2e не менялись (`git diff` пуст) |
| чужие правки (.claude/*, src/commands/lob/*, tools/compute/tk*) не в коммите | да | `git diff --name-only e3e4d5aa master`: ни одного такого пути; в слиянии только statarb, отчёты TK-476/479 и перенос двух файлов TK-476 в candles/ |

| число | файл:строка |
|---|---|
| 10 passed | docs/findings/tk481-merge-2026-11-02.md:7 |
| 741 = 741 | docs/findings/tk481-merge-2026-11-02.md:8 |
| 3 passed | docs/findings/tk481-merge-2026-11-02.md:10 |

Замечание к CEO (не к исполнителю): на ветке `tk479-statarb` после слияния лежат три коммита TK-480 (протокол П-32,
`docs/research/P-32-statarb.md`), которых нет в master. Перед тегом `archive/tk479-statarb` и удалением ветки их нужно
влить в master (или оставить под тегом), иначе протокол уйдёт из ствола.
