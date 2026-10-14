#!/usr/bin/env python3
"""TK-241, В-7: после счёта формы — сводка closes psim -> результаты по каноническому хешу в оверлей сервера (БЕЗ записи в канон Летописи и без нейросетей, В-6).
  postload.py <RUN>   # RUN = /data/launch/out/L-…: читает run.json (new_hm), routes.json, psim/closes-p14f1-cap{0,3}-<мес>.json
Пишет <RUN>/overlay.jsonl (строка = результат хеша за месяц в формате results Летописи + ext как у load_f1.py: cell_id = канонический хеш) и
<RUN>/closes-sum.csv (variant,month,cap,n,sum_usd — как sum_closes_f1.py). Канон: CEO переносит пакет адаптером как load_f1.py.
Ошибка — строка в <RUN>/postload.log (на вкладке «Запуск» одна красная строка) и код 1. Сито (registry.py sieve) — при переносе в канон: на calc нет registry.sqlite."""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_closes(psim, months):
    """{variant: {месяц: {cap: (n, Σ$)}}}; файла месяца нет — ошибка (иначе упавший psim выглядел бы нулём сделок)."""
    agg = {}
    for m in months:
        for cap in (0, 3):
            f = os.path.join(psim, f"closes-p14f1-cap{cap}-{m}.json")
            if not os.path.exists(f):
                raise RuntimeError(f"нет {os.path.basename(f)}: psim не дошёл")
            for v, per in json.load(open(f, encoding="utf-8")).items():
                for mo, caps in per.items():
                    for c, tr in caps.items():
                        agg.setdefault(v, {}).setdefault(mo, {})[str(c)] = (len(tr), round(sum(x[1] for x in tr), 4))
    return agg


def build(run_dir, routes_doc):
    run = json.load(open(os.path.join(run_dir, "run.json"), encoding="utf-8"))
    psim = os.path.join(run_dir, "psim")
    if not os.path.exists(os.path.join(psim, "psim-p14f1.done")):
        raise RuntimeError("нет psim-p14f1.done: " + (open(os.path.join(psim, "p14f1-psim-fail.txt"), encoding="utf-8").read()[:200] if os.path.exists(os.path.join(psim, "p14f1-psim-fail.txt")) else "psim не запускался"))
    months = sorted({m for ms in run["new_hm"].values() for m in ms})
    agg = load_closes(psim, months)
    rows = []
    for h, ms in sorted(run["new_hm"].items()):
        for m in ms:
            ln = routes_doc["lines"].get(h) or routes_doc["monthly"][h][m]
            form, _, st = ln.partition(" ")
            caps = agg.get(f"{st}@{form}", {}).get(m)
            empty = not caps   # psim пишет только варианты со сделками: клетка в --cells есть, в closes нет = 0 сделок
            caps = caps or {"0": (0, 0.0), "3": (0, 0.0)}
            ext = {"group": "launch", "src": "closes", "cap": "0 (free)", "cell_line": ln, "n_cap3": caps["3"][0], "pnl_usd_cap3": caps["3"][1]}
            if empty:
                ext["empty"] = "нет сделок в closes"
            rows.append({"run_id": run["id"], "cell_id": h, "month": m, "trades": caps["0"][0], "pnl_usd": caps["0"][1], "ext": ext})
    return rows, agg


def main(argv=None, doc=None):
    run_dir = (argv or sys.argv[1:])[0]
    log = os.path.join(run_dir, "postload.log")
    try:
        rows, agg = build(run_dir, doc or json.load(open(os.path.join(HERE, "routes.json"), encoding="utf-8")))
        with open(os.path.join(run_dir, "closes-sum.csv"), "w", encoding="utf-8", newline="\n") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["variant", "month", "cap", "n", "sum_usd"])
            for v, per in sorted(agg.items()):
                for mo, caps in sorted(per.items()):
                    for c, (n, s) in sorted(caps.items()):
                        w.writerow([v, mo, c, n, f"{s:.4f}"])
        tmp = os.path.join(run_dir, "overlay.jsonl.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
        os.replace(tmp, os.path.join(run_dir, "overlay.jsonl"))
        if os.path.exists(log):
            os.remove(log)
        print(f"оверлей: {len(rows)} хеш-мес. -> {run_dir}/overlay.jsonl")
        return 0
    except Exception as e:   # одна красная строка на вкладке «Запуск»
        with open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"{os.path.basename(run_dir.rstrip('/'))}: {e}\n")
        print(f"postload: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
