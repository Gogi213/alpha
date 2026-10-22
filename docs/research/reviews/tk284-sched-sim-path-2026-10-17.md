# TK-284 — путь к sched_sim в тестах alsched (PR #51 @c7adb63) — принято

| пункт | да/нет | на чём проверено |
|---|---|---|
| правка только тестов, 2 строки | да | `git diff --stat origin/master...c7adb63`: test_alsched_core.py +1, test_alsched_fail.py +1; база — origin/master |
| до правки на чистой копии красно | да | `git archive origin/master tools/compute`, без PYTHONPATH, `unittest discover -p test_alsched_*.py`: Ran 8, errors=3, ModuleNotFoundError sched_sim ×3 |
| после правки зелено | да | та же команда на c7adb63: Ran 31 tests, OK |
| боевой код не тронут | да | alsched.py, sched_cfg.py, archive/sched_sim.py в диффе нет |

Хвостов нет. CI: Quality-линты зелёные; Rust-джобы к правке Python не относятся.
