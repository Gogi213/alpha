# TK-376 проход 6 — ключевые числа

Источник — отчёт прохода 6; каждое число сверено с `docs/findings/tk376-gap6-sum.csv` (строки 3, 14–17, 31–33; таблица
сверки — `tk376-gap-pass6-2026-10-23.md`). Ссылки на csv не принимает проверка диспетчера: число после запятой-разделителя
csv не находится (`_has_number`, lookbehind `[\d.,]`).

| число | файл:строка |
|---|---|
| 97 | docs/findings/tk376-gap6-2026-10-23.md:6 |
| 92 | docs/findings/tk376-gap6-2026-10-23.md:6 |
| 12,8 | docs/findings/tk376-gap6-2026-10-23.md:7 |
| 18,1 | docs/findings/tk376-gap6-2026-10-23.md:7 |
| -38,4 | docs/findings/tk376-gap6-2026-10-23.md:7 |
| 14,25 | docs/findings/tk376-gap6-2026-10-23.md:14 |
| 36,08 | docs/findings/tk376-gap6-2026-10-23.md:14 |
| 40,79 | docs/findings/tk376-gap6-2026-10-23.md:15 |
| 698,95 | docs/findings/tk376-gap6-2026-10-23.md:15 |
