"""TK-613: проверка сборщика на подставных каталогах (запуск: python tools/p42/test_tk613.py). Ничего не пишет вне временной папки."""
import json
import os
import sys
import tempfile
import time

root = tempfile.mkdtemp()
os.environ["TK613_DATA"] = root
os.environ["TK613_HIST"] = f"{root}/hist.json"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk613_collect as c  # noqa: E402


def put(rel, txt):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(txt)


def by_id(d):
    return {t["id"]: t for t in d["tasks"]}


now = time.time()
# 1. ничего нет — не падает, у каждой задачи состояние «нет данных» или «пауза»
d, _ = c.collect(now, units=[])
t = by_id(d)
assert set(t) == {"TK-599", "TK-607", "TK-609", "TK-611", "TK-610"}, t.keys()
assert t["TK-607"]["state"] == "nodata" and t["TK-611"]["state"] == "nodata" and t["TK-610"]["state"] == "nodata", [x["state"] for x in t.values()]
json.dumps(d, allow_nan=False)

# 2. TK-607: идёт, скорость и ETA по логу; потом report DONE
put("p42-tk607/storefill/fill.log", "".join(f"store-fill: {i}/84745 за {i // 5} с ({5.0} файл/с), записано {i - 100}, пропущено 100, строк 1, различий 0, ошибок 0, склад 1.50 ГБ\n" for i in range(500, 3001, 500)))
d, _ = c.collect(now, units=[("tk607-storefill2", "active", "running")])
r = by_id(d)["TK-607"]
assert r["state"] == "run" and r["done"] == 3000 and r["total"] == 84745 and r["pct"] == 3.5, r
assert abs(r["rate"] - 5.0) < 0.01 and r["eta_ts"] > now, r
put("p42-tk607/storefill/report.txt", "DONE\n")
assert by_id(c.collect(now, units=[])[0])["TK-607"]["state"] == "done"

# 3. TK-599: шаги из live2.out; потом progress.json с «всего»
put("p42-tk599/switch/live2.out", "раундов 1, шагов 303, готово целиком: False, граница: {'round': 1, 'requests': 8, 'missing': 3, 'genuine': True}\n")
d, _ = c.collect(now, units=[("tk599-run", "active", "running")])
r = by_id(d)["TK-599"]
assert r["state"] == "run" and r["done"] == 303 and r["total"] is None and "всего: неизвестно" in r["text"] and "не хватает 3" in r["text"], r
put("p42-tk599/progress.json", json.dumps({"steps_done": 310, "steps_total_est": 408, "stage": "раунд 2", "updated_utc": "2026-11-19T00:01:58+00:00",
                                           "eta": {"low_gmt4": "2026-11-19 06:24 GMT+4", "high_gmt4": "2026-11-19 09:06 GMT+4", "confidence": "низкая"}}))
r = by_id(c.collect(now, units=[("tk599-run", "active", "running")])[0])["TK-599"]
assert r["done"] == 310 and r["total"] == 408 and r["pct"] == 76.0 and "≈408" in r["text"], r
assert r["eta_ts"] == c.to_ts("2026-11-19T02:24:00Z") and r["eta_hi_ts"] == c.to_ts("2026-11-19T05:06:00Z") and r["updated_ts"] == c.to_ts("2026-11-19T00:01:58Z"), r
put("p42-tk599/run.rc", "3\n")
assert by_id(c.collect(now, units=[])[0])["TK-599"]["state"] == "fail"
os.replace(f"{root}/p42-tk599/run.rc", f"{root}/p42-tk599/run.rc.x")
assert by_id(c.collect(now, units=[])[0])["TK-599"]["state"] == "pause"
put("p42-tk599/run.rc", "0\n")
assert by_id(c.collect(now, units=[])[0])["TK-599"]["state"] == "done"

# 4. TK-611: PASS/FAIL, повтор той же таблицы — последний статус
put("p42-tk611/ga.log", "wa-f1-S FAIL ModuleNotFoundError: x | {}\nwa-f1-S PASS GATE PASS rows=1\nwa-f2-L PASS GATE PASS rows=2\nwa-f3-L FAIL boom | {}\n")
r = by_id(c.collect(now, units=[])[0])["TK-611"]
assert r["done"] == 2 and r["total"] == 56 and r["state"] == "pause" and "FAIL 1" in r["text"], r

# 5. TK-609: серия по метке; ворота без «rc=» — не закончены
put("p42-tk609/ga1-v2a.log", "rows ref=3 new=3 total_diffs=0 rows_with_diff=0\nGA1 rc=0\n")
time.sleep(0.05)
put("p42-tk609/ga1-v3a.log", "== wa L deriv\nrows ref=5 new=5 total_diffs=0 rows_with_diff=0\n")
r = by_id(c.collect(now, units=[])[0])["TK-609"]
assert r["state"] == "pause" and r["done"] == 0 and r["total"] == 6 and "серия v3a" in r["text"] and "сверок таблиц 1" in r["text"], r
put("p42-tk609/ga1-v3a.log", "rows ref=5 new=5 total_diffs=0\nGA1 rc=0\n")
r = by_id(c.collect(now, units=[("tk609-gav3a", "active", "running")])[0])["TK-609"]
assert r["state"] == "run" and r["done"] == 1, r

# 6. TK-610: репетиция + ожидание заморозки
put("p42-tk610/out/dry-wc-R0/DONE", "0\n")
put("p42-tk610/out/dry-wc-R1/DONE", "0\n")
put("p42-tk610/dry/cand.DONE", "2026-11-19T01:00:00\n")
r = by_id(c.collect(now, units=[])[0])["TK-610"]
assert r["state"] == "wait" and r["done"] == 3 and r["total"] == 12 and "ждёт заморозки" in r["text"], r
put("p42-tk599/frozen.commit", "abcdef123456\n")
assert by_id(c.collect(now, units=[])[0])["TK-610"]["state"] == "pause"
put("p42-tk610/out/wa-R0/FAIL", "x\n")
assert by_id(c.collect(now, units=[])[0])["TK-610"]["state"] == "fail"

# 7. systemctl недоступен — сборка проходит (list_units вернул None)
c.list_units = lambda: None
d, _ = c.collect(now)
assert d["units_ok"] is False and len(d["tasks"]) == 5
print("test_tk613: OK")
