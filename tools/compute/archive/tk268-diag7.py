# TK-268 ш.7: источник метрик Г-72/Г-146 и какие файлы месяцев в нём есть (только чтение)
import json, glob, os
mets = {d["hash"]: d for d in map(json.loads, filter(str.strip, open("/data/launch/registry/metrics.jsonl", encoding="utf-8")))}
for h in ("cea20ae6", "363d1a92", "38bf2aad", "e76a8b13"):
    k = [x for x in mets if x.startswith(h)][0]; d = mets[k]
    print(h, "src", d.get("src"), "months", d.get("months"), sorted(d["by_month"]))
for p in ("/data/tk0251/ps/closes-cap0-*.json", "/data/tk0251/p3/ps/closes-cap0-*.json", "/data/tk0113/drop2/r1/closes-cap0-*.json", "/data/tk0113/drop2/ext/closes-cap0-*.json"):
    print(p, [os.path.basename(x)[-8:-5] for x in sorted(glob.glob(p))])
