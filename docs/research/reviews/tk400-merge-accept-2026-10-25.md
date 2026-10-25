# TK-400 — слияние tk399 в master — принято (Судья, 25.10 ~07:50)

| пункт | да/нет | на чём проверено |
|---|---|---|
| tk399 влит в master | да | `git merge-base --is-ancestor dbb85521 master` — ok; master c0d0b23c поверх |
| тесты на чистом дереве | да | calc `/data/sched/rc/1025042101256` = 0; лог: `test result: ok. 1187 passed; 0 failed; 7 ignored` |
| clippy -D warnings / fmt | да | сдача инженера 07:18, rc 0 по отдельности |
| vps-check all зелёный | нет, не по вине слияния | красный только check-sprawl (338>83 на master до слияния) — тот же красный на master |

Хвосты (ролям не сделать / решение CEO): push master (origin отстаёт на 348), тег `archive/tk399` + удаление ветки,
check-sprawl красный на master.
