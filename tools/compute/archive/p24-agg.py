#!/usr/bin/env python3
"""TK-344 П-24 §3: исходы по клеткам P* (описание, янв–сен 2026). Вход: дерево суток `<run>/<сутки>/out/all/{signals,rounds}.csv`
(`--busy-skip off`), фильтр шага 0 (`keep-q90.csv`/`keep-q95.csv`, ключ symbol,t0_ns,price_tick). Правило «занято» — как в
`busy-replay.py` (import оттуда). Метрики на (форма, месяц): n_kept (сигналов фильтра), n_exec (принятых исполненных кругов),
sum_net_bps; ITT = sum_net / N_P (v3, TK-344: знаменатель — P*, сопоставленные с подходом прогона: монета, ±2 тика, ≤60 с, один к одному;
одинаков для всех форм; сигнал без строки формы, занятый или неисполненный = 0; P* без пары — «вне прогона»; режимы: точный ключ, все P*), средняя по кругу = sum_net / n_exec.
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
KD = None  # день -> [(монета, t0_ns, price_tick)] P*
TOL_TICK, TOL_NS = 2, 60_000_000_000


def match(pk, ap):
    """P* ↔ подход прогона: монета та же, |Δtick| ≤ 2, |Δt| ≤ 60 с, один к одному (пары по возрастанию |Δt|).
    Возвращает (M — ключи подходов в паре, E — из них точное совпадение ключа)."""
    by = defaultdict(list)
    for a in ap:
        by[a[0]].append(a)
    for v in by.values():
        v.sort(key=lambda a: a[1])
    pairs = []
    for i, (s, t, k) in enumerate(pk):
        v = by.get(s, ())
        lo, hi = 0, len(v)
        while lo < hi:
            m = (lo + hi) // 2
            if v[m][1] < t - TOL_NS: lo = m + 1
            else: hi = m
        while lo < len(v) and v[lo][1] <= t + TOL_NS:
            if abs(v[lo][2] - k) <= TOL_TICK:
                pairs.append((abs(v[lo][1] - t), i, v[lo]))
            lo += 1
    pairs.sort(key=lambda x: (x[0], x[1]))
    usedp, useda, M, E = set(), set(), set(), set()
    for dt, i, a in pairs:
        if i in usedp or a in useda:
            continue
        usedp.add(i); useda.add(a); M.add(a)
        if dt == 0 and a[2] == pk[i][2]:
            E.add(a)
    return M, E


def day(args):
    run, d = args
    base = os.path.join(run, d, "out", "all")
    syms = set()
    for l in open(os.path.join(base, "manifest.txt"), encoding="utf-8"):
        if l.startswith("symbols="):
            syms = set(l.strip()[8:].split(","))
    n_keep = len(KD.get(d, ()))
    pk = [k for k in KD.get(d, ()) if k[0] in syms]
    _, body = br.read_body(os.path.join(base, "signals.csv"))
    rows = list(csv.DictReader(body))
    ap = {(r["symbol"], int(r["t0_ns"]), int(r["price_tick"])) for r in rows}
    M, E = match(pk, ap)
    forms = sorted({r["form"] for r in rows})
    acc = {"m": set(), "e": set()}
    nk = {"m": defaultdict(int), "e": defaultdict(int)}
    na = {"m": defaultdict(int), "e": defaultdict(int)}
    for mode, S in (("m", M), ("e", E)):
        state = {}
        for r in rows:
            if (r["symbol"], int(r["t0_ns"]), int(r["price_tick"])) not in S:
                continue
            nk[mode][r["form"]] += 1
            g = (r["symbol"], r["day_utc"], r["form"])
            idle, stopped = state.get(g, (None, False))
            if stopped or (idle is not None and int(r["t0_ns"]) < idle):
                continue
            acc[mode].add(g + (r["signal_index"],))
            na[mode][r["form"]] += 1
            state[g] = (idle, True) if (r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended") else (int(r["idle_ns"]), False)
    ne = {"m": defaultdict(int), "e": defaultdict(int)}
    sn = {"m": defaultdict(float), "e": defaultdict(float)}
    sq = {"m": defaultdict(float), "e": defaultdict(float)}
    sd_cnt = {"m": defaultdict(set), "e": defaultdict(set)}
    reasons, dist = defaultdict(lambda: defaultdict(int)), defaultdict(list)
    _, body = br.read_body(os.path.join(base, "rounds.csv"))
    for r in csv.DictReader(body):
        key = (r["symbol"], r["day_utc"], r["form"], r["signal_index"])
        for mode in ("m", "e"):
            if key in acc[mode]:
                f = r["form"]; ne[mode][f] += 1
                x = float(r["net_bps"]); sn[mode][f] += x; sq[mode][f] += x * x
                sd_cnt[mode][f].add(r["symbol"])
                if mode == "m":
                    e, q = float(r["entry_px"]), float(r["exit_px"])
                    if e > 0:
                        dist[(f, r["reason"])].append(abs(q - e) / e * 1e4)
                    reasons[f][r["reason"]] += 1
    med = {f"{k[0]}|{k[1]}": [round(y, 2) for y in v] for k, v in dist.items()}
    dd = lambda x: {m: dict(v) for m, v in x.items()}
    return d, forms, (n_keep, len(pk), len(M), len(E)), dd(nk), dd(na), dd(ne), dd(sn), dd(sq),         {m: {f: len(v) for f, v in x.items()} for m, x in sd_cnt.items()}, {f: dict(v) for f, v in reasons.items()}, med


def main():
    global KEEP, KD
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("keep"); ap.add_argument("out")
    ap.add_argument("--procs", type=int, default=15)
    a = ap.parse_args()
    KEEP = br.load_keep(a.keep)
    KD = defaultdict(list)
    for k in KEEP[1]:
        KD[datetime.fromtimestamp(int(k[1]) / 1e9, timezone.utc).strftime("%Y-%m-%d")].append((k[0], int(k[1]), int(k[2])))
    days = sorted(x for x in os.listdir(a.run) if os.path.exists(os.path.join(a.run, x, "done")))
    with Pool(a.procs) as p:
        res = p.map(day, [(a.run, d) for d in days], chunksize=1)
    allf = sorted({f for r in res for f in r[1]})
    # per[mode][f][month] = [(d, знаменатель, n_exec, sum_net)]; mode m — сопоставленные (главный), e — точный ключ, p — все P* прогона (разбавлен)
    per = {m: defaultdict(lambda: defaultdict(list)) for m in "mep"}
    ch = defaultdict(lambda: defaultdict(int))
    sqs, sdays, dist, rs = defaultdict(float), defaultdict(int), defaultdict(list), defaultdict(lambda: defaultdict(int))
    tot = defaultdict(int)
    for d, forms, (n_keep, n_p, n_m, n_e), nk, na, ne, sn, sq, sdc, rz, med in res:
        tot["keep"] += n_keep; tot["in_run"] += n_p; tot["matched"] += n_m; tot["exact"] += n_e
        for f in allf:
            per["m"][f][d[:7]].append((d, n_m, ne["m"].get(f, 0), sn["m"].get(f, 0.0)))
            per["e"][f][d[:7]].append((d, n_e, ne["e"].get(f, 0), sn["e"].get(f, 0.0)))
            per["p"][f][d[:7]].append((d, n_p, ne["m"].get(f, 0), sn["m"].get(f, 0.0)))
            ch[f]["built"] += nk["m"].get(f, 0); ch[f]["accepted"] += na["m"].get(f, 0); ch[f]["exec"] += ne["m"].get(f, 0)
            ch[f]["built_e"] += nk["e"].get(f, 0); ch[f]["exec_e"] += ne["e"].get(f, 0)
            sqs[f] += sq["m"].get(f, 0.0); sdays[f] += sdc["m"].get(f, 0)
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
    out = {"_chain": dict(tot, days=len(days))}
    for f in allf:
        allr = {m: [x for mth in per[m][f].values() for x in mth] for m in "mep"}
        st = stat(allr["m"])
        ne = st["n_exec"]; mean = st["sum_net"] / ne if ne else 0.0
        sd = math.sqrt(max(sqs[f] / ne - mean * mean, 0.0)) if ne else 0.0
        neff = sdays[f]
        st.update({"sd_round": sd, "n_eff_symdays": neff, "mde_round_bps": 2.8 * sd / math.sqrt(neff) if neff else None})
        out[f] = {"all": st, "all_exact": stat(allr["e"]), "all_inrun": stat(allr["p"]),
                  "months": {m: stat(v) for m, v in sorted(per["m"][f].items())}, "reasons": dict(rs[f]), "chain": dict(ch[f])}
    out["_dist_median_bps"] = {k: sorted(v)[len(v) // 2] for k, v in dist.items() if v}
    json.dump(out, open(a.out + ".json", "w"), ensure_ascii=False, indent=1)
    c = out["_chain"]
    L = [f"# П-24 исходы v3 — {os.path.basename(a.keep)}, суток {len(days)}", "",
         f"Цепочка n: keep (P*) {c['keep']} → монеты прогона {c['in_run']} → сопоставлено с подходом прогона {c['matched']} "
         f"({c['matched'] / c['in_run']:.1%}; правило: монета, ±{TOL_TICK} тика, ≤{TOL_NS // 10**9} с, один к одному) = N_P (общий знаменатель) "
         f"→ из них точный ключ {c['exact']} ({c['exact'] / c['in_run']:.1%}) → сигналы формы → принятые → исполненные.", "",
         "| форма | N_P | сигналов формы | принято | исполнено | ITT бп/сигнал P* ± SE(сутки) | ITT точный ключ | ITT на всех P* прогона (разбавлен) | бп/круг | SD круга | n_эфф | MDE круга, бп |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in allf:
        v = out[f]; s_, q = v["all"], v["chain"]; e_, p_ = v["all_exact"], v["all_inrun"]
        L.append(f"| {f} | {s_['n_P']} | {q['built']} | {q['accepted']} | {q['exec']} | {s_.get('itt_bps', 0):+.3f} ± {s_.get('itt_se', 0):.3f} | "
                 f"{e_.get('itt_bps', 0):+.3f} (N {e_['n_P']}, исп. {e_['n_exec']}) | {p_.get('itt_bps', 0):+.3f} | "
                 f"{(s_.get('per_exec_bps') or 0):+.2f} | {s_['sd_round']:.1f} | {s_['n_eff_symdays']} | {(s_['mde_round_bps'] or 0):.2f} |")
    L += ["", "ITT по месяцам (бп/сигнал P*, знаменатель N_P):", "", "| форма | " + " | ".join(f"{m:02d}" for m in range(1, 10)) + " |", "|---|" + "---|" * 9]
    for f in allf:
        cells = []
        for m in range(1, 10):
            s_ = out[f]["months"].get(f"2026-{m:02d}")
            cells.append(f"{s_['itt_bps']:+.2f}" if s_ and s_["n_P"] else "-")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L += ["", "Причины выхода (круги):"]
    for f in allf:
        L.append(f"- {f}: " + ", ".join(f"{k} {n}" for k, n in sorted(out[f]["reasons"].items(), key=lambda x: -x[1])))
    L += ["", "Медиана |выход−вход|/вход, бп, по причине выхода:"]
    L += [f"- {k}: {x:.1f}" for k, x in sorted(out["_dist_median_bps"].items())]
    open(a.out + ".md", "w", encoding="utf-8").write(chr(10).join(L) + chr(10))
    print(chr(10).join(L[:16]))


if __name__ == "__main__":
    main()
