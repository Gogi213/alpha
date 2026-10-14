# TK-240 — замок тяжёлых ssh на calc: проверка Судьи (14.10)

Предмет: `.claude/hooks/heavy_ssh_guard.py` + `tools/compute/tests/test_heavy_ssh_guard.py` (коммит ae725299, ветка tk237).

| пункт договора | да/нет | на чём проверено |
|---|---|---|
| логика heavy_guard.py 1.8.48 перенесена | да | diff с плагином 1.8.48: классификация `heavy_label`/tar/python тождественна (правки — комментарии и константы вместо env); разбор ssh/обёрток/heredoc/подстановок — новый, взамен `delete_guard.scan_one` |
| параметры прежние (хост, ALLOW, подсказка) | да | 89.163.242.211 + алиас calc; ALLOW `^python3 /data/sched/alsched[.]py (submit\|ps\|cancel\|wave\|stand\|reprio\|thaw)( \|$)`; HINT дословно |
| тест: find /data → отказ; systemd-run/benchrun/alsched submit → пропуск | да | pytest 13/13 локально; 17 своих проб через stdin хука: отказ — find, du по IP, ls -R, bash -s heredoc, nohup sudo find, $(du), xargs sha256sum, python3 скрипт, tar c, alsched run, незакрытая кавычка; пропуск — systemd-run, benchrun stand, alsched submit, tail/cat, python3 -c короткий, ssh vps |
| хук зарегистрирован в .claude/settings.json | **нет (вне роли)** | в PreToolUse только bare_systemd_run.py — замок пока не действует; правка settings.json — CEO |
| CLAUDE.md «Грабли» → фактический хук | **нет (вне роли)** | всё ещё `.claude/hooks/bench_guard.py`; правка — CEO |

Вердикт: работа ролей принята. Замок заработает после двух правок CEO (шаги — лог TK-240, запись инженера 19:12:14).
