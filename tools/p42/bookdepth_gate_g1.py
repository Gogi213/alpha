#!/usr/bin/env python3
"""П-42 §13.21 (TK-595), ворота G-B1: к какому моменту относится метка `timestamp` строки Binance USD-M `bookDepth`.

Идея: из полос ±0.2 % снимка восстанавливаем середину стакана
    ask_px = notional(+0.2) / depth(+0.2),  bid_px = notional(-0.2) / depth(-0.2),  mid_hat = (ask_px + bid_px) / 2
(симметричные смещения VWAP внутри полос сокращаются) и сравниваем с ценой последней сделки ленты aggTrades на моменте
ts + δ. Лаг снимка — δ*, при котором расхождение минимально. Правило причинности «снимок с ts + 30 с <= sig» верно,
если снимок описывает рынок не позже ts + L при L <= 30.

  python3 -I bookdepth_gate_g1.py --auto 30 [--seed 7] [--pairs SYM:ДЕНЬ,...] [--mixed-days ДЕНЬ,...] [--out .../result.json]
      выборка монето-дней (BTC/ETH/SOL + поровну ликвидных / средних / мелких по рангу размера архива aggTrades),
      δ* по каждому монето-дню и таблицы по режимам метки (A: δ* > -8 с, B: иначе), дни смены режима — в --mixed-days;
  python3 -I bookdepth_gate_g1.py --scan BTCUSDT,ETHUSDT [--block 3600]
      по всем дням охвата: δ* суток и по блокам суток (календарь смены режимов);
  python3 -I bookdepth_gate_g1.py --phase "A=BTCUSDT:2026-09-15,...;B=BTCUSDT:2026-09-25,..."
      δ* отдельно по фазе метки внутри 30-секундной сетки (ts mod 30).

Три оценки δ* на сетке 1 с (δ от -120 до +120):
  abs  — argmin среднего |ln(mid_hat / p_лента(ts+δ))| (основная, как в задании);
  sq   — argmin среднего квадрата той же разности (чувствительнее к выбросам);
  corr — argmax корреляции приращений mid_hat между соседними снимками и приращений ленты на тех же ts+δ
         (не зависит от постоянного смещения уровня).
Результат TK-595 G-B1: docs/findings/p42-bookdepth-g1-2026-11-18.md.
Запускать через `systemd-run` (nice 19, idle IO); только чтение: ничего не пишет, кроме --out.
"""
import argparse
import datetime as dt
import json
import os
import random
import sys
import zipfile

import numpy as np
import pandas as pd

BOOK_ROOT = "/data/p42-book/zip"
TAPE_ROOT = "/data/aggtrades/binance"
DAY_FIRST, DAY_LAST = "2026-09-14", "2026-10-14"  # общий охват bookDepth
REF_DAY = "2026-09-29"  # день для ранжирования монет по ликвидности (размер архива aggTrades)
MANDATORY = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DELTAS = np.arange(-120, 121)  # с
TABLE = [-90, -60, -30, -15, 0, 15, 30, 60, 90]
MARGIN = 120  # с от границ суток: снимки у края не берём (ленте не хватает соседних суток)


def load_book(root, sym, day):
    """Снимки суток: ts (с, UTC) и суммы глубины/объёма в полосах ±0.2 %. Одна строка = один снимок."""
    path = "%s/%s/%s-bookDepth-%s.zip" % (root, sym, sym, day)
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f, dtype={"timestamp": str})
    pct = np.rint(df["percentage"].to_numpy(dtype=np.float64) * 10).astype(np.int64)
    n_ts_all = df["timestamp"].nunique()

    def band(code):
        b = df.loc[pct == code, ["timestamp", "depth", "notional"]].drop_duplicates("timestamp")
        return b.set_index("timestamp")

    ask, bid = band(2), band(-2)
    j = ask.join(bid, how="inner", lsuffix="_a", rsuffix="_b").sort_index()
    ts = pd.to_datetime(pd.Series(j.index), format="%Y-%m-%d %H:%M:%S").to_numpy().astype("datetime64[s]").astype(np.int64)
    ok = (j["depth_a"].to_numpy() > 0) & (j["depth_b"].to_numpy() > 0) & (j["notional_a"].to_numpy() > 0) & (j["notional_b"].to_numpy() > 0)
    ask_px = j["notional_a"].to_numpy() / np.where(ok, j["depth_a"].to_numpy(), 1.0)
    bid_px = j["notional_b"].to_numpy() / np.where(ok, j["depth_b"].to_numpy(), 1.0)
    mid = (ask_px + bid_px) / 2
    return {"ts": ts, "mid": mid, "ok": ok, "n_ts_all": int(n_ts_all), "n_both": int(len(j))}


def load_tape(root, sym, day):
    """Лента aggTrades суток: время сделки (мс) и цена, по времени."""
    path = "%s/%s/%s-aggTrades-%s.zip" % (root, sym, sym, day)
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            head = f.readline().decode()
        with z.open(z.namelist()[0]) as f:
            if head.startswith("agg_trade_id"):
                df = pd.read_csv(f, usecols=["price", "transact_time"], dtype={"price": np.float64, "transact_time": np.int64})
            else:  # без заголовка: agg_id,price,qty,first,last,time,is_buyer_maker
                df = pd.read_csv(f, header=None, usecols=[1, 5], dtype={1: np.float64, 5: np.int64}).rename(
                    columns={1: "price", 5: "transact_time"})
    t = df["transact_time"].to_numpy()
    p = df["price"].to_numpy()
    if len(t) > 1 and np.any(np.diff(t) < 0):
        o = np.argsort(t, kind="stable")
        t, p = t[o], p[o]
    return t, p


def cadence(ts):
    """Разрывы между соседними метками (с): min/p1/median/p99/max, доля 25..35 с, число разрывов > 35 с."""
    g = np.diff(ts)
    if len(g) == 0:
        return None
    return {"n_snap": int(len(ts)), "min": int(g.min()), "p1": float(np.percentile(g, 1)), "median": float(np.median(g)),
            "p99": float(np.percentile(g, 99)), "max": int(g.max()), "share_25_35": float(np.mean((g >= 25) & (g <= 35))),
            "n_gap_gt35": int(np.sum(g > 35))}


def _wins(x, q=0.5):
    lo, hi = np.percentile(x, [q, 100 - q])
    return np.clip(x, lo, hi)


def evaluate(book, tape_t, tape_p, day, deltas=DELTAS):
    """По дельтам: средние |ln(mid_hat/p)|, знаковое, квадрат, медиана |.|, корреляция приращений; n — число снимков."""
    day0 = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    ts, mid = book["ts"], book["mid"]
    keep = book["ok"] & (ts >= day0 + MARGIN) & (ts <= day0 + 86400 - MARGIN)
    ts, mid = ts[keep], mid[keep]
    # ленте нужна хотя бы одна сделка до самого раннего ts + δ_min
    idx0 = np.searchsorted(tape_t, (ts + deltas[0]) * 1000, side="right") - 1
    good = idx0 >= 0
    ts, mid = ts[good], mid[good]
    n = len(ts)
    if n < 100:
        return None
    lnp = np.empty((n, len(deltas)))
    for k, d in enumerate(deltas):
        idx = np.searchsorted(tape_t, (ts + d) * 1000, side="right") - 1
        lnp[:, k] = np.log(tape_p[idx])
    lnm = np.log(mid)
    diff = lnm[:, None] - lnp
    a = np.abs(diff)
    out = {"n": int(n), "abs": a.mean(0), "sgn": diff.mean(0), "sq": (diff * diff).mean(0), "med": np.median(a, 0)}
    # корреляция приращений: пары соседних снимков с разрывом 20..45 с
    gap = np.diff(ts)
    pair = (gap >= 20) & (gap <= 45)
    corr = np.full(len(deltas), np.nan)
    if pair.sum() > 100:
        rh = _wins(lnm[1:][pair] - lnm[:-1][pair])
        for k in range(len(deltas)):
            rt = _wins(lnp[1:, k][pair] - lnp[:-1, k][pair])
            if rh.std() > 0 and rt.std() > 0:
                corr[k] = np.corrcoef(rh, rt)[0, 1]
    out["corr"] = corr
    out["n_pairs"] = int(pair.sum())
    out["_ts"], out["_diff"] = ts, diff  # для блочного разбора; в JSON не пишется
    return out


def block_argmin(ts, diff, day, block, deltas=DELTAS):
    """δ* по блокам суток длиной `block` с: [(начало блока, снимков, argmin среднего |Δ|, |Δ|(-15) - |Δ|(-1) в bps)]."""
    day0 = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    k15, k1 = int(np.where(deltas == -15)[0][0]), int(np.where(deltas == -1)[0][0])
    res = []
    b = (ts - day0) // block
    for j in np.unique(b):
        m = b == j
        if m.sum() < 30:
            continue
        a = np.abs(diff[m]).mean(0)
        res.append((int(j * block), int(m.sum()), int(deltas[int(np.argmin(a))]), float((a[k15] - a[k1]) * 1e4)))
    return res


def pick_auto(n, seed):
    """Стратифицированная выборка монето-дней: обязательные BTC/ETH/SOL + поровну из ликвидных / средних / мелких
    (ранг по размеру архива aggTrades в REF_DAY), день — случайный из охвата bookDepth."""
    rows = []
    for sym in sorted(os.listdir(TAPE_ROOT)):
        p = "%s/%s/%s-aggTrades-%s.zip" % (TAPE_ROOT, sym, sym, REF_DAY)
        b = "%s/%s" % (BOOK_ROOT, sym)
        if os.path.exists(p) and os.path.isdir(b):
            rows.append((os.path.getsize(p), sym))
    rows.sort(reverse=True)
    syms = [s for _, s in rows if s not in MANDATORY]
    rnd = random.Random(seed)
    third = (n - len(MANDATORY)) // 3
    extra = (n - len(MANDATORY)) - 3 * third
    strata = [syms[:60], syms[60:250], syms[250:]]
    chosen = list(MANDATORY)
    for i, st in enumerate(strata):
        chosen += rnd.sample(st, third + (1 if i < extra else 0))
    d0 = dt.date.fromisoformat(DAY_FIRST)
    nd = (dt.date.fromisoformat(DAY_LAST) - d0).days + 1
    pairs = []
    for s in chosen:
        for _ in range(50):
            day = (d0 + dt.timedelta(rnd.randrange(nd))).isoformat()
            if os.path.exists("%s/%s/%s-bookDepth-%s.zip" % (BOOK_ROOT, s, s, day)) and os.path.exists(
                    "%s/%s/%s-aggTrades-%s.zip" % (TAPE_ROOT, s, s, day)):
                pairs.append((s, day))
                break
    return pairs


def argmin_d(v, deltas=DELTAS):
    return int(deltas[int(np.nanargmin(v))])


def argmax_d(v, deltas=DELTAS):
    return int(deltas[int(np.nanargmax(v))])


def report(res, label):
    """Таблица пула (среднее по монето-дням, веса равные), δ* по монето-дням, каденс, форма кривой у минимума."""
    A = np.array([r["abs"] for r in res])
    S = np.array([r["sgn"] for r in res])
    C = np.array([r["corr"] for r in res])
    Anorm = A / A.min(1, keepdims=True)
    print("\n== %s: среднее по монето-дням (веса равные) ==" % label)
    print("%6s %12s %12s %12s %10s" % ("δ, с", "|Δ|, bps", "|Δ|/свой min", "знак, bps", "corr"))
    for d in TABLE:
        k = int(np.where(DELTAS == d)[0][0])
        print("%+6d %12.3f %12.4f %12.3f %10.4f" % (d, A[:, k].mean() * 1e4, Anorm[:, k].mean(), S[:, k].mean() * 1e4, np.nanmean(C[:, k])))
    kb = int(np.argmin(A.mean(0)))
    kn = int(np.argmin(Anorm.mean(0)))
    kc = int(np.nanargmax(np.nanmean(C, 0)))
    print("argmin |Δ| (1 с): %+d с; argmin |Δ|/min: %+d с; argmax corr: %+d с (corr max %.4f)" % (
        DELTAS[kb], DELTAS[kn], DELTAS[kc], np.nanmean(C, 0)[kc]))
    print("кривая вокруг минимума (δ: |Δ| bps, |Δ|/min):")
    for k in range(max(kb - 8, 0), min(kb + 9, len(DELTAS))):
        print("  %+4d: %.3f  %.4f" % (DELTAS[k], A[:, k].mean() * 1e4, Anorm[:, k].mean()))
    print("δ* по монето-дням: min / p25 / median / p75 / max")
    summ = {"n": len(res), "argmin_pool": int(DELTAS[kb]), "argmin_norm": int(DELTAS[kn]), "argmax_corr_pool": int(DELTAS[kc]),
            "table_abs_bps": {str(d): float(A[:, int(np.where(DELTAS == d)[0][0])].mean() * 1e4) for d in TABLE}}
    for key in ("argmin_abs", "argmin_sq", "argmin_med", "argmax_corr", "argmin_abs_15"):
        v = np.array([r[key] for r in res if r[key] is not None], dtype=float)
        q = np.percentile(v, [0, 25, 50, 75, 100])
        summ[key] = [float(x) for x in q]
        print("  %-14s %+6.0f %+6.0f %+6.0f %+6.0f %+6.0f" % (key, *q))
    cmax = [float(np.nanmax(r["corr"])) for r in res if np.isfinite(r["corr"]).any()]
    print("corr приращений в максимуме по монето-дням: min %.3f median %.3f max %.3f" % (min(cmax), float(np.median(cmax)), max(cmax)))
    cad = [r["cad"] for r in res]
    summ["cad"] = {"min": min(c["min"] for c in cad), "median": float(np.median([c["median"] for c in cad])), "max": max(c["max"] for c in cad),
                   "share_25_35": float(np.mean([c["share_25_35"] for c in cad])), "n_gap_gt35": int(sum(c["n_gap_gt35"] for c in cad)),
                   "p1": [min(c["p1"] for c in cad), max(c["p1"] for c in cad)], "p99": [min(c["p99"] for c in cad), max(c["p99"] for c in cad)]}
    print("каденс меток: min=%d med=%g max=%d с; доля разрывов 25..35 с = %.4f; разрывов > 35 с: %d; p1 %.1f..%.1f; p99 %.1f..%.1f" % (
        summ["cad"]["min"], summ["cad"]["median"], summ["cad"]["max"], summ["cad"]["share_25_35"], summ["cad"]["n_gap_gt35"],
        *summ["cad"]["p1"], *summ["cad"]["p99"]))
    return summ


def phase(spec):
    """δ* в зависимости от фазы метки внутри 30-секундной сетки (ts mod 30). spec: "ИМЯ=SYM:ДЕНЬ,SYM:ДЕНЬ;ИМЯ=..."."""
    for grp in spec.split(";"):
        name, lst = grp.split("=")
        acc = {}
        for item in lst.split(","):
            sym, day = item.split(":")
            book = load_book(BOOK_ROOT, sym, day)
            t, p = load_tape(TAPE_ROOT, sym, day)
            ev = evaluate(book, t, p, day)
            ts, diff = ev["_ts"], ev["_diff"]
            ph = ts % 30
            for k in range(5):  # 4 = «4 и больше»
                m = (ph == k) if k < 4 else (ph >= 4)
                if m.sum() >= 5:
                    a = acc.setdefault(k, [np.zeros(len(DELTAS)), 0])
                    a[0] += np.abs(diff[m]).sum(0)
                    a[1] += int(m.sum())
        tot = sum(v[1] for v in acc.values())
        for k in sorted(acc):
            v = acc[k][0] / acc[k][1]
            print("%s: ts mod 30 %s  доля %.3f (n=%d)  δ* = %+d с  min |Δ| = %.2f bps" % (
                name, k if k < 4 else ">=4", acc[k][1] / tot, acc[k][1], DELTAS[int(np.argmin(v))], v.min() * 1e4), flush=True)


def scan(syms, block):
    """Для каждой монеты и каждого дня охвата: δ* суток (мин. |Δ|, шаг 1 с) и строка δ* по блокам суток."""
    d0 = dt.date.fromisoformat(DAY_FIRST)
    nd = (dt.date.fromisoformat(DAY_LAST) - d0).days + 1
    for sym in syms:
        for n in range(nd):
            day = (d0 + dt.timedelta(n)).isoformat()
            if not (os.path.exists("%s/%s/%s-bookDepth-%s.zip" % (BOOK_ROOT, sym, sym, day))
                    and os.path.exists("%s/%s/%s-aggTrades-%s.zip" % (TAPE_ROOT, sym, sym, day))):
                print("%-12s %s нет файла" % (sym, day), flush=True)
                continue
            book = load_book(BOOK_ROOT, sym, day)
            t, p = load_tape(TAPE_ROOT, sym, day)
            ev = evaluate(book, t, p, day)
            if ev is None:
                print("%-12s %s мало снимков" % (sym, day), flush=True)
                continue
            bl = block_argmin(ev["_ts"], ev["_diff"], day, block)
            print("%-12s %s d*=%+4d min=%5.2fbps  |Δ|(-15)-|Δ|(-1)=%+6.2f  блоки(%d с): %s" % (
                sym, day, argmin_d(ev["abs"]), ev["abs"].min() * 1e4, (ev["abs"][DELTAS == -15][0] - ev["abs"][DELTAS == -1][0]) * 1e4,
                block, " ".join("%+d" % x[2] for x in bl)), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", help="SYM:YYYY-MM-DD,SYM:YYYY-MM-DD,... (добавляются к --auto)")
    ap.add_argument("--auto", type=int, help="число монето-дней (стратифицированно, обязательные BTC/ETH/SOL)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scan", help="SYM,SYM,...: по всем дням охвата — δ* суток и по блокам (--block с); режим смены семантики метки")
    ap.add_argument("--block", type=int, default=3600)
    ap.add_argument("--phase", help='"A=SYM:ДЕНЬ,SYM:ДЕНЬ;B=SYM:ДЕНЬ,...": δ* по фазе метки внутри 30-секундной сетки (ts mod 30)')
    ap.add_argument("--mixed-days", default="", help="дни смены режима метки (через запятую): монето-дни исключаются из таблиц по режимам")
    ap.add_argument("--out", default="/data/p42-book/g1/result.json")
    a = ap.parse_args()
    if a.scan:
        scan(a.scan.split(","), a.block)
        return
    if a.phase:
        phase(a.phase)
        return
    if not (a.pairs or a.auto):
        ap.error("--pairs или --auto")
    pairs = pick_auto(a.auto, a.seed) if a.auto else []
    if a.pairs:  # явные пары добавляются к автовыборке
        pairs += [tuple(x.split(":")) for x in a.pairs.split(",")]
    print("pairs:", ",".join("%s:%s" % p for p in pairs), flush=True)

    res = []
    for sym, day in pairs:
        book = load_book(BOOK_ROOT, sym, day)
        t, p = load_tape(TAPE_ROOT, sym, day)
        ev = evaluate(book, t, p, day)
        cad = cadence(book["ts"])
        if ev is None:
            print("%-14s %s  пропуск: мало снимков" % (sym, day), flush=True)
            continue
        r = {"sym": sym, "day": day, "n_trades": int(len(t)), "n_ts_all": book["n_ts_all"], "n_both": book["n_both"], "cad": cad,
             **{k: v for k, v in ev.items() if not k.startswith("_")}}
        r["abs_bps_min"] = float(ev["abs"].min() * 1e4)
        r["argmin_abs"] = argmin_d(ev["abs"])
        r["argmin_sq"] = argmin_d(ev["sq"])
        r["argmin_med"] = argmin_d(ev["med"])
        r["argmax_corr"] = argmax_d(ev["corr"]) if np.isfinite(ev["corr"]).any() else None
        kk = [int(np.where(DELTAS == d)[0][0]) for d in TABLE]
        r["argmin_abs_15"] = int(TABLE[int(np.argmin(ev["abs"][kk]))])
        res.append(r)
        print("%-14s %s trades=%8d snaps=%4d used=%4d gap=%d/%g/%d  abs_min=%6.2fbps  d*: abs=%+4d sq=%+4d med=%+4d corr=%s 15s=%+d" % (
            sym, day, len(t), cad["n_snap"], ev["n"], cad["min"], cad["median"], cad["max"], r["abs_bps_min"], r["argmin_abs"],
            r["argmin_sq"], r["argmin_med"], ("%+4d" % r["argmax_corr"]) if r["argmax_corr"] is not None else "  na",
            r["argmin_abs_15"]), flush=True)

    if not res:
        sys.exit("нет данных")
    mixed = set(a.mixed_days.split(",")) if a.mixed_days else set()
    for r in res:
        r["regime"] = "A" if r["argmin_abs"] > -8 else "B"  # A: метка ≈ момент снимка (δ* ≈ -1…-2 с); B: δ* ≈ -15 с
        r["mixed"] = r["day"] in mixed
    summ = {"all": report(res, "все %d монето-дней" % len(res))}
    for g in ("A", "B"):
        sub = [r for r in res if r["regime"] == g and not r["mixed"]]
        if sub:
            summ[g] = report(sub, "режим %s без дней смены (%d монето-дней)" % (g, len(sub)))
    print("\nдни смены режима, исключены из групп: %s" % (", ".join("%s %s(%s)" % (r["sym"], r["day"], r["regime"])
                                                            for r in res if r["mixed"]) or "нет"))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"deltas": DELTAS.tolist(), "pairs": [[r["sym"], r["day"]] for r in res], "summary": summ,
                   "per": [{k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in r.items()} for r in res]}, f)
    print("json:", a.out)


if __name__ == "__main__":
    main()
