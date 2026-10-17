# TK-283 @275164c — сверенные числа

- 39 тестов OK на голове; до фикса 1 FAIL (test_long_job_waits_not_shrunk).
- ps на calc: 16/16 по заявкам, loadavg 16,45.
- ELASTIC_SHRINK_S = 5400 в sched_cfg.py; тест: mrt 1800 x 3 = 5400 встаёт, 1801 ждёт, 18000 ждёт.
