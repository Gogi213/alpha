#!/usr/bin/env python3
"""TK-344 П-24 §3: исходы по клеткам P* (описание, янв–сен 2026). Вход: дерево суток `<run>/<сутки>/out/all/{signals,rounds}.csv`
(`--busy-skip off`), фильтр шага 0 (`keep-q90.csv`/`keep-q95.csv`, ключ symbol,t0_ns,price_tick). Правило «занято» — как в
`busy-replay.py` (import оттуда). Метрики на (форма, месяц): n_kept (сигналов фильтра), n_exec (принятых исполненных кругов),
sum_net_bps; ITT = sum_net / N_P (v2, TK-344: знаменатель — ВСЕ сигналы P* фильтра на сутках и монетах прогона, одинаковый для
всех форм; сигнал без строки в signals.csv формы, занятый или неисполненный = 0), средняя по кругу = sum_net / n_exec.
Цепочка n: keep → в прогоне (монеты суток из manifest) → сигналы формы (n_kept) → принятые (n_acc) → исполненные (n_exec).
n_эфф = символо-сутки с ≥ 1 принятым исполненным кругом, SD круга = SD net_bps, MDE_круга = 2,8·SD/√n_эфф. Медианы расстояний
|exit−entry|/entry в бп по причине выхода. Интервал — по суткам (ratio-оценка, SE по суммам суток). Выход: <out>.json + <out>.md.
    p24-agg.py <run> <keep.csv> <out-prefix> [--procs N]"""
import argparse, csv, importlib.util, json, math, os, sys
from datetime import datetime, timezone
from collections import defaultdict
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("busy_replay", os.path.join(HERE, "busy-replay.py"))
br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)

KEEP = None


KD = None  # день -> {монета: число сигналов P*}


def day(args):
    run, d = args
    base = os.path.join(run, d, "out", "all")
    _, allowed, norm = KEEP
    syms = set()
    for l in open(os.path.join(base, "manifest.txt"), encoding="utf-8"):
        if l.startswith("symbols="):
            syms = set(l.strip()[8:].split(","))
    n_keep = sum(KD.get(d, {}).values())
    n_p = sum(v for k, v in KD.get(d, {}).items() if k in syms)
    _, body = br.read_body(os.path.join(base, "signals.csv"))
    state, accepted = {}, set()
    nk = defaultdict(int)
    na = defaultdict(int)
    for r in csv.DictReader(body):
        if norm(r) not in allowed:
            continue
        nk[r["form"]] += 1
        g = (r["symbol"], r["day_utc"], r["form"])
        idle, stopped = state.get(g, (None, False))
        if stopped or (idle is not None and int(r["t0_ns"]) < idle):
            continue
        accepted.add(g + (r["signal_index"],))
        na[r["form"]] += 1
        state[g] = (idle, True) if (r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended") else (int(r["idle_ns"]), False)
    ne, sn, reasons = defaultdict(int), defaultdict(float), defaultdict(lambda: defaultdict(int))
    sq, sd_cnt, dist = defaultdict(float), defaultdict(set), defaultdict(list)
    _, body = br.read_body(os.path.join(base, "rounds.csv"))
    for r in csv.DictReader(body):
        if (r["symbol"], r["day_utc"], r["form"], r["signal_index"]) in accepted:
            ne[r["form"]] += 1
            x = float(r["net_bps"])
            sn[r["form"]] += x
            sq[r["form"]] += x * x
            sd_cnt[r["form"]].add(r["symbol"])
            e, q = float(r["entry_px"]), float(r["exit_px"])
            if e > 0:
                dist[(r["form"], r["reason"])].append(abs(q - e) / e * 1e4)
            reasons[r["form"]][r["reason"]] += 1
    med = {f"{k[0]}|{k[1]}": [round(y, 2) for y in v] for k, v in dist.items()}
    return d, dict(nk), dict(ne), dict(sn), {f: dict(v) for f, v in reasons.items()}, (n_keep, n_p, dict(na), dict(sq), {f: len(v) for f, v in sd_cnt.items()}, med)


def main():
    global KEEP, KD
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("keep"); ap.add_argument("out")
    ap.add_argument("--procs", type=int, default=15)
    a = ap.parse_args()
    KEEP = br.load_keep(a.keep)
    KD = defaultdict(lambda: defaultdict(int))
    for k in KEEP[1]:
        KD[datetime.fromtimestamp(int(k[1]) / 1e9, timezone.utc).strftime("%Y-%m-%d")][k[0]] += 1
    days = sorted(x for x in os.listdir(a.run) if os.path.exists(os.path.join(a.run, x, "done")))
    with Pool(a.procs) as p:
        res = p.map(day, [(a.run, d) for d in days], chunksize=1)
    per = defaultdict(lambda: defaultdict(list))  # form -> month -> [(d, N_P, ne, sn)]
    rs = defaultdict(lambda: defaultdict(int))
    ch = defaultdict(lambda: defaultdict(int))  # form -> звено цепочки
    sqs, sdays, dist = defaultdict(float), defaultdict(int), defaultdict(list)
    keep_all = in_run = 0
    for d, nk, ne, sn, rz, (n_keep, n_p, na, sq, sdc, med) in res:
        keep_all += n_keep; in_run += n_p
        for f in nk:
            per[f][d[:7]].append((d, n_p, ne.get(f, 0), sn.get(f, 0.0)))
            ch[f]["built"] += nk[f]; ch[f]["accepted"] += na.get(f, 0); ch[f]["exec"] += ne.get(f, 0)
            sqs[f] += sq.get(f, 0.0); sdays[f] += sdc.get(f, 0)
            for k, v in rz.get(f, {}).items():
                rs[f][k] += v
        for k, v in med.items():
            dist[k].extend(v)

    def stat(rows):
        nk = sum(r[1] for r in rows); ne = sum(r[2] for r in rows); sn = sum(r[3] for r in rows)
        if nk == 0:
            return {"days": len(rows), "n_P": 0, "n_exec": ne, "sum_net": sn}
        m = sn / nk
        # ratio-оценка по суткам: SE = sqrt(sum (s_i - m*k_i)^2) / nk (кластер = сутки)
        se = math.sqrt(sum((r[3] - m * r[1]) ** 2 for r in rows)) / nk
        return {"days": len(rows), "n_P": nk, "n_exec": ne, "sum_net": sn, "itt_bps": m, "itt_se": se,
                "per_exec_bps": sn / ne if ne else None}
    out = {"_chain": {"keep_total": keep_all, "keep_in_run_symbols": in_run, "days": len(days)}}
    for f in sorted(per):
        allr = [x for mth in per[f].values() for x in mth]
        st = stat(allr)
        ne = st["n_exec"]; mean = st["sum_net"] / ne if ne else 0.0
        sd = math.sqrt(max(sqs[f] / ne - mean * mean, 0.0)) if ne else 0.0
        neff = sdays[f]
        st.update({"sd_round": sd, "n_eff_symdays": neff, "mde_round_bps": 2.8 * sd / math.sqrt(neff) if neff else None})
        out[f] = {"all": st, "months": {m: stat(v) for m, v in sorted(per[f].items())}, "reasons": dict(rs[f]), "chain": dict(ch[f])}
    out["_dist_median_bps"] = {k: sorted(v)[len(v) // 2] for k, v in dist.items() if v}
    json.dump(out, open(a.out + ".json", "w"), ensure_ascii=False, indent=1)
    c = out["_chain"]
    L = [f"# П-24 исходы v2 — {os.path.basename(a.keep)}, суток {len(days)}", "",
         f"Цепочка n: keep (фильтр P*) на этих сутках {c['keep_total']} → монеты прогона {c['keep_in_run_symbols']} = N_P (общий знаменатель ITT).", "",
         "| форма | N_P | сигналов формы | принято | исполнено | ITT бп/сигнал P* ± SE(сутки) | бп/круг | SD круга | n_эфф (символо-сутки) | MDE круга, бп |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for f, v in out.items():
        if f.startswith("_"):
            continue
        s, q = v["all"], v["chain"]
        L.append(f"| {f} | {s['n_P']} | {q['built']} | {q['accepted']} | {q['exec']} | {s.get('itt_bps', 0):+.3f} ± {s.get('itt_se', 0):.3f} | "
                 f"{(s.get('per_exec_bps') or 0):+.2f} | {s['sd_round']:.1f} | {s['n_eff_symdays']} | {(s['mde_round_bps'] or 0):.2f} |")
    L += ["", "ITT по месяцам (бп/сигнал P*):", "", "| форма | " + " | ".join(f"{m:02d}" for m in range(1, 10)) + " |", "|---|" + "---|" * 9]
    for f, v in out.items():
        if f.startswith("_"):
            continue
        cells = []
        for m in range(1, 10):
            s = v["months"].get(f"2026-{m:02d}")
            cells.append(f"{s['itt_bps']:+.2f}" if s and s["n_P"] else "-")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L += ["", "Причины выхода (круги):"]
    for f, v in out.items():
        if f.startswith("_"):
            continue
        L.append(f"- {f}: " + ", ".join(f"{k} {n}" for k, n in sorted(v["reasons"].items(), key=lambda x: -x[1])))
    L += ["", "Медиана |выход−вход|/вход, бп, по причине выхода:"]
    L += [f"- {k}: {x:.1f}" for k, x in sorted(out["_dist_median_bps"].items())]
    open(a.out + ".md", "w", encoding="utf-8").write(chr(10).join(L) + chr(10))
    print(chr(10).join(L[:16]))


if __name__ == "__main__":
    main()
