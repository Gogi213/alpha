#!/usr/bin/env python3
"""TK-344 П-24 §3: исходы по клеткам P* (описание, янв–сен 2026). Вход: дерево суток `<run>/<сутки>/out/all/{signals,rounds}.csv`
(`--busy-skip off`), фильтр шага 0 (`keep-q90.csv`/`keep-q95.csv`, ключ symbol,t0_ns,price_tick). Правило «занято» — как в
`busy-replay.py` (import оттуда). Метрики на (форма, месяц): n_kept (сигналов фильтра), n_exec (принятых исполненных кругов),
sum_net_bps; ITT = sum_net / n_kept (неисполненные и занятые = 0), средняя по кругу = sum_net / n_exec. Интервал — по суткам
(ratio-оценка, SE по суммам суток). Выход: <out>.json + <out>.md.
    p24-agg.py <run> <keep.csv> <out-prefix> [--procs N]"""
import argparse, csv, importlib.util, json, math, os, sys
from collections import defaultdict
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("busy_replay", os.path.join(HERE, "busy-replay.py"))
br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)

KEEP = None


def day(args):
    run, d = args
    base = os.path.join(run, d, "out", "all")
    _, allowed, norm = KEEP
    _, body = br.read_body(os.path.join(base, "signals.csv"))
    state, accepted = {}, set()
    nk = defaultdict(int)
    for r in csv.DictReader(body):
        if norm(r) not in allowed:
            continue
        nk[r["form"]] += 1
        g = (r["symbol"], r["day_utc"], r["form"])
        idle, stopped = state.get(g, (None, False))
        if stopped or (idle is not None and int(r["t0_ns"]) < idle):
            continue
        accepted.add(g + (r["signal_index"],))
        state[g] = (idle, True) if (r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended") else (int(r["idle_ns"]), False)
    ne, sn, reasons = defaultdict(int), defaultdict(float), defaultdict(lambda: defaultdict(int))
    _, body = br.read_body(os.path.join(base, "rounds.csv"))
    for r in csv.DictReader(body):
        if (r["symbol"], r["day_utc"], r["form"], r["signal_index"]) in accepted:
            ne[r["form"]] += 1
            sn[r["form"]] += float(r["net_bps"])
            reasons[r["form"]][r["reason"]] += 1
    return d, dict(nk), dict(ne), dict(sn), {f: dict(v) for f, v in reasons.items()}


def main():
    global KEEP
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("keep"); ap.add_argument("out")
    ap.add_argument("--procs", type=int, default=15)
    a = ap.parse_args()
    KEEP = br.load_keep(a.keep)
    days = sorted(x for x in os.listdir(a.run) if os.path.exists(os.path.join(a.run, x, "done")))
    with Pool(a.procs) as p:
        res = p.map(day, [(a.run, d) for d in days], chunksize=1)
    per = defaultdict(lambda: defaultdict(list))  # form -> month -> [(d, nk, ne, sn)]
    rs = defaultdict(lambda: defaultdict(int))
    for d, nk, ne, sn, rz in res:
        for f in nk:
            per[f][d[:7]].append((d, nk[f], ne.get(f, 0), sn.get(f, 0.0)))
            for k, v in rz.get(f, {}).items():
                rs[f][k] += v

    def stat(rows):
        nk = sum(r[1] for r in rows); ne = sum(r[2] for r in rows); sn = sum(r[3] for r in rows)
        if nk == 0:
            return {"days": len(rows), "n_kept": 0, "n_exec": ne, "sum_net": sn}
        m = sn / nk
        # ratio-оценка по суткам: SE = sqrt(sum (s_i - m*k_i)^2) / nk (кластер = сутки)
        se = math.sqrt(sum((r[3] - m * r[1]) ** 2 for r in rows)) / nk
        return {"days": len(rows), "n_kept": nk, "n_exec": ne, "sum_net": sn, "itt_bps": m, "itt_se": se,
                "per_exec_bps": sn / ne if ne else None}
    out = {}
    for f in sorted(per):
        allr = [x for mth in per[f].values() for x in mth]
        out[f] = {"all": stat(allr), "months": {m: stat(v) for m, v in sorted(per[f].items())}, "reasons": dict(rs[f])}
    json.dump(out, open(a.out + ".json", "w"), ensure_ascii=False, indent=1)
    L = [f"# П-24 исходы — {os.path.basename(a.keep)}, суток {len(days)}", "",
         "| форма | суток | n_kept | n_exec | ITT бп/сигнал ± SE(сутки) | бп/круг |", "|---|---|---|---|---|---|"]
    for f, v in out.items():
        s = v["all"]
        L.append(f"| {f} | {s['days']} | {s['n_kept']} | {s['n_exec']} | {s.get('itt_bps', 0):+.3f} ± {s.get('itt_se', 0):.3f} | "
                 f"{(s.get('per_exec_bps') or 0):+.2f} |")
    L += ["", "ITT по месяцам (бп/сигнал):", "", "| форма | " + " | ".join(f"{m:02d}" for m in range(1, 10)) + " |", "|---|" + "---|" * 9]
    for f, v in out.items():
        cells = []
        for m in range(1, 10):
            s = v["months"].get(f"2026-{m:02d}")
            cells.append(f"{s['itt_bps']:+.2f}" if s and s["n_kept"] else "-")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L += ["", "Причины выхода (круги):"]
    for f, v in out.items():
        L.append(f"- {f}: " + ", ".join(f"{k} {n}" for k, n in sorted(v["reasons"].items(), key=lambda x: -x[1])))
    open(a.out + ".md", "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:14]))


if __name__ == "__main__":
    main()
