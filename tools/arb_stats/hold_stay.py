"""«Разошлись и остались» против «расходятся и сходятся»: метка и признаки в момент начала расхождения (протокол docs/findings/arb-holding-stay-protocol-2026-11-17.md, записан до счёта).

Единица — эпизод вида «от центра», θ = 50 bps, строгий вариант, ≥ 5 с, не снят слоями gap / stale (сырьё) / tradfi / delist / funding. Метка (смотрит вперёд, только для оценки): c0 = центр на начале;
τ = первый момент после начала, где сторона · (M − c0) ≤ 0; «сходится» — τ − начало ≤ 30 мин, иначе «остался». Признаки — по данным не позже начала эпизода.
Запуск: python3 hold_stay.py build <flagged.pkl> <книги (с parsed/)> <marks.pkl[,…]> <trades.pkl> <выход.pkl> [PAR]   — строит таблицу единиц;
        python3 hold_stay.py eval <stay.pkl> <выход.md>                                                     — оценка по протоколу (разбиение по суткам, перестановки).
"""
import pickle
import sys
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import hold_scan as hs  # noqa: E402
import maker_run  # noqa: E402
from hold_common import CENTRE_MIN, DAYS, TZ_MS, unit_of  # noqa: E402

TH, VIEW = 50.0, "c"
HORIZON_MS = 1_800_000                 # «сходится» — возврат за 30 мин
HIST_MS = 6 * 3_600_000
CARRY = ["f_fake", "f_bingx", "f_stale", "f_gap", "f_tradfi", "f_delist", "f_funding", "f_mark", "f_trend", "f_contract", "mark_gap", "mark_dev_A", "mark_dev_B", "size0", "spA", "spB", "edge0", "ageA", "ageB", "day"]
FEATURES = ["hist_conv6", "hist_n6", "since_end", "lvl_shift6", "dev_lvl6", "mark_conf", "mark_off", "fund_diff", "depth", "spread", "flow_min", "mover", "vol10", "bingx"]
G = {}


def last_rate(mk, ex, sym, t):
    """Ставка последнего начисленного фандинга символа не позже t (доля, не bps); NaN — выплаты не записаны."""
    d = mk.get(ex, {}).get(sym)
    if d is None or not d["pay"]:
        return np.nan
    tf = d["_tf"] if "_tf" in d else None
    if tf is None:
        pay = sorted(d["pay"])
        d["_tf"], d["_rf"] = np.array([x[0] for x in pay], np.int64), np.array([x[1] for x in pay], float)
        tf = d["_tf"]
    i = np.searchsorted(tf, t, "right") - 1
    return float(d["_rf"][i]) if i >= 0 else np.nan


def trades_before(tr, ex, sym, ts, win=6):
    """Число сделок в окнах по 10 с, лежащих строго до окна начала (win окон = 60 с)."""
    d = tr.get((ex, sym))
    if d is None:
        return 0.0
    b, n, _ = d
    k = ts // 10_000
    lo, hi = np.searchsorted(b, k - win, "left"), np.searchsorted(b, k, "left")
    return float(n[lo:hi].sum())


def work_pair(key):
    coin, a, b = key
    g = G
    books = {}
    for ex in (a, b):
        sym, mult, meta = g["coins"][coin][ex]
        books[ex] = maker_run.load_book(g["bwork"], ex, sym, mult, unit_of(ex, meta))
    A, B = books[a], books[b]
    T, S, Bu, ia, ib = core.pair_series(A, B, g["t1"])
    valid = ((T - A["e"][ia]) <= core.CAP_MS) & ((T - B["e"][ib]) <= core.CAP_MS)
    mins, _, sb, bb = core.minute_bars(T[valid], S[valid], Bu[valid])
    m0, cen = core.rolling_centre(mins, sb, bb, CENTRE_MIN, CENTRE_MIN // 2)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    M = (S + Bu) / 2
    mid_min = np.full(len(cen), np.nan)
    mid_min[mins - m0] = (sb[3] + bb[3]) / 2
    midA = (A["bid"] + A["ask"]) / 2
    midB = (B["bid"] + B["ask"]) / 2
    sub = g["df"].loc[g["idx"][key]].sort_values("t")
    symA, symB = sub["symA"].iloc[0], sub["symB"].iloc[0]
    t, dur, side, st = sub["t"].to_numpy().astype(np.int64), sub["dur"].to_numpy(), sub["side"].to_numpy().astype(int), sub["i0"].to_numpy().astype(int)
    n = len(sub)
    # метка: возврат M к замороженному центру c0 за 30 мин
    c0 = cT[st]
    tau = np.full(n, np.nan)
    for j in range(n):
        if np.isnan(c0[j]):
            continue
        hi = int(np.searchsorted(T, t[j] + HORIZON_MS, "right"))
        seg = side[j] * (M[st[j]:hi] - c0[j]) <= 0
        if seg.any():
            tau[j] = T[st[j] + int(np.argmax(seg))] - t[j]
    horizon_ok = t + HORIZON_MS <= g["t1"]
    label = np.where(np.isfinite(tau), 1, np.where(horizon_ok, 0, -1))     # 1 — сходится, 0 — остался, −1 — не известно (хвост окна)
    known_at = np.where(np.isfinite(tau), t + np.nan_to_num(tau), t + HORIZON_MS)    # когда метка прошлого эпизода становится известной
    ends = t + dur
    feats = {f: np.full(n, np.nan) for f in FEATURES}
    for j in range(n):
        ts = int(t[j])
        # история пары: прошлые эпизоды, та же сторона, начало в предыдущие 6 ч (t отсортирован)
        lo = int(np.searchsorted(t, ts - HIST_MS, "left"))
        prev = lo + np.flatnonzero(side[lo:j] == side[j])
        feats["hist_n6"][j] = len(prev)
        kn = prev[(known_at[prev] <= ts) & (label[prev] >= 0)]
        feats["hist_conv6"][j] = float(np.mean(label[kn])) if len(kn) else np.nan
        pe = ends[:j]
        pe = pe[pe <= ts]
        feats["since_end"][j] = min((ts - pe.max()) / 1000, 86_400.0) if len(pe) else 86_400.0
        k = ts // 60000 - m0
        w = mid_min[max(k - 360, 0):max(k, 0)]
        lvl = float(np.nanmedian(w)) if np.isfinite(w).sum() >= 60 else np.nan
        feats["lvl_shift6"][j] = side[j] * (c0[j] - lvl) if np.isfinite(lvl) else np.nan
        feats["dev_lvl6"][j] = side[j] * (M[st[j]] - lvl) if np.isfinite(lvl) else np.nan
        w10 = mid_min[max(k - 10, 0):max(k, 0)]
        feats["vol10"][j] = float(np.nanmax(w10) - np.nanmin(w10)) if np.isfinite(w10).sum() >= 3 else np.nan
        # mover: уход середины каждой ноги за последние 10 с
        ts10 = ts - 10_000
        iA0, iB0 = np.searchsorted(A["e"], ts10, "right") - 1, np.searchsorted(B["e"], ts10, "right") - 1
        if iA0 >= 0 and iB0 >= 0:
            dA = abs(midA[ia[st[j]]] / midA[iA0] - 1) * 1e4
            dB = abs(midB[ib[st[j]]] / midB[iB0] - 1) * 1e4
            feats["mover"][j] = max(dA, dB) / (dA + dB) if dA + dB > 0 else 0.5
        ra, rb = last_rate(g["marks"], a, symA, ts), last_rate(g["marks"], b, symB, ts)
        feats["fund_diff"][j] = side[j] * (ra - rb) * 1e4 if np.isfinite(ra) and np.isfinite(rb) else np.nan
        na, nb_ = trades_before(g["trades"], a, symA, ts), trades_before(g["trades"], b, symB, ts)
        feats["flow_min"][j] = np.log1p(min(na, nb_))
    out = pd.DataFrame({"coin": coin, "exA": a, "exB": b, "t": t, "dur": dur, "side": side, "label": label, "tau": tau, "c0": c0, **feats})
    for c in CARRY:
        out[c] = sub[c].to_numpy()
    return out


def build(flagp, bwork, marksp, tradesp, out, par=4):
    from hold_common import build_coins
    from hold_filter import load_marks
    tab = pickle.load(open(flagp, "rb"))
    df = pd.concat([tab["df"], tab["df_long"]], ignore_index=True)
    df = df[(df["view"] == VIEW) & (df["th"] == TH) & (df["dur"] >= 1000)].reset_index(drop=True)          # индекс = позиция (groupby.indices — позиционные)
    df["f_fake"] = df["f_gap"] | df["f_stale"] | df["f_tradfi"] | df["f_delist"] | df["f_funding"]
    coins, _ = build_coins(str(Path(bwork, "parsed")), DAYS)
    G.update({"bwork": str(Path(bwork, "parsed")), "t1": tab["t1"], "coins": coins, "df": df, "idx": df.groupby(["coin", "exA", "exB"]).indices,
              "marks": load_marks(marksp), "trades": pickle.load(open(tradesp, "rb"))})
    keys = list(G["idx"])
    t = time.time()
    res = []
    with Pool(par, maxtasksperchild=8) as p:
        for k, r in enumerate(p.imap_unordered(work_pair, keys, chunksize=1)):
            res.append(r)
            if k % 50 == 0:
                print(k, len(keys), round(time.time() - t), flush=True)
    st = pd.concat(res, ignore_index=True)
    with np.errstate(invalid="ignore"):
        st["mark_conf"] = st["side"] * (st["mark_gap"] - st["c0"])
        st["mark_off"] = st["mark_dev_A"].abs() - st["mark_dev_B"].abs()
        st["depth"] = np.log10(st["size0"].clip(lower=1.0))
        st["spread"] = st["spA"] + st["spB"]
    st["bingx"] = st["f_bingx"].astype(float)
    pickle.dump({"st": st, "days": tab["days"], "t0": tab["t0"], "t1": tab["t1"]}, open(out, "wb"), protocol=4)
    print({"units": len(st), "stay": int((st["label"] == 0).sum()), "conv": int((st["label"] == 1).sum())}, round(time.time() - t))


# ---------------- оценка ----------------

def bal_acc(y, pred):
    """y: 1 — «остался» (положительный класс), pred — булев «признак говорит: останется»."""
    p, n = y == 1, y == 0
    tpr = float((pred & p).sum() / max(p.sum(), 1))
    tnr = float((~pred & n).sum() / max(n.sum(), 1))
    return (tpr + tnr) / 2, tpr, tnr


def auc(y, x):
    """AUC для положительного класса y == 1 по x (больше x — вероятнее «остался»), пропуски не берутся."""
    m = np.isfinite(x)
    y, x = y[m], x[m]
    p, n = x[y == 1], x[y == 0]
    if not len(p) or not len(n):
        return np.nan
    r = pd.Series(np.r_[p, n]).rank().to_numpy()
    return float((r[:len(p)].sum() - len(p) * (len(p) + 1) / 2) / (len(p) * len(n)))


def fit_rule(y, x):
    """Порог и знак по подбору: максимум сбалансированной точности. -> (порог, знак, BA); знак +1: «остался», если x ≥ порога."""
    m = np.isfinite(x)
    y, x = y[m], x[m]
    if (y == 1).sum() < 3 or (y == 0).sum() < 3:
        return None
    cands = np.unique(np.quantile(x, np.linspace(0.02, 0.98, 49)))
    best = (None, 1, 0.0)
    for thr in cands:
        for sg in (1, -1):
            pred = (x >= thr) if sg == 1 else (x <= thr)
            ba = bal_acc(y, pred)[0]
            if ba > best[2]:
                best = (float(thr), sg, ba)
    return best


def apply_rule(rule, x):
    thr, sg, _ = rule
    with np.errstate(invalid="ignore"):
        return (x >= thr) if sg == 1 else (x <= thr)


def perm_p(y, pred, ba_obs, n_perm=5000, seed=7):
    rng = np.random.default_rng(seed)
    cnt = 0
    for _ in range(n_perm):
        if bal_acc(rng.permutation(y), pred)[0] >= ba_obs:
            cnt += 1
    return (cnt + 1) / (n_perm + 1)


def boot_pairs(y, pred, pair_id, n_boot=300, seed=11):
    """95 % интервал BA блочным бутстрепом по ПАРАМ (эпизоды одной пары зависимы): пары выбираются с возвращением."""
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(pair_id, return_inverse=True)
    groups = [np.flatnonzero(inv == k) for k in range(len(ids))]
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(ids), len(ids))
        ix = np.concatenate([groups[k] for k in pick])
        vals.append(bal_acc(y[ix], pred[ix])[0])
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def logit_fit(X, y, l2=1.0, it=300):
    """Логистическая регрессия с L2 градиентным спуском (без сторонних пакетов)."""
    w = np.zeros(X.shape[1] + 1)
    Xb = np.c_[np.ones(len(X)), X]
    cw = np.where(y == 1, 0.5 / max((y == 1).sum(), 1), 0.5 / max((y == 0).sum(), 1)) * len(y)       # веса классов
    for _ in range(it):
        z = np.clip(Xb @ w, -30, 30)
        pr = 1 / (1 + np.exp(-z))
        grad = Xb.T @ (cw * (pr - y)) / len(y) + l2 * np.r_[0, w[1:]] / len(y)
        w -= 0.5 * grad
    return w


def logit_pred(w, X):
    return 1 / (1 + np.exp(-np.clip(np.c_[np.ones(len(X)), X] @ w, -30, 30)))


def design(tr, te):
    """Совместная модель: пропуски — медиана подбора + индикатор пропуска, стандартизация по подбору."""
    cols, A, Bm = [], [], []
    for f in FEATURES:
        a, b = tr[f].to_numpy(float), te[f].to_numpy(float)
        med = np.nanmedian(a) if np.isfinite(a).any() else 0.0
        sd = np.nanstd(a) if np.isfinite(a).sum() > 1 and np.nanstd(a) > 0 else 1.0
        A.append((np.where(np.isfinite(a), a, med) - med) / sd)
        Bm.append((np.where(np.isfinite(b), b, med) - med) / sd)
        if (~np.isfinite(a)).any() or (~np.isfinite(b)).any():
            A.append((~np.isfinite(a)).astype(float))
            Bm.append((~np.isfinite(b)).astype(float))
    return np.c_[tuple(A)], np.c_[tuple(Bm)]


def evaluate(path_in, path_out):
    d = pickle.load(open(path_in, "rb"))
    st, days = d["st"], d["days"]
    st = st[(st["label"] >= 0) & ~st["f_fake"] & (st["dur"] >= 5000)].copy()
    st["y"] = (st["label"] == 0).astype(int)               # 1 — «остался»
    st["dayno"] = np.searchsorted(np.array(days[1:-1]), st["t"].to_numpy(), "right")
    nd = len(days) - 1
    half = nd // 2
    parts = {"подбор первые сутки → проверка последние": (st["dayno"] < half, st["dayno"] >= half), "подбор последние → проверка первые": (st["dayno"] >= half, st["dayno"] < half)}
    L = [f"Единиц (эпизодов вида «от центра», θ = 50 bps, ≥ 5 с, без явных подделок): {len(st)}; «остался» (нет возврата за 30 мин): {int(st['y'].sum())}, «сходится»: {int((st['y'] == 0).sum())}. Суток GMT+4 в окне: {nd}; первая половина — сутки 0…{half - 1}, вторая — {half}…{nd - 1}.", ""]
    L += ["| сутки | эпизодов | остался | сходится |", "|---|---|---|---|"]
    for k in range(nd):
        s = st[st["dayno"] == k]
        L.append(f"| {pd.to_datetime(days[k] + TZ_MS, unit='ms').strftime('%d.%m')} | {len(s)} | {int(s['y'].sum())} | {int((s['y'] == 0).sum())} |")
    L.append("")
    res = {}
    for nm, (mtr, mte) in parts.items():
        tr, te = st[mtr], st[mte]
        L += [f"### {nm}: подбор {len(tr)} ({int(tr['y'].sum())} остался), проверка {len(te)} ({int(te['y'].sum())} остался)", ""]
        rows = []
        for f in FEATURES:
            rule = fit_rule(tr["y"].to_numpy(), tr[f].to_numpy(float))
            if rule is None:
                rows.append({"признак": f, "порог": None, "знак": None, "BA подбор": None, "BA проверка": None, "найдено «остался», %": None, "пропущено «сходится», %": None, "AUC проверка": None, "p": None, "BA 95 % по парам": None, "суток проверки с BA > 0,5": None})
                continue
            x = te[f].to_numpy(float)
            pred = apply_rule(rule, x)
            m = np.isfinite(x)
            ba, tpr, tnr = bal_acc(te["y"].to_numpy()[m], pred[m])
            a_ = auc(te["y"].to_numpy(), x if rule[1] == 1 else -x)
            p = perm_p(te["y"].to_numpy()[m], pred[m], ba)
            pid = (te["coin"] + "|" + te["exA"] + "|" + te["exB"]).to_numpy()[m]
            ci = boot_pairs(te["y"].to_numpy()[m], pred[m], pid)
            good = 0
            yt, dn = te["y"].to_numpy(), te["dayno"].to_numpy()
            for k in sorted(set(dn)):
                mk = (dn == k) & m
                if (yt[mk] == 1).any() and (yt[mk] == 0).any() and bal_acc(yt[mk], pred[mk])[0] > 0.5:
                    good += 1
            rows.append({"признак": f, "порог": round(rule[0], 3), "знак": "≥" if rule[1] == 1 else "≤", "BA подбор": round(rule[2], 3), "BA проверка": round(ba, 3), "найдено «остался», %": round(100 * tpr, 1),
                         "пропущено «сходится», %": round(100 * tnr, 1), "AUC проверка": round(a_, 3) if a_ == a_ else None, "p": round(p, 4), "BA 95 % по парам": f"{ci[0]:.2f}…{ci[1]:.2f}", "суток проверки с BA > 0,5": good})
            res[(nm, f)] = (ba, p, good, ci[0])
        Xtr, Xte = design(tr, te)
        w = logit_fit(Xtr, tr["y"].to_numpy(float))
        pr_tr, pr_te = logit_pred(w, Xtr), logit_pred(w, Xte)
        thr = fit_rule(tr["y"].to_numpy(), pr_tr)
        if thr is not None:
            pred = apply_rule(thr, pr_te)
            ba, tpr, tnr = bal_acc(te["y"].to_numpy(), pred)
            p = perm_p(te["y"].to_numpy(), pred, ba)
            ci = boot_pairs(te["y"].to_numpy(), pred, (te["coin"] + "|" + te["exA"] + "|" + te["exB"]).to_numpy())
            rows.append({"признак": "совместная модель (логистическая)", "порог": round(thr[0], 3), "знак": "≥", "BA подбор": round(thr[2], 3), "BA проверка": round(ba, 3), "найдено «остался», %": round(100 * tpr, 1),
                         "пропущено «сходится», %": round(100 * tnr, 1), "AUC проверка": round(auc(te["y"].to_numpy(), pr_te), 3), "p": round(p, 4), "BA 95 % по парам": f"{ci[0]:.2f}…{ci[1]:.2f}", "суток проверки с BA > 0,5": None})
            res[(nm, "model")] = (ba, p, None, ci[0])
        L += [_md(pd.DataFrame(rows)), ""]
    # правило вердикта протокола
    verdict = []
    for f in FEATURES + ["model"]:
        r = [res.get((nm, f)) for nm in parts]
        ok = all(x is not None and x[0] >= 0.65 and x[1] < 0.0017 and x[3] > 0.5 and (x[2] is None or x[2] >= 2) for x in r)
        verdict.append((f, ok))
    L += ["**Вердикт по правилу протокола (BA ≥ 0,65, p < 0,0017, нижняя граница BA по парам > 0,5 — в обоих направлениях; BA > 0,5 минимум в двух из трёх суток проверки):** " + (", ".join(f for f, ok in verdict if ok) or "ни один признак не годится"), ""]
    open(path_out, "w", encoding="utf-8").write("\n".join(L))


def _md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(v) for v in r.tolist()) + " |")
    return "\n".join(out)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], int(sys.argv[7]) if len(sys.argv) > 7 else 4)
    else:
        evaluate(sys.argv[2], sys.argv[3])
