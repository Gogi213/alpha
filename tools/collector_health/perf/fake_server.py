"""Локальный стенд для проверки вёрстки: страница + arb_api на синтетических данных из макета v3 + снимок /api/v3."""
import json
import re
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(sys.argv[1])
sys.path.insert(0, str(REPO / "tools" / "collector_health"))
import arb_api  # noqa: E402

mock = Path(r"C:\Users\Георгий\Downloads\alpha-arb-tab-mockup-v3.html").read_text(encoding="utf-8")
ARB = json.loads(re.search(r"const ARB=(\{.*?\});\n", mock, re.S).group(1))
SNAP = json.loads(re.search(r"const SNAP=(\{.*?\});\n", mock, re.S).group(1))
d = Path(sys.argv[2])
d.mkdir(exist_ok=True)
rows, off = [], 0
with open(d / "chart-1.jsonl", "wb") as f:
    for r in ARB["rows"]:
        dt = ARB["detail"][str(r["i"])]
        line = (json.dumps({"S": dt["S"], "B": dt["B"], "C": dt["C"]}, separators=(",", ":")) + "\n").encode()
        f.write(line)
        n = r["n"]
        rows.append({"c": r["c"], "a": r["a"], "b": r["b"], "cnt": [round(r["h"]), round(r["h"] * 4), round(r["h"] * 6), n], "big": [n // 4, n // 9, n // 30] if r["i"] % 7 else None, "pk": r["pk"], "tau": r["tau"],
                     "net": r["net"], "o": off, "l": len(line)})
        off += len(line)
gen = int(time.time() * 1000)
(d / "arb.json").write_text(json.dumps({"gen": gen, "t0": ARB["t0"], "t1": ARB["t1"], "start": ARB["t0"], "slot": ARB["slot"], "fee": 12.6, "chart": "chart-1.jsonl",
                                        "names": ARB["names"], "rows": rows}), encoding="utf-8")
import serve  # noqa: E402

serve.v3.payload = lambda *a, **k: SNAP
serve.Handler.arb = arb_api.Arb(d)
serve.Handler.health_dir = d
serve.Handler.expected = []
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[3])), serve.Handler).serve_forever()
