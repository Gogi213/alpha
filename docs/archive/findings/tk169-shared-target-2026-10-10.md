# TK-169: стена vps-check (строка «Finished … target(s) in» этапа тестов), пассивно по логам

Общий target — коммит 3b22e8f2 (10.10 20:06 GMT+4). Новых сборок не гонялось.

| | лог | Finished in |
|---|---|---|
| до | docs/findings/tk065-g0-vps-check-2026-10-08.txt:15 | 1m 44s |
| до | tk065-g0-vps-check-b6d7fd27-2026-10-08.txt:15 | 1m 44s |
| до | tk065-g0-vps-check-f53f2b26-2026-10-08.txt:15 | 1m 41s |
| до | tk065-g0-vps-check-pyrgate-2026-10-08.txt:15 | 1m 43s |
| до | tk147-vps-check-all-2026-10-10.log:15 | 1m 41s |
| до | data/tk150/all.log, all2.log, all3.log | 1m 41s, 1m 40s, 1m 38s |
| до | data/tk151/vps-all.log | 1m 43s |
| после | data/tk161/a2-vps.log:9 | 33,4 с |
| после | data/tk162-vps.log:9 | 34,5 с |
| после | data/tk163/vps-all.log:9 | 29,4 с |

До: 9 логов, 98–104 с (медиана 101 с). После: 3 лога, 29–35 с (медиана 33 с) ≈ ×3. Оговорки: «после» — только этап
сборки тестов (строка Finished), без fmt/clippy; N=3; код разных веток, часть выигрыша — меньший объём кода рефактора.
