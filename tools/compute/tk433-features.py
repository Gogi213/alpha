#!/usr/bin/env python3
"""TK-433 шаг 2 (протокол P-TK433 v9b + уточнения Судьи): контроли и окна для tk422-features.py, свои v/m, слияние в таблицу признаков.
Подкоманды:
  windows <legs-th60.csv> <legs-th240.csv> <корень свечей> <aug-корень|-> <выход-каталог> [месяц ГГГГ-ММ]
      события = не исключённые ноги, представитель кластера (θ60: E60-n, θ240: E240-n); контроли: та же монета-рынок, та же длительность,
      тот же час UTC, ±30 суток, окно [старт−60, пик] не пересекает ни одну ногу ≥ 90 мин этого инструмента (любые θ, исключённые тоже),
      ≥ доли минут со свечой, как у события; до 5, детерминированно (md5 seed 433), без взаимных пересечений.
      Пишет windows.csv (для tk422-features), own.csv (v_in, v_pre, m_in, m_pre, hist_days), controls-diag.json.
  merge <windows.csv> <own.csv> <tk422-features.csv> <выход.csv>
      таблица признаков 12 (v_in, v_pre, m_in, m_pre, buy_in, buy_pre, sf_diff, sf_corr, oi_in, fund_in, liq_in, btc_in) по окну."""
import csv, gzip, hashlib, json, math, os, sys
from bisect import bisect_right
from collections import defaultdict

MIN = 60000
SEED = 433
PRE = 60          # мин «до», протокол
NORM_D = 7        # суток нормы оборота
PER = 5
SPAN = 30         # суток ± для контроля (Судья 28.10: главный ±30, тот же час; ±7 — чувствительность)
MIN_HIST_D = 3


def month_bounds(ym):
    import datetime
    y, m = int(ym[:4]), int(ym[5:])
    a = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
    b = datetime.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return int(a.timestamp() * 1000) // MIN, int(b.timestamp() * 1000) // MIN


def load(roots, sub, sym):
    d = {}
    for r in roots:
        fn = "%s/%s/%s.csv.gz" % (r, sub, sym) if r else None
        if fn and os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                f.readline()
                for line in f:
                    p = line.split(",", 7)
                    c = float(p[4])
                    if c > 0:
                        d[int(p[0]) // MIN] = (c, float(p[6] or 0))
    return d


def mad_demeaned(d, a, b):
    """MAD×1,4826 по 1 м логдоходностям закрытий в [a,b] за вычетом среднего (соседние минуты со свечой)."""
    r = []
    for m in range(a + 1, b + 1):
        if m in d and m - 1 in d:
            r.append(math.log(d[m][0] / d[m - 1][0]))
    if len(r) < 5:
        return None
    mu = sum(r) / len(r)
    x = sorted(v - mu for v in r)
    md = x[len(x) // 2]
    y = sorted(abs(v - md) for v in x)
    return y[len(y) // 2] * 1.4826


def med(x):
    y = sorted(x)
    return y[len(y) // 2] if y else None


def merge_busy(iv):
    """Слияние интервалов занятости: тот же результат проверок пересечения, но поиск по bisect (v11: тысячи событий на инструмент)."""
    out = []
    for i0, i1 in sorted(iv):
        if out and i0 <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], i1))
        else:
            out.append((i0, i1))
    return out


def hit(mb, x, y):
    """Пересекает ли [x,y] слитую занятость mb = (merged, starts)."""
    mg, st = mb
    k = bisect_right(st, y) - 1
    return k >= 0 and mg[k][1] >= x


def own_features(d, a, e, busy):
    """a..e — минуты окна ноги [старт, пик]; busy — [(i0,i1)] минуты всех ног ≥ 90 мин инструмента (исключаются из нормы)."""
    out = {"v_in": mad_demeaned(d, a, e), "v_pre": mad_demeaned(d, a - PRE, a - 1)}
    first = min(d) if d else a
    out["hist_days"] = round((a - first) / 1440, 2)
    norm = None
    if out["hist_days"] >= MIN_HIST_D:
        q = [d[m][1] for m in range(a - NORM_D * 1440, a) if m in d and not hit(busy, m, m)]
        norm = med(q)
    for nm, (x, y) in (("m_in", (a, e)), ("m_pre", (a - PRE, a - 1))):
        s = sum(d[m][1] for m in range(x, y + 1) if m in d)
        out[nm] = s / (norm * (y - x + 1)) if norm else None
    return out


PAIR = {"linear": "spot", "futures_usdt": "spot"}


def sf_features(df, ds, a, e, sg):
    """Спот↔фьючерс (Судья 28.10): basis_pre/in/chg (bps), lead_in (лаг 1 м), fsr_in. df/ds — свечи фьючерса/спота; нет спота — пусто."""
    out = dict(basis_pre=None, basis_in=None, basis_chg=None, lead_in=None, fsr_in=None)
    if not ds:
        return out
    bas = lambda m: (df[m][0] / ds[m][0] - 1) * 1e4 if m in df and m in ds else None
    pre = [x for x in (bas(m) for m in range(a - PRE, a)) if x is not None]
    inn = [(m, bas(m)) for m in range(a, e + 1)]
    inn = [(m, x) for m, x in inn if x is not None]
    out["basis_pre"] = med(pre)
    if inn:
        out["basis_in"] = med([x for _, x in inn])
        out["basis_chg"] = (inn[-1][1] - inn[0][1]) * sg
    rf, rs = {}, {}
    for m in range(a + 1, e + 1):
        if m in df and m - 1 in df and m in ds and m - 1 in ds:
            rf[m] = math.log(df[m][0] / df[m - 1][0])
            rs[m] = math.log(ds[m][0] / ds[m - 1][0])

    def corr(x, y):
        n = len(x)
        if n < 10:
            return None
        mx, my = sum(x) / n, sum(y) / n
        sxx = sum((v - mx) ** 2 for v in x)
        syy = sum((v - my) ** 2 for v in y)
        return sum((u - mx) * (v - my) for u, v in zip(x, y)) / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None
    ks = [m for m in rf if m + 1 in rf]
    c1 = corr([rf[m] for m in ks], [rs[m + 1] for m in ks])
    c2 = corr([rs[m] for m in ks], [rf[m + 1] for m in ks])
    out["lead_in"] = None if c1 is None or c2 is None else c1 - c2
    qf = sum(df[m][1] for m in range(a, e + 1) if m in df)
    qs = sum(ds[m][1] for m in range(a, e + 1) if m in ds)
    out["fsr_in"] = math.log(qf / qs) if qf > 0 and qs > 0 else None
    return out


def cmd_windows(av):
    l60, l240, root, aug, outd = av[:5]
    ym = av[5] if len(av) > 5 else "2026-09"
    lo, hi = month_bounds(ym)
    roots = (aug if aug != "-" else None, root)
    os.makedirs(outd, exist_ok=True)
    legs = {}
    for tag, fn in (("60", l60), ("240", l240)):
        legs[tag] = list(csv.DictReader(open(fn, encoding="utf-8")))
    busy = defaultdict(set)
    for rs in legs.values():
        for r in rs:
            busy[(r["биржа"], r["рынок"], r["инструмент"])].add((int(r["t_start_ms"]) // MIN, int(r["t_peak_ms"]) // MIN))
    ev = []
    for tag, rs in legs.items():
        n = 0
        for r in rs:
            if r["исключено"] == "" and r.get("представитель") == "1":
                n += 1
                t = r.get("θ") or tag          # v11: θ = D (колонка в выборке)
                ev.append((t, "E%s-%d" % (t, n), r))
    byins = defaultdict(list)
    for e in ev:
        byins[(e[2]["биржа"], e[2]["рынок"], e[2]["инструмент"])].append(e)
    wins, own, diag = [], [], {"событий": defaultdict(int), "контролей": defaultdict(int), "без_контролей": [], "короткая_история": []}
    for (ex, mk, sym), L in sorted(byins.items()):
        sub = "%s-%s" % (ex, mk)
        d = load(roots, sub, sym)
        dsp = load(roots, "%s-%s" % (ex, PAIR[mk]), sym) if mk in PAIR else {}
        mg = merge_busy(busy[(ex, mk, sym)])
        bz = (mg, [x[0] for x in mg])
        for tag, eid, r in sorted(L, key=lambda x: x[1]):
            a, e = int(r["t_start_ms"]) // MIN, int(r["t_peak_ms"]) // MIN
            dur = e - a
            sg = "+1" if r["знак"] == "+" else "-1"
            frac = sum(1 for m in range(a, e + 1) if m in d) / (dur + 1)
            wins.append(dict(id=eid, биржа=ex, рынок=mk, инструмент=sym, t_start=a * MIN, t_peak=e * MIN, t_end=(e + 1) * MIN,
                             t_pre=(a - PRE) * MIN, знак=sg, роль="event", event_id=eid, θ=tag))
            o = own_features(d, a, e, bz)
            o.update(sf_features(d, dsp, a, e, int(sg)))
            own.append(dict(id=eid, **o))
            diag["событий"][tag] += 1
            if o["hist_days"] < MIN_HIST_D:
                diag["короткая_история"].append(eid)
            hs = a - a % 60
            cand = []
            for dd in range(-SPAN, SPAN + 1):
                if dd == 0:
                    continue
                for k in range(0, 60, 5):
                    s = hs + dd * 1440 + k
                    pre_s, end_s = s - PRE, s + dur
                    if pre_s < lo or end_s >= hi:
                        continue
                    if hit(bz, pre_s, end_s):
                        continue
                    if s not in d or end_s not in d:
                        continue
                    if sum(1 for m in range(s, end_s + 1) if m in d) < frac * (dur + 1):
                        continue
                    cand.append((hashlib.md5(("%d|%s|%d" % (SEED, eid, s)).encode()).hexdigest(), s))
            cand.sort()
            got = []
            for _, s in cand:
                if all(s + dur < g - PRE or s - PRE > g + dur for g in got):
                    got.append(s)
                    if len(got) == PER:
                        break
            if not got:
                diag["без_контролей"].append(eid)
            for j, s in enumerate(got, 1):
                cid = "C-%s-%d" % (eid, j)
                wins.append(dict(id=cid, биржа=ex, рынок=mk, инструмент=sym, t_start=s * MIN, t_peak=(s + dur) * MIN,
                                 t_end=(s + dur + 1) * MIN, t_pre=(s - PRE) * MIN, знак=sg, роль="control", event_id=eid, θ=tag))
                oc = own_features(d, s, s + dur, bz)
                oc.update(sf_features(d, dsp, s, s + dur, int(sg)))
                own.append(dict(id=cid, **oc))
                diag["контролей"][tag] += 1
    with open(os.path.join(outd, "windows.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(wins[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(wins)
    with open(os.path.join(outd, "own.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["id", "v_in", "v_pre", "m_in", "m_pre", "hist_days", "basis_pre", "basis_in", "basis_chg", "lead_in", "fsr_in"], lineterminator="\n")
        w.writeheader()
        for o in own:
            w.writerow({k: ("" if v is None else v) for k, v in o.items()})
    diag = {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in diag.items()}
    json.dump(diag, open(os.path.join(outd, "controls-diag.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if isinstance(v, dict) else len(v)) for k, v in diag.items()}, ensure_ascii=False))


def num(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def cmd_merge(av):
    wf, of, ff, outp = av[:4]
    W = {r["id"]: r for r in csv.DictReader(open(wf, encoding="utf-8"))}
    O = {r["id"]: r for r in csv.DictReader(open(of, encoding="utf-8"))}
    F = {r["id"]: r for r in csv.DictReader(open(ff, encoding="utf-8"))}
    cols = ["id", "event_id", "роль", "θ", "биржа", "рынок", "инструмент", "знак", "t_start", "t_peak", "hist_days",
            "v_in", "v_pre", "m_in", "m_pre", "buy_in", "buy_pre", "sf_diff", "sf_corr", "oi_in", "fund_in", "liq_in", "btc_in",
            "basis_pre", "basis_in", "basis_chg", "lead_in", "fsr_in"]
    miss = set()
    with open(outp, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(cols)
        for i, w in W.items():
            o, x = O.get(i, {}), F.get(i, {})
            sg = 1 if w["знак"] == "+1" else -1

            def g(k):
                if k not in x:
                    miss.add(k)
                return num(x.get(k))
            ib, ip = g("flow_imb_leg"), g("flow_imb_pre")      # imb в ориентации хода: +1 = агрессор по ходу
            buy = lambda v: None if v is None else (1 + sg * v) / 2   # доля рыночных покупок: вверх — по ходу, вниз — против
            sd = g("spot_minus_fut_leg_bps")
            ll, ls = g("liq_long_usd_leg"), g("liq_short_usd_leg")
            rec = [i, w["event_id"], w["роль"], w["θ"], w["биржа"], w["рынок"], w["инструмент"], w["знак"], w["t_start"], w["t_peak"],
                   o.get("hist_days", ""), o.get("v_in", ""), o.get("v_pre", ""), o.get("m_in", ""), o.get("m_pre", ""),
                   buy(ib), buy(ip), None if sd is None else abs(sd) / 100, g("spot_corr_leg"), g("oi_pct_leg"),
                   g("funding_last_ctx"), None if ll is None and ls is None else (ll or 0) + (ls or 0), g("btc_ret_bps_leg"),
                   o.get("basis_pre", ""), o.get("basis_in", ""), o.get("basis_chg", ""), o.get("lead_in", ""), o.get("fsr_in", "")]
            wr.writerow(["" if v is None else v for v in rec])
    print("нет колонок в tk422:", sorted(miss))


if __name__ == "__main__":
    {"windows": cmd_windows, "merge": cmd_merge}[sys.argv[1]](sys.argv[2:])
