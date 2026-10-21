# TK-341 — сторож delete.ok (второй срок) — Судья 21.10 ~13:00 GMT+4

Не «долго», а упало: `/data/tk0341/delete.fail` = `FAIL refs left` (09:14 CEST = 11:14 GMT+4), юнит не живёт, `/data/tk0279` цел, df /data 289G своб.

| что | состояние | на чём проверено |
|---|---|---|
| f2-psim 1021090111742 | rc 0 | /data/sched/rc/1021090111742 |
| правка ссылок f245 / f2-psim / tk308 run.sh / p17f2.sh | сделана, tk0279 = 0; оригиналы в /data/tk0341/orig2 | grep -c, ls orig2 |
| keep (days9, p17a, p17b, p17b-day.sh, p17g81, p17g98, pgo.ok, tk308-sieve.py) | на месте | ls /data/tk0341/keep |
| остаток grep (left2.txt) | 1 строка: docstring `/data/tk0340/tk340-sieve.py:2` («closes F1 /data/tk0279/p17*») — текст, не путь чтения | left2.txt, grep -n по файлу |
| реестр /data/launch/registry/runs.jsonl | 4 строки с tk0279: 4 `result_path` (1 битый `C:/Program Files/Git/data/tk0279/…`) + `what` | grep -c / grep -o |

Вернуть инженеру:
1. docstring tk340-sieve.py — заменить путь на /data/tk0341/keep/p17* (или исключить строку из проверки с пометкой).
2. Критерий тикета включает docs/registry: 4 `result_path` в runs.jsonl → /data/tk0341/keep/… (тем способом, каким реестр правится — не руками, если есть команда), битый `C:/Program Files/Git/…` — тоже; добавить runs.jsonl в проверку stage2.
3. Перезапуск stage2: НЕ перезаписывать orig2 (там оригиналы до sed) — шаг cp в orig2 пропустить; ожидание rc уже пройдено.
4. Затем rm юнитом, df до/после, и панель Г-135/136 «Отвергнута».
