#!/usr/bin/env python3
"""TK-345 проход валидности 1–14.02: инварианты по всем единицам (сутки × символы × семейство F1–F3 × 12 наборов).
  tk345-validity-pass-check.py <pass2> <pass3> <out-dir>      env: NP (процессов, 15), EPOCH (каталог эпохи фев)
Сутки 01–04 берутся из pass2 (шарды SH=6 по 3 символа), 05–14 — из pass3 (задача = символ); pass2 05–07 — только для сверки
«группировка не влияет» (строки forms.csv равны построчно). Единица годна, если есть файл done.
Пишет <out>/coverage.txt (покрытие сутки × символ × семейство), <out>/invariants.txt (таблицы), код 1 при нарушениях (2)(3 знак)(4).
Инварианты: (2) n_signals+n_skipped одно на все наборы; набор ⊆ родителя по n_signals; сторона набора = знак dir;
(3) доля выходов ≤ 1 с от t0 по формам, стоп с gross>0; (4) gross(entry_vwap, exit_px) − net_bps ∈ {2,52; 4,41; 6,30} (ноги В-47)."""
import collections, glob, os, statistics, sys
from multiprocessing import Pool

P2, P3, OUT = sys.argv[1:4]
EPOCH = os.environ.get("EPOCH", "/data/tk046/feb/home/alpha/epochs/e-feb")
DAYS = [f"2026-02-{d:02d}" for d in range(1, 15)]
FAMS = ("F1", "F2", "F3")
SETS = ["base", "age10", "age60", "usd30k", "flow100", "fr", "btcup", "btcdn", "ret1hup", "ret1hdn", "age10usd30k", "ask"]
PARENTS = {"age10": ["base"], "age60": ["age10"], "usd30k": ["base"], "flow100": ["base"], "fr": ["base"], "btcup": ["base"],
           "btcdn": ["base"], "ret1hup": ["base"], "ret1hdn": ["base"], "age10usd30k": ["age10", "usd30k"]}
FEES = (2.52, 4.41, 6.30)   # мейкер+мейкер, мейкер+тейкер, тейкер+тейкер: 2·1,4·0,9 / (1,4+3,5)·0,9 / 2·3,5·0,9 — В-47 `leg_fee_bps`


def rd(p):
    with open(p, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh if not ln.startswith("#")]
    return lines[0].split(","), [ln.split(",") for ln in lines[1:]]


def units_of(day):
    """[(каталог единицы, семейство)] с done для суток: pass3 при day ≥ 05, иначе pass2."""
    root = P3 if int(day[-2:]) >= 5 else P2
    return [(u, os.path.basename(u).rsplit("-", 1)[1]) for u in sorted(glob.glob(f"{root}/{day}-*-F[123]")) if os.path.exists(u + "/done")]


def unit_stats(args):
    u, fam = args
    r = collections.defaultdict(int)
    forms = {}          # (набор, символ, форма) -> (n_signals, n_skipped, строка целиком)
    form_acc = collections.defaultdict(lambda: collections.Counter())
    resid = collections.defaultdict(collections.Counter)
    syms = set()
    for s in SETS:
        hd, rows = rd(f"{u}/out/{s}/forms.csv")
        ix = {h: i for i, h in enumerate(hd)}
        for row in rows:
            forms[(s, row[ix["symbol"]], row[ix["form"]])] = (int(row[ix["n_signals"]]), int(row[ix["n_skipped"]]), ",".join(row))
            syms.add(row[ix["symbol"]])
        hd, rows = rd(f"{u}/out/{s}/rounds.csv")
        ix = {h: i for i, h in enumerate(hd)}
        want = -1.0 if s == "ask" else 1.0
        for row in rows:
            form = row[ix["form"]]; reason = row[ix["reason"]]
            entry = "market" if form.startswith("market") else "single"
            a = form_acc[(fam, form)]
            a["n"] += 1; a[reason] += 1
            try:
                d = float(row[ix["dir"]])
                ep = float(row[ix["entry_vwap"]] or row[ix["entry_px"]]); xp = float(row[ix["exit_px"]]); net = float(row[ix["net_bps"]])
                hold = (int(row[ix["exit_ns"]]) - int(row[ix["t0_ns"]])) / 1e9
            except ValueError:
                a["unparsed"] += 1
                continue
            sg = 1.0 if d > 0 else -1.0
            if sg != want:
                r["side_viol_" + s] += 1
            if hold < 0:
                a["exit_before_t0"] += 1
            if hold <= 1:
                a["le1s"] += 1
                if reason == "stop":
                    a["stop_le1s"] += 1
            gross = sg * (xp / ep - 1) * 1e4
            if reason == "stop" and gross > 0:
                a["stop_gross_pos"] += 1
            resid[(entry, reason)][round(gross - net, 2)] += 1
    return u, fam, syms, forms, dict(r), {k: dict(v) for k, v in form_acc.items()}, {k: dict(v) for k, v in resid.items()}


def expected_syms(day):
    return sorted(os.path.basename(p)[: -len(f"-{day}.binlog")] for p in glob.glob(f"{EPOCH}/study/root-{day}/*.binlog"))


def main():
    os.makedirs(OUT, exist_ok=True)
    cov = open(f"{OUT}/coverage.txt", "w")
    inv = open(f"{OUT}/invariants.txt", "w")
    tasks, days_units = [], {}
    for d in DAYS:
        days_units[d] = units_of(d)
        tasks += days_units[d]
    with Pool(int(os.environ.get("NP", "15"))) as pool:
        res = pool.map(unit_stats, tasks, chunksize=1)
    by_unit = {x[0]: x for x in res}
    bad = 0
    # ---- покрытие
    tot_exp = tot_have = 0
    print("== покрытие: сутки × символ × семейство (ожидаемые = binlog суток в study/root-<сутки>); done = единица годна", file=cov)
    for d in DAYS:
        exp = expected_syms(d)
        have = collections.defaultdict(set)
        for u, fam in days_units[d]:
            for s in by_unit[u][2]:
                have[fam].add(s)
        failed = sorted(os.path.basename(p)[len(d) + 1:] for p in glob.glob(f"{P3 if int(d[-2:]) >= 5 else P2}/{d}-*/fail"))
        miss = {f: [s for s in exp if s not in have[f]] for f in FAMS}
        tot_exp += len(exp) * 3; tot_have += sum(len(exp) - len(m) for m in miss.values())
        print(f"{d}: ожидается {len(exp)} символов × 3 семейства; покрыто F1 {len(exp) - len(miss['F1'])}, F2 {len(exp) - len(miss['F2'])}, "
              f"F3 {len(exp) - len(miss['F3'])}" + (f"; fail-единицы: {' '.join(failed)}" if failed else ""), file=cov)
        for f in FAMS:
            if miss[f]:
                print(f"    нет {f}: {' '.join(miss[f])}", file=cov)
    print(f"ИТОГО: покрыто {tot_have} из {tot_exp} (сутки × символ × семейство)", file=cov)
    # ---- (2) фильтр режет
    print("== (2) фильтр режет: по (сутки, символ, форма) сверяются 12 наборов (Σ по 14 суткам)", file=inv)
    f_tot_viol = f_pairs = 0
    sub_viol = collections.Counter(); sub_n = collections.Counter(); sub_sum = collections.Counter()
    side_viol = collections.Counter()
    for u, fam, syms, forms, r, acc, resid in res:
        keys = {(sy, fo) for (_, sy, fo) in forms}
        for k in keys:
            tots = {forms[(s, *k)][0] + forms[(s, *k)][1] for s in SETS if (s, *k) in forms}
            f_pairs += 1
            if len(tots) > 1:
                f_tot_viol += 1
        for s, ps in PARENTS.items():
            for k in keys:
                if (s, *k) not in forms:
                    continue
                sub_n[s] += 1; sub_sum[s] += forms[(s, *k)][0]
                for p in ps:
                    if (p, *k) in forms and forms[(s, *k)][0] > forms[(p, *k)][0]:
                        sub_viol[(s, p)] += 1
        for kk, v in r.items():
            side_viol[kk] += v
    bad += f_tot_viol + sum(sub_viol.values()) + sum(side_viol.values())
    print(f"  n_signals+n_skipped одно на все наборы: нарушений {f_tot_viol} из {f_pairs} (сутки×символ×форма)", file=inv)
    for s, ps in PARENTS.items():
        print(f"  набор {s:12s} ⊆ {'+'.join(ps):12s}: нарушений {sum(sub_viol[(s, p)] for p in ps)} из {sub_n[s]} (символ×форма); Σn_signals {sub_sum[s]}", file=inv)
    print(f"  сторона набора = знак dir (ask — продажа, прочие — покупка): нарушений {sum(side_viol.values())}", file=inv)
    # ---- (3) стоп и тейк по формам; (4) PnL
    print("\n== (3) выходы по формам (Σ по наборам, символам, суткам): круги, доля причин, ≤1 с от t0, стоп с gross>0", file=inv)
    acc_all = collections.defaultdict(collections.Counter)
    resid_all = collections.defaultdict(collections.Counter)
    for u, fam, syms, forms, r, acc, resid in res:
        for k, v in acc.items():
            acc_all[k].update(v)
        for k, v in resid.items():
            resid_all[k].update(v)
    print(f"  {'сем':3s} {'форма':44s} {'кругов':>9s} {'stop':>7s} {'take':>7s} {'deadl':>7s} {'early':>7s} {'eat/gone':>8s} {'≤1с':>6s} {'stop≤1с':>8s} {'stop>0':>7s} {'t<t0':>5s}", file=inv)
    for (fam, form), a in sorted(acc_all.items()):
        n = a["n"]
        eg = sum(v for k, v in a.items() if k.startswith(("eat", "gone", "wall", "trail")))
        print(f"  {fam:3s} {form:44s} {n:9d} {a['stop']:7d} {a['take']:7d} {a['deadline']:7d} {a['early']:7d} {eg:8d} "
              f"{100 * a['le1s'] / max(n, 1):5.1f}% {100 * a['stop_le1s'] / max(a['stop'], 1):7.1f}% {a['stop_gross_pos']:7d} {a['exit_before_t0']:5d}", file=inv)
        bad += a["exit_before_t0"]
    reasons = collections.Counter()
    for a in acc_all.values():
        for k, v in a.items():
            if k not in ("n", "le1s", "stop_le1s", "stop_gross_pos", "exit_before_t0", "unparsed"):
                reasons[k] += v
    print(f"  причины выхода (все формы): {dict(reasons)}; не разобрано строк: {sum(a['unparsed'] for a in acc_all.values())}", file=inv)
    print("\n== (4) PnL: gross(entry_vwap, exit_px) − net_bps = издержки по (вход, причина); ожидаемые ноги В-47: 2,52 (м+м) / 4,41 (м+т) / 6,30 (т+т)", file=inv)
    nonconf = 0
    for (e, why), c in sorted(resid_all.items()):
        n = sum(c.values())
        ok = sum(v for x, v in c.items() if any(abs(x - f) <= 0.02 for f in FEES))
        top = ", ".join(f"{x}:{v}" for x, v in c.most_common(3))
        xs = sorted(c)
        print(f"  {e:6s} {why:14s} n={n:9d} в {{2,52;4,41;6,30}}±0,02: {100 * ok / n:6.2f}% (вне: {n - ok}); min={xs[0]} max={xs[-1]}; топ: {top}", file=inv)
        nonconf += n - ok
    print(f"  вне ожидаемых значений всего: {nonconf} из {sum(sum(c.values()) for c in resid_all.values())}", file=inv)
    # ---- группировка не влияет: pass2 05–07 против pass3
    print("\n== (5) группировка не влияет: pass2 (шард 3 символа/задача) против pass3 (символ/задача), сутки 05–07, строки forms.csv целиком", file=inv)
    cmp_n = cmp_diff = 0
    for d in ("2026-02-05", "2026-02-06", "2026-02-07"):
        p3 = {}
        for u, fam in units_of(d):
            for (s, sy, fo), v in by_unit[u][3].items():
                p3[(fam, s, sy, fo)] = v[2]
        for u in sorted(glob.glob(f"{P2}/{d}-*-F[123]")):
            if not os.path.exists(u + "/done"):
                continue
            fam = os.path.basename(u).rsplit("-", 1)[1]
            for s in SETS:
                hd, rows = rd(f"{u}/out/{s}/forms.csv")
                ix = {h: i for i, h in enumerate(hd)}
                for row in rows:
                    k = (fam, s, row[ix["symbol"]], row[ix["form"]])
                    if k in p3:
                        cmp_n += 1
                        if p3[k] != ",".join(row):
                            cmp_diff += 1
    bad += cmp_diff
    print(f"  сверено строк forms.csv: {cmp_n}, различий {cmp_diff}", file=inv)
    print(f"\nнарушений автопроверки (2)(время)(5): {bad}", file=inv)
    cov.close(); inv.close()
    sys.exit(1 if bad else 0)


main()
