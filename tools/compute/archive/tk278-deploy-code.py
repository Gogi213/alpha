"""TK-278: код формы головы PR на calc. Бэкап /data/launch/prev-tk278c; launch.py/build_metrics.py/verify_metrics.py заменяются файлами из <каталог>;
routes.json = объединение (PR из <каталог>/routes.json + calc), при расхождении побеждает calc (Г-82: TK-268 уже в каноне как r1). Запуск на calc:
tk278-deploy-code.py <каталог с файлами головы PR>"""
import json, os, shutil, sys
A = sys.argv[1]; L = "/opt/alpha-launch/tools/launch"; B = "/data/launch/prev-tk278c"
os.makedirs(B, exist_ok=True)
for n in ("launch.py", "build_metrics.py", "verify_metrics.py", "routes.json"):
    if not os.path.exists(f"{B}/{n}"):
        shutil.copy2(f"{L}/{n}", f"{B}/{n}")
cur = json.load(open(f"{L}/routes.json", encoding="utf-8")); new = json.load(open(f"{A}/routes.json", encoding="utf-8"))
out = {**new, **cur}   # скалярные поля (generated, r2_re) — calc; protocols — из PR
out["protocols"] = new["protocols"]
for sec in ("routes", "lines", "monthly", "sets", "r1spec"):
    out[sec] = {**new[sec], **cur[sec]}
    print(sec, len(cur[sec]), "->", len(out[sec]))
open(f"{L}/routes.json.new", "w", encoding="utf-8", newline="\n").write(json.dumps(out, ensure_ascii=False, sort_keys=True))
os.replace(f"{L}/routes.json.new", f"{L}/routes.json")
for n in ("launch.py", "build_metrics.py", "verify_metrics.py"):
    shutil.copy2(f"{A}/{n}", f"{L}/{n}.new"); os.replace(f"{L}/{n}.new", f"{L}/{n}")
