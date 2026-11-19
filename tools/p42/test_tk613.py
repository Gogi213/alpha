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
assert d["now"] == {"ok": False, "err": "файл now.json не найден или не читается", "pushed_ts": None, "tasks": []}, d["now"]
assert d["units"] == [] and d["units_ok"] is True and "tasks" not in d
assert d["host"]["sofia"]["disk"]["free_gb"] == 31.2 and d["host"]["builds"]["ok"] and "сборок нет" in d["host"]["builds"]["text"]
assert d["host"]["cpus"] and d["host"]["disk"]["total_gb"] > 0

# 2. now.json → строка на задачу: цепочка ролей по порядку старта, состояние, русское имя по TK (иначе тема словами), мусор пропускается
def T(id, tk, topic, role, ver, state, started, updated):
    return {"id": id, "state": state, "tk": tk, "topic": topic, "role": role, "version": ver, "started_ts": started, "updated_ts": updated, "title": id}


put("pages/ml/data/now.json", json.dumps({"generated_ts": now - 30, "threads": [
    T("a1", "TK-631", "progress-now", "исп", 1, "run", now - 600, now - 5),                       # одиночный бегущий
    T("b1", "TK-700", "new-topic-here", "исп", 1, "done", now - 7200, now - 4000),             # исполнитель → проверяющий, оба готовы
    T("b2", "TK-700", "new-topic-here", "пров", 1, "done", now - 3900, now - 900),
    T("c1", "TK-701", "pair-run", "исп", 1, "done", now - 9000, now - 3000),                   # проверяющий идёт — задача «идёт»
    T("c2", "TK-701", "pair-run", "пров", 1, "run", now - 2000, now - 1900),
    T("d1", "TK-702", "retry", "исп", 1, "fail", now - 8000, now - 7000),                      # v1 с ошибкой, v2 идёт, затем проверяющий закрыт: «идёт» (v2)
    T("d2", "TK-702", "retry", "исп", 2, "run", now - 6000, now - 5900),
    T("d3", "TK-702", "retry", "пров", 1, "done", now - 5000, now - 4900),
    T("e1", "TK-703", "twice", "исп", 1, "done", now - 5000, now - 4500),                      # два подряд одинаковых ярлыка схлопываются
    T("e2", "TK-703", "twice", "исп", 1, "done", now - 4000, now - 800),
    T("f1", None, None, None, None, "fail", None, None),                                       # не по формату — своя строка
    {"id": "w", "state": "weird", "tk": "TK-1", "topic": "skip", "role": "исп"}, "мусор"]}))
put("p42-tk613/tk_names.json", json.dumps({"TK-631": "Вкладка «Прогресс»: что идёт сейчас"}))
nb = c.collect(now, units=[], sofia=SOFIA)["now"]
assert nb["ok"] and nb["pushed_ts"] == now - 30 and "threads" not in nb, nb
tasks = {t["id"]: t for t in nb["tasks"]}
assert [t["id"] for t in nb["tasks"]] == ["TK-631", "TK-702", "TK-701", "f1", "TK-703", "TK-700"], [t["id"] for t in nb["tasks"]]   # идёт → ошибка → готово; внутри — новее старт выше
a = tasks["TK-631"]
assert a["name"] == "Вкладка «Прогресс»: что идёт сейчас" and a["named"] and a["chain"] == ["исполнитель"] and a["state"] == "run" and a["end_ts"] is None and a["threads"] == 1
b = tasks["TK-700"]
assert b["name"] == "new topic here" and not b["named"] and b["chain"] == ["исполнитель", "проверяющий"] and b["state"] == "done" and b["end_ts"] == now - 900 and b["started_ts"] == now - 7200 and b["threads"] == 2
cc = tasks["TK-701"]
assert cc["chain"] == ["исполнитель", "проверяющий"] and cc["state"] == "run" and cc["end_ts"] is None
d = tasks["TK-702"]
assert d["chain"] == ["исполнитель", "исполнитель v2", "проверяющий"] and d["state"] == "run" and d["threads"] == 3
e = tasks["TK-703"]
assert e["chain"] == ["исполнитель"] and e["state"] == "done" and e["threads"] == 2
f = tasks["f1"]
assert [t["state"] for t in nb["tasks"]] == ["run", "run", "run", "fail", "done", "done"]
assert f["tk"] is None and f["name"] == "f1" and f["chain"] == [] and f["state"] == "fail" and f["started_ts"] is None
# задача, закрытая последним тредом с ошибкой, и без идущих: состояние последнего по старту
last_fail = c.group_tasks([T("x1", "TK-9", "t", "исп", 1, "done", 10, 20), T("x2", "TK-9", "t", "пров", 1, "fail", 30, 40)], {})
assert last_fail[0]["state"] == "fail" and last_fail[0]["end_ts"] == 40
# битый json / не тот формат — «ok: False», не падение
put("pages/ml/data/now.json", "{oops")
assert c.collect(now, units=[], sofia=SOFIA)["now"]["ok"] is False
put("pages/ml/data/now.json", json.dumps({"threads": "нет"}))
assert c.collect(now, units=[], sofia=SOFIA)["now"]["ok"] is False
put("pages/ml/data/now.json", json.dumps({"generated_ts": now, "threads": []}))
assert c.collect(now, units=[], sofia=SOFIA)["now"] == {"ok": True, "pushed_ts": now, "tasks": []}

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
