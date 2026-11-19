"""TK-613 / TK-631: проверка сборщика на подставных каталогах (запуск: python tools/p42/test_tk613.py). Ничего не пишет вне временной папки."""
import json
import os
import sys
import tempfile
import time

root = tempfile.mkdtemp()
os.environ["TK613_DATA"] = root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk613_collect as c  # noqa: E402

c.rbuild_state = lambda: (False, [])
SOFIA = {"ok": True, "load": [0.5, 0.4, 0.3], "cpus": 4, "disk": {"free_gb": 31.2, "total_gb": 80.0, "used_pct": 61.0, "guard_gb": 23}}


def put(rel, txt):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(txt)
    return p


now = time.time()

# 1. ничего нет — не падает: нет now.json → ok=False, юниты пусты, хост на месте; старых задач в выводе нет
d = c.collect(now, units=[], sofia=SOFIA)
json.dumps(d, allow_nan=False)
assert d["now"] == {"ok": False, "err": "файл now.json не найден или не читается", "pushed_ts": None, "threads": []}, d["now"]
assert d["units"] == [] and d["units_ok"] is True and "tasks" not in d
assert d["host"]["sofia"]["disk"]["free_gb"] == 31.2 and d["host"]["builds"]["ok"] and "сборок нет" in d["host"]["builds"]["text"]
assert d["host"]["cpus"] and d["host"]["disk"]["total_gb"] > 0

# 2. now.json: русское имя по TK из таблицы, иначе тема словами; роль по-русски; мусорные строки пропускаются
put("pages/ml/data/now.json", json.dumps({"generated_ts": now - 30, "threads": [
    {"id": "t1", "state": "run", "tk": "TK-631", "topic": "progress-now", "role": "исп", "version": 1, "started_ts": now - 600, "updated_ts": now - 5, "title": "x"},
    {"id": "t2", "state": "done", "tk": "TK-700", "topic": "new-topic-here", "role": "пров", "version": 2, "started_ts": now - 7200, "updated_ts": now - 900, "title": "y"},
    {"id": "t3", "state": "fail", "tk": None, "topic": None, "role": None, "version": None, "started_ts": None, "updated_ts": None, "title": "не по формату"},
    {"id": "t4", "state": "weird", "tk": "TK-1", "topic": "skip", "role": "исп"}, "мусор"]}))
put("p42-tk613/tk_names.json", json.dumps({"TK-631": "Вкладка «Прогресс»: что идёт сейчас"}))
nb = c.collect(now, units=[], sofia=SOFIA)["now"]
assert nb["ok"] and nb["pushed_ts"] == now - 30 and [t["id"] for t in nb["threads"]] == ["t1", "t2", "t3"], nb
t1, t2, t3 = nb["threads"]
assert t1["name"] == "Вкладка «Прогресс»: что идёт сейчас" and t1["named"] and t1["role"] == "исполнитель" and t1["state"] == "run"
assert t2["name"] == "new topic here" and not t2["named"] and t2["role"] == "проверяющий" and t2["version"] == 2
assert t3["name"] == "не по формату" and t3["tk"] is None and t3["state"] == "fail"
# битый json / не тот формат — «ok: False», не падение
put("pages/ml/data/now.json", "{oops")
assert c.collect(now, units=[], sofia=SOFIA)["now"]["ok"] is False
put("pages/ml/data/now.json", json.dumps({"threads": "нет"}))
assert c.collect(now, units=[], sofia=SOFIA)["now"]["ok"] is False

# 3. юниты tk6*: разбор «systemctl show»; свой сборщик и таймеры не в счёте; завершённые — только свежие
SHOW = """Id=tk631-run.service
ActiveState=active
Result=success
ExecMainStatus=0
ActiveEnterTimestamp=@{a}
ActiveExitTimestamp=

Id=tk630-job.service
ActiveState=failed
Result=exit-code
ExecMainStatus=3
ActiveEnterTimestamp=@{b}
ActiveExitTimestamp=@{e}

Id=tk629-done.service
ActiveState=inactive
Result=success
ExecMainStatus=0
ActiveEnterTimestamp=@{b}
ActiveExitTimestamp=@{e}

Id=tk628-old.service
ActiveState=inactive
Result=success
ExecMainStatus=0
ActiveEnterTimestamp=@{old}
ActiveExitTimestamp=@{old}

Id=tk627-never.service
ActiveState=inactive
Result=success
ExecMainStatus=0
ActiveEnterTimestamp=
ActiveExitTimestamp=

Id=tk613-progress.service
ActiveState=inactive
Result=success
ExecMainStatus=0
ActiveEnterTimestamp=@{e}
ActiveExitTimestamp=@{e}

Id=tk613-progress.timer
ActiveState=active
Result=success
ExecMainStatus=0
ActiveEnterTimestamp=@{a}
ActiveExitTimestamp=
""".format(a=int(now - 3600), b=int(now - 1800), e=int(now - 1200), old=int(now - 8 * 3600))
rows = c.unit_rows(c.parse_show(SHOW), now)
assert [(u["name"], u["state"], u["rc"]) for u in rows] == [("tk631-run", "run", None), ("tk630-job", "fail", 3), ("tk629-done", "done", 0)], rows
assert rows[0]["since_ts"] == int(now - 3600) and rows[0]["end_ts"] is None and rows[1]["end_ts"] == int(now - 1200)
# inactive с кодом ≠ 0 — тоже ошибка
bad = c.unit_rows(c.parse_show("Id=tk626-x.service\nActiveState=inactive\nResult=exit-code\nExecMainStatus=2\nActiveEnterTimestamp=@%d\nActiveExitTimestamp=@%d\n" % (now - 90, now - 60)), now)
assert bad[0]["state"] == "fail" and bad[0]["rc"] == 2
assert c.unit_rows([], now) == [] and c.parse_show("") == []
assert c.unit_ts("@0") is None and c.unit_ts("n/a") is None and c.unit_ts("@123") == 123.0

# 4. юниты прокидываются в итог как есть; units=None → units_ok False
assert c.collect(now, units=rows, sofia=SOFIA)["units"] == rows
orig = c.list_units
c.list_units = lambda now: None
d = c.collect(now, sofia=SOFIA)
c.list_units = orig
assert d["units_ok"] is False and d["units"] == []

# 5. сборки: идёт сборка / очередь / процессы не прочитать
c.rbuild_state = lambda: (True, [("a.tgz", now - 120), ("b.tgz", now - 60)])
b = c.builds(now)
assert b["building"] and b["waiting"] == 1 and "сборка идёт, в очереди ждут 1" in b["text"] and "старейший запрос 2 мин" in b["text"], b
c.rbuild_state = lambda: None
assert c.builds(now)["ok"] is False
print("test_tk613: OK")
