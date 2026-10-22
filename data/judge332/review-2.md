# TK-332 — проверка Судьи 20.10 ~23:50 — ВЕРНУТЬ (повтор, PR #76 @38952640)

| пункт заказа | да/нет | на чём проверено |
|---|---|---|
| сверка шагов лота (не ADA/ENA), проба §2 | да | findings §2 = calc /data/tk0328/lotprobe/*/report.txt (rc 1020213921382 = 0): строки 23951/7877/7494/12695, SUI dropped 5, BNB traded_lots 2/55, tape_with 111/4737 = 2,3 % |
| C: сдвиг в touches-project | да | TK-333 принят, PR #77 слит в tk332-merge (7514db3c) |
| П-22 калибровка calibrate.sh (В-178) | нет | findings §3: «Не сделано» |
| заявка A/B 108 хеш×мес | нет | findings §3: «Не сделано»; заявки в alsched нет |
| слияние tk328 в master | PR #76 OPEN MERGEABLE | gh |

Что сделать: calibrate.sh на срезе, затем заявки A (S0 фьюч) и B (SPOT_EPOCH day-cells) и C (сдвиг TK-333) в alsched; сдача — id заявок + wait на их rc. Ядра заняты — заявку подать в очередь, не ждать свободных.
