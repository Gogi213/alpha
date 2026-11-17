"""П-42 §13.20 п. 6 (TK-594): ворота — независимые реализации признаков (построчное чтение zip/CSV модулем `csv`, чистый Python, без numpy/pandas в расчёте),
проверка «нет заглядывания», выравнивание `metrics`, источник фандинга.

Использование (srv):
  python3 tk594_gate.py flow   <episodes.parquet> <feat.parquet> <выход.json> [n=220] [base-date]  — G2: поток на n эпизодах (≥ 200)
  python3 tk594_gate.py deriv  <episodes.parquet> <feat.parquet> <выход.json> [n=220] [base-date]  — G2: производные на n эпизодах
  python3 tk594_gate.py nolook <episodes.parquet> <выход.json> [n=60] [base-date]                  — G3: данные обрезаны на sig_ms, за границей мусор
  python3 tk594_gate.py align  <выход.json> [n=10]                                                  — G1: тейкер-отношение metrics против aggTrades
  python3 tk594_gate.py fund   <выход.json>                                                         — G6: месячный архив 09.2026 против funding.csv
Расхождение — ненулевой код возврата.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import io
import json
import math
import os
import random
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk594_feat as F  # noqa: E402  (константы и пути; расчёт здесь независимый)

DAY = 86_400_000
HOUR = 3_600_000
MIN = 60_000
TRADES, METRICS, PREMIUM, FUND_CSV, FUND_M = F.TRADES, F.METRICS, F.PREMIUM, F.FUND_CSV, F.FUND_M
EDGES = [10.0 ** ((j - 300) / 100.0) for j in range(1201)]
TOL = 1e-9


def dstr(d: int) -> str:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=d)).isoformat()


def close(a, b) -> bool:
    if a != a and b != b:
        return True
    if a != a or b != b:
        return False
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))


# ───────────────────────────── чтение построчно ─────────────────────────────

_trade_cache: dict = {}


def trades_day(sym: str, d: int):
    """[(t, v, buy)] суток в порядке файла (устойчивая сортировка, если время убывало) или None."""
    key = (sym, d)
    if key in _trade_cache:
        return _trade_cache[key]
    name = f"{sym}-aggTrades-{dstr(d)}"
    out = None
    for root in TRADES:
        path = f"{root}/{sym}/{name}.zip"
        if os.path.exists(path):
            out = []
            with zipfile.ZipFile(path) as zf, zf.open(name + ".csv") as fh:
                rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
                head = next(rd)
                ip, iq, it, im = head.index("price"), head.index("quantity"), head.index("transact_time"), head.index("is_buyer_maker")
                for r in rd:
                    out.append((int(r[it]), float(r[ip]) * float(r[iq]), r[im].strip().lower() != "true"))
            if any(out[i][0] > out[i + 1][0] for i in range(len(out) - 1)):
                out.sort(key=lambda x: x[0])
            break
    if len(_trade_cache) > 24:
        _trade_cache.pop(next(iter(_trade_cache)))
    _trade_cache[key] = out
    return out


def hist_hours(sym: str, d: int):
    """{абсолютный час: {корзина: число записей}} суток по общим границам."""
    recs = trades_day(sym, d)
    out: dict = {}
    if recs is None:
        return out
    for t, v, _ in recs:
        h = min(max(t // HOUR, d * 24), d * 24 + 23)
        j = bisect.bisect_right(EDGES, v) - 1
        j = min(max(j, 0), 1199)
        out.setdefault(h, {}).setdefault(j, 0)
        out[h][j] += 1
    return out


# ───────────────────────────── поток «в лоб» ─────────────────────────────

def flow_brute(sym: str, sig: int, s: int) -> dict:
    nan = float("nan")
    f = {k: nan for k in F.FLOW}
    d = sig // DAY
    recs = []
    for dd in (d - 1, d):
        x = trades_day(sym, dd)
        if x:
            recs += x
    pre = [r for r in recs if r[0] < sig]

    def agg(a_ms: int, b_ms: int):
        Vt = Vb = 0.0
        N = Nb = 0
        for t, v, bb in pre:
            if sig - a_ms <= t < sig - b_ms:
                Vt += v
                N += 1
                if bb:
                    Vb += v
                    Nb += 1
        return Vt, Vb, N, Nb

    for w in F.WIN_S:
        Vt, Vb, N, Nb = agg(w * 1000, 0)
        if N > 0:
            f[f"tfv{w}"] = s * (2.0 * Vb - Vt) / Vt if Vt > 0 else nan
            f[f"tfn{w}"] = s * (2.0 * Nb - N) / N
    V1, _, N1, _ = agg(60_000, 0)
    Vp, _, Np, _ = agg(31 * MIN, MIN)
    if Vp > 0:
        f["acc_v"] = (V1 / 60.0) / (Vp / 1800.0)
    if Np > 0:
        f["acc_n"] = (N1 / 60.0) / (Np / 1800.0)
    # q99: 168 полных часов до последнего полного часа ≤ sig
    h_end = sig // HOUR
    counts = {}
    hours_with = 0
    first_t = None
    for dd in range(d - 8, d + 1):
        x = trades_day(sym, dd)
        if x:
            if first_t is None:
                first_t = x[0][0] if x else None
    cnt_by_hour = {}
    for dd in range(d - 8, d + 1):
        for h, c in hist_hours(sym, dd).items():
            if h_end - 168 <= h < h_end:
                cnt_by_hour[h] = c
    for h, c in cnt_by_hour.items():
        if sum(c.values()) > 0:
            hours_with += 1
        for j, n in c.items():
            counts[j] = counts.get(j, 0) + n
    q99 = None
    if hours_with >= 24:
        total = sum(counts.values())
        if total > 0:
            q99 = float("inf")
            ge = 0
            best = None
            for j in range(1199, -1, -1):
                ge += counts.get(j, 0)
                if ge <= 0.01 * total:
                    best = j
                else:
                    break
            if best is not None:
                q99 = EDGES[best]
    if q99 is not None:
        for w, key in ((300, "big300"), (1800, "big1800")):
            vt = sum(v for t, v, _ in pre if sig - w * 1000 <= t)
            if vt > 0:
                f[key] = sum(v for t, v, _ in pre if sig - w * 1000 <= t and v >= q99) / vt
    has24 = first_t is not None and first_t <= sig - DAY
    V5, Vb5, N5, _ = agg(300_000, 0)
    if has24:
        V24 = sum(v for t, v, _ in pre if t >= sig - DAY)
        if V24 > 0:
            f["tfp300"] = s * (2.0 * Vb5 - V5) / (V24 / 288.0)
        # vpin
        seg = [(v, bb) for t, v, bb in pre if t >= sig - DAY]
        n = len(seg)
        if n > 0:
            tot_all = 0.0
            cum = []
            for v, _ in seg:
                tot_all += v
                cum.append(tot_all)
            if tot_all > 0:
                idx = []
                i = 0
                for k in range(1, 50):
                    target = k * tot_all / 50.0 * (1.0 - 1e-12)
                    while cum[i] < target:
                        i += 1
                    idx.append(i)
                e = [0] + [x + 1 for x in idx] + [n]
                ims = []
                for b in range(50):
                    tt = bb_ = 0.0
                    for v, buy in seg[e[b]:e[b + 1]]:
                        tt += v
                        if buy:
                            bb_ += v
                    if tt > 0:
                        ims.append(abs(2.0 * bb_ - tt) / tt)
                if len(ims) >= 10:
                    f["vpin"] = sum(ims) / len(ims)
    if N5 > 0 and V5 > 0:
        f["lmsz300"] = math.log10(V5 / N5)
        Vq, _, Nq, _ = agg(35 * MIN, 5 * MIN)
        if Nq > 0 and Vq > 0:
            f["msz_r"] = (V5 / N5) / (Vq / Nq)
    return f


# ───────────────────────────── производные «в лоб» ─────────────────────────────

def ms_of(ts: str) -> int:
    return int(dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def metrics_rows(sym: str, d: int):
    path = f"{METRICS}/{sym}/{sym}-metrics-{dstr(d)}.zip"
    out = {}
    if not os.path.exists(path):
        return out
    with zipfile.ZipFile(path) as zf, zf.open(f"{sym}-metrics-{dstr(d)}.csv") as fh:
        rd = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
        for r in rd:
            out[ms_of(r["create_time"])] = r
    return out


def deriv_brute(sym: str, sig: int, turn24h: float) -> dict:
    nan = float("nan")
    f = {k: nan for k in F.DERIV}
    Ts = ((sig - 10 * MIN) // 300_000) * 300_000
    rows = {}
    first = None
    d_sig = sig // DAY
    for dd in range(d_sig - 3, d_sig + 1):
        r = metrics_rows(sym, dd)
        rows.update(r)
    # первая строка монеты: ищем самую раннюю среди файлов до d_sig − 3 (идём назад до отсутствия подряд 5 суток)
    fr = min(rows) if rows else None
    probe = d_sig - 4
    miss = 0
    while miss < 6 and probe > d_sig - 140:
        r = metrics_rows(sym, probe)
        if r:
            miss = 0
            fr = min(min(r), fr) if fr is not None else min(r)
        else:
            miss += 1
        probe -= 1
    first = fr
    if first is not None and first <= Ts - DAY:
        def val(col, back):
            r = rows.get(Ts - back * 300_000)
            return float(r[col]) if r is not None else nan
        oi = val("sum_open_interest", 0)
        for h, key in ((12, "oi_c1"), (48, "oi_c4"), (288, "oi_c24")):
            o2 = val("sum_open_interest", h)
            if oi == oi and o2 == o2 and o2 > 0:
                f[key] = (oi / o2 - 1.0) * 1e4
        oiv = val("sum_open_interest_value", 0)
        if oiv == oiv and oiv > 0 and turn24h > 0:
            f["loi_turn"] = math.log10(oiv / turn24h)
        for col, k1, k2 in (("count_toptrader_long_short_ratio", "ls_tc", "dls_tc"), ("sum_toptrader_long_short_ratio", "ls_ts", "dls_ts"),
                            ("count_long_short_ratio", "ls_a", "dls_a")):
            x, x1 = val(col, 0), val(col, 12)
            if x == x and x > 0:
                f[k1] = math.log(x)
                if x1 == x1 and x1 > 0:
                    f[k2] = math.log(x) - math.log(x1)
    # фандинг
    ft = {}
    mdir = f"{FUND_M}/{sym}"
    if os.path.isdir(mdir):
        for fn in sorted(os.listdir(mdir)):
            if fn.endswith(".zip"):
                with zipfile.ZipFile(f"{mdir}/{fn}") as zf, zf.open(fn[:-4] + ".csv") as fh:
                    for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                        ft[int(r["calc_time"])] = float(r["last_funding_rate"])
    p = f"{FUND_CSV}/{sym}/funding.csv"
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                ft[int(r["funding_time"])] = float(r["funding_rate"])
    times = sorted(ft)
    le = [t for t in times if t <= sig]
    if le:
        f["fund_last"] = ft[le[-1]] * 1e4
        le24 = [t for t in times if t <= sig - DAY]
        if le24:
            f["fund_chg24"] = (ft[le[-1]] - ft[le24[-1]]) * 1e4
        if len(le) >= 2:
            f["fund_int"] = float(round((le[-1] - le[-2]) / HOUR))
    # премия: закрытая минута t−1
    mt = (sig // MIN - 1) * MIN
    dd = mt // DAY
    path = f"{PREMIUM}/{sym}/{sym}-1m-{dstr(dd)}.zip"
    if os.path.exists(path):
        with zipfile.ZipFile(path) as zf, zf.open(f"{sym}-1m-{dstr(dd)}.csv") as fh:
            for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                if int(r["open_time"]) == mt:
                    f["basis"] = float(r["close"]) * 1e4
    return f


# ───────────────────────────── ворота ─────────────────────────────

def pick(ep, n: int, seed: int, small_only: bool, per_coin: int):
    """Монеты по размеру (число эпизодов как грубый прокси размера не годится — берём размер zip BTC-подобных отдельно): случайно по монетам, не больше per_coin на монету."""
    rnd = random.Random(seed)
    idx = list(range(len(ep)))
    rnd.shuffle(idx)
    bycoin: dict = {}
    out = []
    for i in idx:
        c = ep.iloc[i]["sym"]
        if bycoin.get(c, 0) >= per_coin:
            continue
        if small_only and ep.iloc[i]["sym"] in BIG:
            continue
        bycoin[c] = bycoin.get(c, 0) + 1
        out.append(i)
        if len(out) >= n:
            break
    return out


BIG = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT", "SUIUSDT", "1000PEPEUSDT", "LINKUSDT", "AVAXUSDT", "TRXUSDT",
       "LTCUSDT", "BCHUSDT", "DOTUSDT", "NEARUSDT", "UNIUSDT", "AAVEUSDT", "ENAUSDT", "WLDUSDT", "FILUSDT", "TAOUSDT", "HYPEUSDT", "ZECUSDT", "ASTERUSDT"}


def run_compare(kind: str, argv):
    import pandas as pd
    ep_path, feat_path, out_path = argv[0], argv[1], argv[2]
    n = int(argv[3]) if len(argv) > 3 else 220
    base = F.base_ms(argv[4] if len(argv) > 4 else "2026-09-01")
    ep = pd.read_parquet(ep_path, columns=["ep_id", "sym", "side", "sig_ms", "turn24h", "half"])
    ft = pd.read_parquet(feat_path).set_index("ep_id")
    cols = F.FLOW if kind == "flow" else F.DERIV
    sel = pick(ep, n - 12, 594, True, 3) if kind == "flow" else pick(ep, n, 594, False, 3)
    if kind == "flow":  # + 12 эпизодов крупных монет (по 1–2 на монету)
        big = ep[ep.sym.isin(BIG)]
        sel += pick(big.reset_index(drop=True), 12, 5941, False, 2) and [ep.index.get_loc(i) for i in big.sample(12, random_state=594).index]
    bad, ndone = [], 0
    sides = {"long": 0, "short": 0}
    halves = {"A": 0, "B": 0}
    for i in sel:
        r = ep.iloc[i]
        sig = base + int(r["sig_ms"])
        s = 1 if r["side"] == "long" else -1
        ref = flow_brute(r["sym"], sig, s) if kind == "flow" else deriv_brute(r["sym"], sig, float(r["turn24h"]))
        got = ft.loc[r["ep_id"]]
        ndone += 1
        sides[r["side"]] += 1
        halves[r["half"]] = halves.get(r["half"], 0) + 1
        for c in cols:
            if not close(float(got[c]), ref[c]):
                bad.append({"ep_id": str(r["ep_id"]), "sym": r["sym"], "feat": c, "fast": float(got[c]), "brute": ref[c]})
    res = {"kind": kind, "episodes": ndone, "mismatches": len(bad), "sides": sides, "halves": halves, "coins": len({ep.iloc[i]["sym"] for i in sel}),
           "nan_share_checked": sum(1 for i in sel for c in cols if ft.loc[ep.iloc[i]["ep_id"]][c] != ft.loc[ep.iloc[i]["ep_id"]][c]) / max(1, ndone * len(cols)),
           "examples": bad[:20]}
    json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "examples"}, ensure_ascii=False))
    return 1 if bad else 0


def run_nolook(argv):
    """G3: быстрая реализация на данных, где всё, что не должно быть видно (сделки ≥ sig, строки metrics с create_time > sig − 10 мин, фандинг > sig, премия после t−1), заменено мусором."""
    import numpy as np
    import pandas as pd
    ep_path, out_path = argv[0], argv[1]
    n = int(argv[2]) if len(argv) > 2 else 60
    base = F.base_ms(argv[3] if len(argv) > 3 else "2026-09-01")
    ep = pd.read_parquet(ep_path, columns=["ep_id", "sym", "side", "sig_ms", "turn24h"])
    rng = np.random.default_rng(594)
    sel = rng.choice(len(ep), size=n * 3, replace=False)
    bad, ndone = [], 0
    for i in sel:
        if ndone >= n:
            break
        r = ep.iloc[int(i)]
        sym = r["sym"]
        sig = base + int(r["sig_ms"])
        s = 1 if r["side"] == "long" else -1
        d = sig // DAY
        parts = [F.read_trades_day(sym, dd) for dd in (d - 1, d)]
        parts = [p for p in parts if p is not None]
        if not parts:
            continue
        T = np.concatenate([p[0] for p in parts])
        V = np.concatenate([p[1] for p in parts])
        B = np.concatenate([p[2] for p in parts])
        cut = int(np.searchsorted(T, sig, "left"))
        # q99 и has24 — по тем же суткам, что и в полном прогоне (гистограммы идут из часов ДО sig: обрезка не влияет); берём напрямую из полного
        Hh = np.zeros(NBH := F.NB, np.int64)
        h_end = sig // HOUR
        n_hours = 0
        coin_first = None
        for dd in range(d - 8, d + 1):
            x = F.read_trades_day(sym, dd)
            if x is None:
                continue
            if coin_first is None and len(x[0]):
                coin_first = int(x[0][0])
            t, v, _ = x
            m = (t // HOUR >= h_end - 168) & (t // HOUR < h_end)
            if m.any():
                ix = np.clip(np.searchsorted(F.EDGES_ARR, v[m], "right") - 1, 0, F.NB - 1)
                Hh += np.bincount(ix, minlength=F.NB)
                n_hours += len(np.unique(t[m] // HOUR))
        q99 = F.q99_edge(Hh) if n_hours >= 24 else None
        has24 = coin_first is not None and coin_first <= sig - DAY
        full = F.flow_features(T, V, B, sig, s, q99, has24)
        T2, V2, B2 = T[:cut].copy(), V[:cut].copy(), B[:cut].copy()
        T2 = np.concatenate([T2, sig + rng.integers(0, 10**7, 5000)])  # мусор за границей
        V2 = np.concatenate([V2, rng.uniform(1e3, 1e12, 5000)])
        B2 = np.concatenate([B2, rng.integers(0, 2, 5000).astype(bool)])
        o = np.argsort(T2, kind="stable")
        trunc = F.flow_features(T2[o], V2[o], B2[o], sig, s, q99, has24)
        for k in F.FLOW:
            if not close(full[k], trunc[k]):
                bad.append({"ep_id": str(r["ep_id"]), "feat": k, "full": full[k], "trunc": trunc[k]})
        # производные: мусор в строках metrics позже sig − 10 мин, фандинге позже sig, премии позже t−1
        d0 = d - 2
        M, first_row = F.load_metrics(sym, d0, d)
        ft, fr = F.load_funding(sym)
        prem = F.load_premium(sym, d0, d)
        a = F.deriv_features(sig, float(r["turn24h"]), M, first_row, d0, ft, fr, prem)
        Ts = ((sig - 10 * MIN) // 300_000) * 300_000
        gT = (Ts - d0 * DAY) // 300_000
        M2 = {c: v.copy() for c, v in M.items()}
        for c in M2:
            M2[c][gT + 1:] = rng.uniform(1e-3, 1e6, len(M2[c]) - (gT + 1))
        keep = ft <= sig
        ft2 = np.concatenate([ft[keep], sig + 1 + np.arange(5) * HOUR])
        fr2 = np.concatenate([fr[keep], rng.uniform(-0.01, 0.01, 5)])
        prem2 = prem.copy()
        k0 = ((sig // MIN) - 1) - (d0 * DAY // MIN)
        prem2[k0 + 1:] = rng.uniform(-0.01, 0.01, len(prem2) - (k0 + 1))
        b = F.deriv_features(sig, float(r["turn24h"]), M2, first_row, d0, ft2, fr2, prem2)
        for k in F.DERIV:
            if not close(a[k], b[k]):
                bad.append({"ep_id": str(r["ep_id"]), "feat": k, "full": a[k], "trunc": b[k]})
        ndone += 1
    res = {"kind": "nolook", "episodes": ndone, "mismatches": len(bad), "examples": bad[:20]}
    json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "examples"}, ensure_ascii=False))
    return 1 if bad else 0


def run_align(argv):
    """G1: тейкер-отношение строки `metrics` T против aggTrades на [T, T+5 мин) и на [T−5 мин, T) — относительная ошибка по барам (суммы base-количества)."""
    import numpy as np
    import pandas as pd
    out_path = argv[0]
    n = int(argv[1]) if len(argv) > 1 else 10
    rnd = random.Random(594)
    syms = [l for l in open("/data/p42-t566/symbols.txt").read().replace(",", "\n").split() if l]
    rows = []
    tries = 0
    while len(rows) < n and tries < 400:
        tries += 1
        s = rnd.choice(syms)
        d = rnd.randrange(F.base_ms("2026-09-20") // DAY, F.base_ms("2026-11-05") // DAY)
        mrow = metrics_rows(s, d)
        tr = F.read_trades_day(s, d)
        tr_next = F.read_trades_day(s, d + 1)
        if not mrow or tr is None:
            continue
        name = f"{s}-aggTrades-{dstr(d)}"
        path = f"{F.TRADES[0]}/{s}/{name}.zip"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(zipfile.ZipFile(path).open(name + ".csv"), usecols=["quantity", "transact_time", "is_buyer_maker"])
        t = df["transact_time"].to_numpy()
        q = df["quantity"].to_numpy(float)
        bm = df["is_buyer_maker"].to_numpy().astype(bool) if df["is_buyer_maker"].dtype == bool else df["is_buyer_maker"].astype(str).str.lower().eq("true").to_numpy()
        e_fwd, e_back, nb = [], [], 0
        for T0, r in sorted(mrow.items()):
            if T0 < d * DAY + 300_000 or T0 + 300_000 > (d + 1) * DAY:
                continue
            tgt = float(r["sum_taker_long_short_vol_ratio"])
            if not tgt > 0:
                continue
            for a, store in ((T0, e_fwd), (T0 - 300_000, e_back)):
                m = (t >= a) & (t < a + 300_000)
                buy = q[m & ~bm].sum()
                sell = q[m & bm].sum()
                if sell > 0 and buy > 0:
                    store.append(abs(buy / sell / tgt - 1.0))
        if len(e_fwd) > 100:
            rows.append({"sym": s, "day": dstr(d), "bars": len(e_fwd), "err_fwd_mean": float(np.mean(e_fwd)), "err_back_mean": float(np.mean(e_back))})
    res = {"kind": "align", "coin_days": len(rows), "rows": rows,
           "ok": bool(rows) and all(r["err_fwd_mean"] < 0.01 and r["err_back_mean"] > 0.05 for r in rows)}
    json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"coin_days": len(rows), "ok": res["ok"], "fwd": [round(r["err_fwd_mean"], 4) for r in rows], "back": [round(r["err_back_mean"], 3) for r in rows]}))
    return 0 if res["ok"] else 1


def run_fund(argv):
    """G6: месячный архив 2026-09 = funding.csv за сентябрь (все монеты, время и ставка)."""
    out_path = argv[0]
    syms = [l for l in open("/data/p42-t566/symbols.txt").read().replace(",", "\n").split() if l]
    n_cmp = n_bad = n_missing_m = n_missing_c = 0
    ex = []
    lo, hi = F.base_ms("2026-09-01"), F.base_ms("2026-10-01")
    for s in syms:
        mp = f"{FUND_M}/{s}/{s}-fundingRate-2026-09.zip"
        cp = f"{FUND_CSV}/{s}/funding.csv"
        if not os.path.exists(mp):
            n_missing_m += 1
            continue
        if not os.path.exists(cp):
            n_missing_c += 1
            continue
        m = {}
        with zipfile.ZipFile(mp) as zf, zf.open(f"{s}-fundingRate-2026-09.csv") as fh:
            for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                m[int(r["calc_time"])] = float(r["last_funding_rate"])
        c = {}
        with open(cp, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                t = int(r["funding_time"])
                if lo <= t < hi:
                    c[t] = float(r["funding_rate"])
        n_cmp += 1
        if m != c:
            n_bad += 1
            if len(ex) < 10:
                ex.append({"sym": s, "only_m": len(set(m) - set(c)), "only_c": len(set(c) - set(m)), "diff_val": sum(1 for t in set(m) & set(c) if m[t] != c[t])})
    res = {"kind": "fund", "compared": n_cmp, "mismatch_coins": n_bad, "no_monthly": n_missing_m, "no_csv": n_missing_c, "examples": ex}
    json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "examples"}))
    return 1 if n_bad else 0


if __name__ == "__main__":
    mode = sys.argv[1]
    a = sys.argv[2:]
    if mode in ("flow", "deriv"):
        sys.exit(run_compare(mode, a))
    if mode == "nolook":
        sys.exit(run_nolook(a))
    if mode == "align":
        sys.exit(run_align(a))
    if mode == "fund":
        sys.exit(run_fund(a))
    sys.exit("режим: flow | deriv | nolook | align | fund")
