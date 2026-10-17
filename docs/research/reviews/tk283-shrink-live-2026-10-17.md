# TK-283 PR #49 @275164c — elastic не сжимает многочасовые (CEO 17.10 08:54), вживую

Открыто: ssh calc, 17.10 06:57 CEST (08:57 GMT+4), `python3 /data/sched/alsched.py ps`, `/proc/loadavg`, `/data/sched/jobs/<id>.json`; ~5 с.

- Выкладка = голова: md5 /data/sched/alsched.py dd0f2914…, sched_cfg.py 28b32724… = `git show 275164c:…`.
- ps: «занято 16/16 по заявкам; в очереди 4»; loadavg 16,45 / 15,98 / 15,93.
- Идут: tk268-g82 ядер=12, tk279-g81-probe-tail ядер=4 (elastic, заявлено 4 — не сжатие).
- tk278-g117 прежний (4 ядра, до фикса) снят rc=143; новый tk278-g117 1017065625464 — queued, ядер=12, не на 4.
  Он подан без elastic (elastic=None, max_runtime 151200) — новое правило на нём вживую не проверяется; правило проверено тестом.

Тест на чистой копии (git archive 275164c tools/compute, sched_sim.py из archive/ рядом с tests/):
- голова 275164c: Ran 39, OK;
- alsched.py+sched_cfg.py от bc7d13a с тестами головы: FAIL test_long_job_waits_not_shrunk (1 из 39) — падал до фикса.
