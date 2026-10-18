#!/usr/bin/env python3
"""TK-291 (П-19 §7 п.1, ворота 0; Судья P-19-result-check): 3 честные клетки lot_first=0.5/2/8 (bounce-grid, авг+сен) -> дельта Летописи.
tk291-load-lf.py <out-lf каталог> <метрики.py каталог> <выход каталог> <run_id>
Форма как у 68 клеток (tk291-load.py): id = sha256(JSON ячейки)[:16], хеш плана канонический (cellhash.cell_hash от params.cfg_json);
lot_first в каноническом ключе набора нет — лежит в variant.axes. без DROP монет (как Летопись B1: 1966/722 сделок = ворота 0 (в)); closes из rounds.csv: (exit_ns//1e6, net_bps/1e4·qty·entry_vwap, монета)."""
import collections, csv, glob, hashlib, json, os, sys
sys.path.insert(0, os.environ.get("REGTOOLS", "/opt/alpha-launch/tools/registry"))
import cellhash as ch
import cellhash_reg as cr

root, mdir, out, run = sys.argv[1:5]
sys.path.insert(0, mdir)
import metrics as M
FORM = cr.B1_FORM
BASE = "30229bf7071fb028"
SRC = "bounce-grid честный, --set t-bid-btc4h-q1-lf<метка>:…,lot_first=k, --touch-col, tk291 1cdf78ee; ворота 0 §7 п.1, авг+сен"
LF = [("0p5", 0.5), ("2", 2.0), ("8", 8.0)]
MONTHS = ["2026-08", "2026-09"]


def closes_of(setname):
    res = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(root, "run-*", "out", "*", setname, "rounds.csv"))):
        mo = p.split(os.sep)[-3][:7]
        lines = [l.rstrip("\r\n") for l in open(p, newline="", encoding="utf-8") if not l.startswith("#")]
        h, rows = lines[0].split(","), list(csv.reader(lines[1:]))
        c = {n: h.index(n) for n in ("symbol", "form", "exit_ns", "net_bps", "qty", "entry_vwap")}
        for r in rows:
            if r[c["form"]] != FORM:
                continue
            usd = float(r[c["net_bps"]]) / 1e4 * float(r[c["qty"]]) * float(r[c["entry_vwap"]])
            res[mo].append([int(r[c["exit_ns"]]) // 1_000_000, round(usd, 4), r[c["symbol"]]])
    return res


os.makedirs(out, exist_ok=True)
w = {n: open(os.path.join(out, f"add-{n}.jsonl"), "w", encoding="utf-8", newline="\n") for n in ("cells", "run_cells", "results", "metrics", "plan_cells")}
for lab, k in LF:
    cl = closes_of(f"t-bid-btc4h-q1-lf{lab}")
    assert sorted(cl) == MONTHS, (lab, sorted(cl))
    cfg = {"form": cr.B1_FORM, "set": cr.B1_SET, "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": cr.UNIVERSE["v171c"],
           "variant": {"family": "p19-honest", "axes": {"lot_first": k}}}
    cfg_json = ch.canonical_json(cfg)
    h = ch.cell_hash(cfg)
    params = {"group": "p19-honest", "lot_first": k, "base": BASE, "source": SRC, "cfg_json": cfg_json}
    cell = {"logic_version": "bounce-v1", "form": "B1", "set_name": f"p19/lf{lab}", "params": params}
    cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    w["cells"].write(json.dumps({"id": cid, **cell}, ensure_ascii=False) + "\n")
    w["run_cells"].write(json.dumps({"run_id": run, "cell_id": cid, "hyp_id": "М-04"}, ensure_ascii=False) + "\n")
    for mo in MONTHS:
        ext = {"group": "p19-honest", "src": "bounce-grid-lot-first", "cap": "0 (free)", "fraction": "F1", "lot_first": k, "gate": "ворота 0 §7 п.1"}
        w["results"].write(json.dumps({"run_id": run, "cell_id": cid, "month": mo, "trades": len(cl[mo]),
                                       "pnl_usd": round(sum(x[1] for x in cl[mo]), 4), "ext": ext}, ensure_ascii=False) + "\n")
    w["metrics"].write(json.dumps({**M.hash_metrics(cl), "hash": h, "src": ["tk291/out-lf"]}, ensure_ascii=False) + "\n")
    w["plan_cells"].write(json.dumps({"protocol": "P-19", "fraction": 1, "hyp": "М-04", "cell_hash": h,
                                      "axes": json.dumps({"ось": "ворота 0 · честный lot_first (первое касание)", "k": k}, ensure_ascii=False),
                                      "cfg": cfg_json}, ensure_ascii=False) + "\n")
    print(lab, "хеш", h, "сделок", {m: len(cl[m]) for m in MONTHS})
for f in w.values():
    f.close()
