#!/usr/bin/env python3
"""TK-291 (П-19, В-37; форма загрузки — Судья, P-19-result-check): 68 пересчётных клеток F1∪F2 -> дельта Летописи (add-*.jsonl).
tk291-load.py <psim/f2 каталог с closes-p19-<мес>.json> <sieve F1∪F2 csv> <sieve F1 csv> <метрики.py каталог> <выход каталог> <run_id> <runs.jsonl-образец>
Ячейка (как load_closes.py TK-182): id = sha256(JSON ячейки)[:16], form B1, set_name p19/<вектор>, params.lot_by_n = k0..k3.
results: trades = число закрытий, pnl_usd = Σ$ cap 0 (сырой, не нормированный); ext: m, usd_norm (сито), dSR, p, se, статус сита, фракция F1/F2.
metrics: tools/launch/metrics.hash_metrics по closes вектора. Только дельта (дописью), B1 не пишется (30229bf7071fb028 уже в Летописи)."""
import csv, hashlib, json, os, re, sys

closes_dir, sieve, sieve_f1, mdir, out, run, runs_tpl = sys.argv[1:8]
sys.path.insert(0, mdir)
import metrics as M
K = [0, 0.25, 0.375, 0.5, 0.75, 1.5, 2, 3, 4, 6, 8]
MONTHS = [f"2026-{i:02d}" for i in range(1, 10)]
BASE = "30229bf7071fb028"
SRC = "portfolio-sim --lot-by-n по rounds B1 + touch_index, tk291 1cdf78ee"
rows = {r["vec"]: r for r in csv.DictReader(open(sieve, encoding="utf-8"))}
f1 = {r["vec"] for r in csv.DictReader(open(sieve_f1, encoding="utf-8"))}
closes = {mo: json.load(open(os.path.join(closes_dir, f"closes-p19-{mo}.json"), encoding="utf-8")) for mo in MONTHS}


def vec_k(name):
    """метка -> (k0,k1,k2,k3): a<ось>k<значение> (F1) или из файла f2vec (метка=k0,k1,k2,k3 — ключи closes те же метки)."""
    m = re.fullmatch(r"a(\d)k([0-9.]+)", name)
    if m:
        t = [1.0] * 4; t[int(m.group(1))] = float(m.group(2)); return t
    return None


f2lab = {}
for ln in open(os.path.join(os.path.dirname(closes_dir.rstrip("/")), "f2vec.txt"), encoding="utf-8"):
    if ln.strip():
        a, b = ln.strip().split("="); f2lab[a] = [float(x) for x in b.split(",")]
os.makedirs(out, exist_ok=True)
w = {n: open(os.path.join(out, f"add-{n}.jsonl"), "w", encoding="utf-8", newline="\n") for n in ("cells", "run_cells", "results", "metrics", "runs")}
nres = 0
for name, r in rows.items():
    if name == "B1":
        continue
    k = vec_k(name) or f2lab[name]
    params = {"group": "p19-psim", "lot_by_n": k, "base": BASE, "source": SRC}
    if 0 in k:
        params["approx"] = "k=0: освобождение лимита позиции не моделируется"
    cell = {"logic_version": "bounce-v1", "form": "B1", "set_name": "p19/" + name, "params": params}
    cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    w["cells"].write(json.dumps({"id": cid, **cell}, ensure_ascii=False) + "\n")
    w["run_cells"].write(json.dumps({"run_id": run, "cell_id": cid, "hyp_id": "М-04"}, ensure_ascii=False) + "\n")
    cm = {}
    for mo in MONTHS:
        cl = closes[mo][name]
        cm[mo] = cl
        ext = {"group": "p19-psim", "src": "psim-lot-by-n", "cap": "0 (free)", "fraction": "F1" if name in f1 else "F2",
               "m": float(r[f"{mo}_m"]), "usd_norm_half_A": float(r["usd_norm"]), "dSR_A": float(r["dSR"]),
               "p_A": float(r["p"]), "se_A": float(r["se"]), "sieve": r["status"]}
        w["results"].write(json.dumps({"run_id": run, "cell_id": cid, "month": mo, "trades": len(cl),
                                       "pnl_usd": round(sum(x[1] for x in cl), 4), "ext": ext}, ensure_ascii=False) + "\n")
        nres += 1
    hm = M.hash_metrics(cm)
    w["metrics"].write(json.dumps({**hm, "hash": cid, "src": ["tk291/psim/f2"]}, ensure_ascii=False) + "\n")
tpl = [json.loads(l) for l in open(runs_tpl, encoding="utf-8") if l.strip()][-1]
tpl.update({"id": run, "ts": __import__("datetime").datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=4))).isoformat(timespec="seconds"),
            "result_path": "/data/tk291/psim/f2", "ticket": "TK-291", "protocol": "P-19",
            "what": "TK-291: П-19 (размер по номеру касания, В-37) — линейный пересчёт portfolio-sim --lot-by-n по rounds B1 + touch_index "
                    "(проход B1 9 мес. с --touch-col, 1cdf78ee, 11 915 сделок = Летопись B1), F1 44 + F2 24 = 68 клеток × янв–сен, "
                    "движок на клетку не гонялся; сито А: годных 0, плато нет; docs/findings/tk291-p19-size-by-touch-2026-10-17.md"})
w["runs"].write(json.dumps(tpl, ensure_ascii=False) + "\n")
for f in w.values():
    f.close()
print("клеток", len(rows) - ("B1" in rows), "результатов", nres, "→", out)
