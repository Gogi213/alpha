# TK-292 PR #54 @5e5c95e — вернуть (17.10 17:32 GMT+4)

| пункт договора | да/нет | на чём проверено |
|---|---|---|
| причина найдена (вытеснение build → ядра вытесненного ушли очереди) | да | дифф alsched.py: preempt_held(), free/room/OVERBOOK_MAX |
| тест падает до правки | да | test_alsched_core.py с alsched.py из master: FAIL test_preempted_cores_not_given_to_queue «27 not less than or equal to 16» |
| тест зелёный после | да | голова 5e5c95e: Ran 41 tests, OK |
| [вживую] ps: сумма заявок ≤ 16, load ≈ 15 | нет | calc 15:31 CEST: md5 /data/sched/alsched.py = dd0f2914… = master (не голова bf344de7…); ps «занято 27/16 по заявкам; в очереди 4»; loadavg 27.25 25.85 22.22 |

Вернуть: правка не выложена на calc, живой критерий не выполнен. Нужно: выложить alsched.py на calc (демон, идущие не убивать),
показать ps «≤ 16» и load ≈ 15 при ≥ 2 больших в очереди, и что демон не пускает новых, пока идущие 27 не опустятся ≤ 16.
