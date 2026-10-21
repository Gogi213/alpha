"""TK-315: дельта панели для прогона Летописи (П-17 F2): из docs/registry берёт строки прогона RUN -> каталог add-*.jsonl для tk289-deploy-delta.py.
add-metrics.jsonl и add-routes.json кладутся рядом заранее (metrics-p17f2.jsonl с calc; routes — tk315-make-routes.py).  python tk289-make-add.py RUN <каталог>"""
import json, os, sys
RUN, OUT = sys.argv[1], sys.argv[2]
REG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "registry")
def rows(n):
    return [l.rstrip("\r\n") for l in open(os.path.join(REG, n + ".jsonl"), encoding="utf-8", newline="") if l.strip()]
def put(n, ls):
    open(os.path.join(OUT, f"add-{n}.jsonl"), "w", encoding="utf-8", newline="\n").write("".join(l + "\n" for l in ls))
    print(n, len(ls))
rc = [l for l in rows("run_cells") if json.loads(l)["run_id"] == RUN]
cells = {json.loads(l)["cell_id"] for l in rc}; hyps = {json.loads(l)["hyp_id"] for l in rc}
put("run_cells", rc)
put("runs", [l for l in rows("runs") if json.loads(l)["id"] == RUN])
put("results", [l for l in rows("results") if json.loads(l)["run_id"] == RUN])
put("cells", [l for l in rows("cells") if json.loads(l)["id"] in cells])
put("plan_cells", [l for l in rows("plan_cells") if json.loads(l)["protocol"] == "P-17" and json.loads(l)["fraction"] == 2 and json.loads(l)["cell_hash"] in cells])
put("hypotheses", [l for l in rows("hypotheses") if json.loads(l)["id"] in hyps])
