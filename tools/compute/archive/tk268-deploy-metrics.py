"""TK-268 ш.5–6: metrics.jsonl Летописи на calc = итог сверки cover*.py (прежние ∪ метрики из готовых closes, прошедшие check против канона; хеш — одна строка).
Бэкап первого запуска /data/launch/prev-tk268m/metrics.jsonl; замена атомарная (mv). Запуск на calc: python3 tk268-deploy-metrics.py /data/tk0268/metrics-final3.jsonl"""
import json, os, shutil, sys
R = "/data/launch/registry/metrics.jsonl"; B = "/data/launch/prev-tk268m"
os.makedirs(B, exist_ok=True)
if not os.path.exists(B + "/metrics.jsonl"):
    shutil.copy2(R, B + "/metrics.jsonl")
rd = lambda p: [l.rstrip("\r\n") for l in open(p, encoding="utf-8", newline="") if l.strip()]
old, new = rd(R), rd(sys.argv[1])
oh = {json.loads(l)["hash"] for l in old}; nh = {json.loads(l)["hash"] for l in new}
assert len(nh) == len(new) and oh <= nh, "итог должен быть надмножеством прежних хешей без дублей"
with open(R + ".tmp", "w", encoding="utf-8", newline="") as f:
    f.writelines(l + "\n" for l in new)
os.replace(R + ".tmp", R)
print("metrics", len(old), "->", len(new), "(+", len(nh - oh), "новых хешей,", sum(a != b for a, b in zip(sorted(old), sorted(new))) if False else "заменённые — по check", ")")
