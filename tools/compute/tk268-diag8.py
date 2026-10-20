# TK-268 ш.7: ключи closes L-P15F1/psim и наличие хешей Г-72/Г-146 по месяцам (только чтение)
import json, glob, os
fs = sorted(glob.glob("/data/launch/out/L-P15F1/psim/closes-p14f1-cap0-2026-*.json"))
print([os.path.basename(f)[-12:-5] for f in fs])
want = ("cea20ae6", "363d1a92", "38bf2aad", "e76a8b13")
for f in fs:
    d = json.load(open(f))
    ks = list(d)
    hit = {w: [k[:60] for k in ks if k.startswith(w) or w in k] for w in want}
    print(os.path.basename(f)[-12:-5], len(ks), ks[0][:70], {w: len(v) for w, v in hit.items()})
