"""Сводка событий таймаута/разрыва Bybit-коллектора с момента запуска (TK-532, В-140): events-*.jsonl + health.json -> JSON, затем .done."""
import glob, json, sys, time
since = int(sys.argv[1]); out = sys.argv[2]
ev = []
for f in sorted(glob.glob("/data/bbws/events-*.jsonl")):
    for ln in open(f):
        try: d = json.loads(ln)
        except ValueError: continue
        if d["ts_ms"] >= since and d["ev"] in ("timeout", "error", "ping_degraded", "path_switch", "switch_timeout", "path_rejected", "rotated"):
            ev.append(d)
h = json.load(open("/data/health/bybit.json"))
res = {"since_ms": since, "until_ms": int(time.time() * 1000), "events": ev, "n_timeout": sum(e["ev"] == "timeout" for e in ev), "n_error": sum(e["ev"] == "error" for e in ev),
       "health": {k: h["ws"].get(k) for k in ("connections", "timeouts_1h", "reconnects_1h", "timeouts_24h", "reconnects_24h")}}
json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
open(out + ".done", "w").write("ok\n")
