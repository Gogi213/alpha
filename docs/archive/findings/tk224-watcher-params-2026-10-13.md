# TK-224 — сторож calc без форка (13.10)

PR #93 плагина (1.8.39, голова d45f266), CI 12/12 SUCCESS. Выкладка на calc 01:01 CEST (файлы + drop-in + daemon-reload + restart одним шагом, шина не слепла).

| критерий | факт |
|---|---|
| watcher.py на calc = выпуск | sha256 aefccacf…12df = `git show d45f266:.claude/bus/watcher.py` |
| busclient.py на calc = выпуск | sha256 7a436381…a210 = git d45f266 |
| настройки только в юните | `/etc/systemd/system/alpha-bus-watcher.service.d/rpv.conf`: Environment RPV_BUS_URL, RPV_BUS_TOKEN_FILE; ExecStart с `--patterns tk* alpha-* --exclude alpha-bus --progress-glob /data/progress/*.json --watch-file /data/progress/watch.list` |
| стенд | `tk224d-job` (sleep 8): БД шины seq 17941 `юнит.запущен`, seq 17942 `юнит.остановлен` (invocation 90de33d6…), `/root/.rpv` не создан |
| повторная выкладка не меняет юнит | `systemctl cat alpha-bus-watcher | sha256sum` до и после повторной записи файлов: 46cede8e… = 46cede8e…; sha файлов прежние |

Откат: `*.py.bak-tk224` рядом (форк e7da46b+6 строк, без drop-in не запустится с прежними константами — drop-in удалить и вернуть .bak).
