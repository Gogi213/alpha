"""«Разошлись и остались» против «расходятся и сходятся»: метка и признаки в момент начала расхождения (протокол docs/findings/arb-holding-stay-protocol-2026-11-17.md, записан до счёта; v2 после проверки протокола).

Единица — эпизод вида «от центра», θ = 50 bps, строгий вариант, ≥ 5 с; не снят слоями gap / stale (сырьё) / tradfi / delist / funding, посчитанными на отрезке [t − запас, t + 5 с] (не по всему эпизоду, п. Б1).
Метка (смотрит вперёд, только для оценки): c0 = центр на начале; τ = первый ВАЛИДНЫЙ тик (вне молчания сырья любой ноги > 5 с) после начала, где сторона · (M − c0) ≤ 0; «сходится» — τ − начало ≤ 30 мин, иначе «остался».
Метка −1 (не известно, в оценку не идёт): c0 = NaN; до конца окна < 30 мин; окно [t, t + 30 мин] пересекает dead / обрыв / аудит любой ноги; символ ноги без сырья (raw); доля времени вне молчания сырья (> 5 с, rawgaps, любая нога) в окне < 95 % (п. Б2, П1: «нет данных» = молчание ВСЕХ записей символа, а не неизменная лучшая цена).
Признаки — по данным не позже начала эпизода; mark — только ведра строго раньше ведра начала (п. Б3).
Запуск: python3 hold_stay.py build <flagged.pkl> <книги (с parsed/)> <marks.pkl[,…]> <trades.pkl> <dead.json> <аудит collect-gap> <rawgaps.pkl> <выход.pkl> [PAR]   — строит таблицу единиц;
        python3 hold_stay.py eval <stay.pkl> <выход.md>                                                     — оценка по протоколу (разбиение по суткам, эмбарго, базы, бутстреп по монетам).
"""
import json
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import maker_run  # noqa: E402
from hold_common import CENTRE_MIN, EX5, STALE_MS, TZ_MS, unit_of  # noqa: E402
from hold_filter import FUND_MARGIN_MS, GAP_MARGIN_MS, OUTAGES, load_audit, load_raw, max_free_gap, merge, overlaps  # noqa: E402

TH, VIEW = 50.0, "c"
HORIZON_MS = 1_800_000                 # «сходится» — возврат за 30 мин
HIST_MS = 6 * 3_600_000
DECIDE_MS = 5_000                      # единица существует при длительности ≥ 5 с: правило применимо на t + 5 с (признаки — на t, консервативно)
COVER_MIN = 0.95                       # доля времени вне молчания сырья (любая нога) в окне метки
COVER_SCOPE = 0.10                     # доля единиц, снятых по покрытию, в трети ликвидности, выше которой вердикт — «только пары с покрытием ≥ 95 %»
MIN_DAY_MS = 12 * 3_600_000            # сутки с покрытием окна < 12 ч не берутся
EMBARGO_FIT_MS = HIST_MS + HORIZON_MS  # единицы подбора ближе 6 ч 30 мин к проверке выбрасываются
EMBARGO_TEST_MS = HORIZON_MS           # единицы проверки ближе 30 мин к подбору выбрасываются
N_BOOT = 20_000                        # бутстреп по монетам
MIN_STAY = 30                          # «остался» в проверке каждого направления
BA_MIN = 0.65
PRIOR_PASSES = 3                       # прошлые взгляды на те же сутки (v1–v3) считаются испытаниями
CARRY = ["f_fake_old", "f_bingx", "f_tradfi", "f_delist", "size0", "spA", "spB", "edge0", "day"]
FEATURES = ["hist_conv6", "hist_n6", "since_end", "lvl_shift6", "dev_lvl6", "mark_conf", "mark_off", "fund_diff", "depth", "spread", "flow_min", "mover", "vol10", "bingx"]
N_TRIALS = 2 * (len(FEATURES) + 1) + PRIOR_PASSES          # признаки и совместная модель × 2 направления + прошлые проходы
ALPHA_ADJ = 0.05 / N_TRIALS
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


def mark_before(mk, ex, sym, mult, ts):
    """Mark символа по ведру 10 с СТРОГО раньше ведра начала (в ведре хранится последняя запись; ведро начала могло бы заглянуть до 10 с вперёд), делённый на множитель; NaN — нет или старше 15 мин."""
    d = mk.get(ex, {}).get(sym)
    ts = np.asarray(ts, np.int64)
    if d is None or not d["b"].size:
        return np.full(len(ts), np.nan)
    k = ts // 10_000
    i = np.searchsorted(d["b"], k, "left") - 1
    ok = (i >= 0) & (k - d["b"][np.maximum(i, 0)] <= 90)
    return np.where(ok, d["p"][np.maximum(i, 0)] / mult, np.nan)


TRADES_COVER_MS = 600_000              # запись сделок символа должна доходить до момента (± 10 мин), иначе «нет данных», не «ноль сделок»


def trades_before(tr, ex, sym, ts, win=6):
    """Число сделок в окнах по 10 с, лежащих строго до окна начала (win окон = 60 с); NaN — записи сделок символа нет или она оборвалась / ещё не началась (первая / последняя запись дальше 10 мин от ts):
    у части символов Gate после 16.11 02:10 UTC записей сделок нет вовсе — это не «ноль сделок»."""
    d = tr.get((ex, sym))
    if d is None or not len(d[0]):
        return np.nan
    b, n, _ = d
    if ts < int(b[0]) * 10_000 - TRADES_COVER_MS or ts > int(b[-1]) * 10_000 + TRADES_COVER_MS:
        return np.nan
    k = ts // 10_000
    lo, hi = np.searchsorted(b, k - win, "left"), np.searchsorted(b, k, "left")
    return float(n[lo:hi].sum())


def union_intervals(parts):
    """Объединение интервалов молчания (p[], n[]) нескольких символов -> отсортированные непересекающиеся (s[], e[])."""
    s = np.concatenate([np.asarray(p, np.int64) for p, _ in parts]) if parts else np.zeros(0, np.int64)
    e = np.concatenate([np.asarray(n, np.int64) for _, n in parts]) if parts else np.zeros(0, np.int64)
    if not len(s):
        return s, e
    o = np.argsort(s, kind="stable")
    s, e = s[o], e[o]
    new = np.r_[True, s[1:] > np.maximum.accumulate(e)[:-1]]
    return s[new], np.maximum.reduceat(e, np.flatnonzero(new))


def silence_time(s, e, x):
    """Суммарное время внутри интервалов молчания (s, e) до момента x (массив)."""
    x = np.asarray(x, np.int64)
    if not len(s):
        return np.zeros(len(x))
    cl = np.r_[0, np.cumsum(e - s)]
    k = np.searchsorted(s, x, "right")
    return cl[k] - np.where(k > 0, np.maximum(e[np.maximum(k - 1, 0)] - x, 0), 0)


def ticks_outside(s, e, T):
    """Тик ряда T валиден, если не лежит строго внутри интервала молчания (s, e) какой-либо ноги."""
    if not len(s):
        return np.ones(len(T), bool)
    i = np.searchsorted(s, T, "left") - 1
    return ~((i >= 0) & (T < e[np.maximum(i, 0)]))


def label_units(T, M, valid, cover, t, st, side, c0, t1, hole, raw_unknown):
    """Метка единиц пары (массивы по единицам; ряды T / M / valid — по тикам). -> tau (мс от начала или NaN), why (причина −1 или ""), label (1 сходится / 0 остался / −1 не известно).
    τ ищется только по валидным тикам (вне молчания сырья любой ноги > 5 с); окно метки — одно и то же [t, t + 30 мин] для всех единиц; cover — доля окна вне молчания."""
    n = len(t)
    tau = np.full(n, np.nan)
    for j in range(n):
        if np.isnan(c0[j]):
            continue
        hi = int(np.searchsorted(T, t[j] + HORIZON_MS, "right"))
        seg = (side[j] * (M[st[j]:hi] - c0[j]) <= 0) & valid[st[j]:hi]
        if seg.any():
            tau[j] = T[st[j] + int(np.argmax(seg))] - t[j]
    why = np.full(n, "", object)
    why[cover < COVER_MIN] = "cover"
    why[raw_unknown] = "raw"
    why[hole] = "hole"
    why[t + HORIZON_MS > t1] = "tail"
    why[np.isnan(c0)] = "c0"
    label = np.where(why != "", -1, np.where(np.isfinite(tau), 1, 0))
    return tau, why, label


def leg_audit(g, ex, sym):
    iv = g["audit"].get(ex, {}).get(sym)
    return merge(iv) if iv else None


def work_pair(key):
    coin, a, b = key
    g = G
    books, mults = {}, {}
    for ex in (a, b):
        sym, mult, meta = g["coins"][coin][ex]
        mults[ex] = mult
        books[ex] = maker_run.load_book(g["bwork"], ex, sym, mult, unit_of(ex, meta))
    A, B = books[a], books[b]
    T, S, Bu, ia, ib = core.pair_series(A, B, g["t1"])
    cap_ok = ((T - A["e"][ia]) <= core.CAP_MS) & ((T - B["e"][ib]) <= core.CAP_MS)          # как в hold_scan: по таким тикам строится центр (c0 совпадает с таблицей)
    mins, _, sb, bb = core.minute_bars(T[cap_ok], S[cap_ok], Bu[cap_ok])
    m0, cen = core.rolling_centre(mins, sb, bb, CENTRE_MIN, CENTRE_MIN // 2)
    cT = cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]
    M = (S + Bu) / 2
    mid_min = np.full(len(cen), np.nan)
    mid_min[mins - m0] = (sb[3] + bb[3]) / 2
    midA = (A["bid"] + A["ask"]) / 2
    midB = (B["bid"] + B["ask"]) / 2
    sub = g["df"].loc[g["idx"][key]].sort_values("t")
    symA, symB = sub["symA"].iloc[0], sub["symB"].iloc[0]
    legs = ((a, symA), (b, symB))
    t, dur, side, st = sub["t"].to_numpy().astype(np.int64), sub["dur"].to_numpy(), sub["side"].to_numpy().astype(int), sub["i0"].to_numpy().astype(int)
    n = len(sub)
    c0 = cT[st]
    # метка: окно [t, t + 30 мин] — дыры данных (dead / обрыв / аудит любой ноги) и покрытие валидным временем → −1
    hole = np.zeros(n, bool)
    for ex, sym in legs:
        hole |= overlaps(*g["dead_iv"][ex], t, t + HORIZON_MS)
        au = leg_audit(g, ex, sym)
        if au is not None:
            hole |= overlaps(*au, t, t + HORIZON_MS)
    # валидность окна метки — по молчанию СЫРЬЯ (все записи символа, rawgaps), не по возрасту лучшей цены: цена может честно стоять, пока расхождение держится на тихой монете
    parts = []
    for ex, sym in legs:
        if (ex, sym) in g["raw"]["sym"]:
            parts.append(g["raw"]["gaps"][(ex, sym)])
            last = int(g["raw"]["sym"][(ex, sym)][2])
            if g["t1"] - last > STALE_MS:
                parts.append((np.array([last], np.int64), np.array([g["t1"]], np.int64)))       # символ перестал писаться до конца окна
    s_iv, e_iv = union_intervals(parts)
    valid = ticks_outside(s_iv, e_iv, T)
    cover = 1.0 - (silence_time(s_iv, e_iv, t + HORIZON_MS) - silence_time(s_iv, e_iv, t)) / HORIZON_MS
    raw_unknown = np.full(n, any((ex, sym) not in g["raw"]["sym"] for ex, sym in legs))
    tau, why, label = label_units(T, M, valid, cover, t, st, side, c0, g["t1"], hole, raw_unknown)
    known_at = np.where(np.isfinite(tau), t + np.nan_to_num(tau), t + HORIZON_MS)    # когда метка прошлого эпизода становится известной
    # слои «подделки» на отрезке, одинаковом для всех единиц и известном к моменту решения: [t − запас, t + 5 с] (п. Б1)
    dec = t + DECIDE_MS
    f_gap, f_audit, f_stale, f_fund = (np.zeros(n, bool) for _ in range(4))
    for ex, sym in legs:
        f_gap |= overlaps(*g["dead_iv"][ex], t - GAP_MARGIN_MS, dec)
        au = leg_audit(g, ex, sym)
        if au is not None:
            f_audit |= overlaps(*au, t, dec)
        if (ex, sym) in g["raw"]["sym"]:
            gp, gn = g["raw"]["gaps"][(ex, sym)]
            f_stale |= max_free_gap(gp, gn, t, dec) > STALE_MS
        d = g["marks"].get(ex, {}).get(sym)
        if d is not None and d["pay"]:
            tf = np.sort(np.array([x[0] for x in d["pay"]], np.int64))
            j = np.searchsorted(tf, t - FUND_MARGIN_MS, "left")
            f_fund |= (j < len(tf)) & (tf[np.minimum(j, len(tf) - 1)] <= dec)
    f_fake = f_gap | f_audit | f_stale | f_fund | sub["f_tradfi"].to_numpy(bool) | sub["f_delist"].to_numpy(bool)
    ends = t + dur
    feats = {f: np.full(n, np.nan) for f in FEATURES}
    pair_age = t - int(T[0])
    for j in range(n):
        ts = int(t[j])
        if pair_age[j] >= HIST_MS:                  # история пары — только если данные пары идут ≥ 6 ч (иначе признаки истории пропущены)
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
        feats["flow_min"][j] = np.log1p(min(na, nb_)) if np.isfinite(na) and np.isfinite(nb_) else np.nan
    # mark: только ведра строго раньше ведра начала (п. Б3)
    mkA, mkB = mark_before(g["marks"], a, symA, mults[a], t), mark_before(g["marks"], b, symB, mults[b], t)
    with np.errstate(invalid="ignore", divide="ignore"):
        feats["mark_conf"] = side * ((mkA / mkB - 1) * 1e4 - c0)
        feats["mark_off"] = np.abs((sub["price"].to_numpy() / mkA - 1) * 1e4) - np.abs((sub["priceB"].to_numpy() / mkB - 1) * 1e4)
    out = pd.DataFrame({"coin": coin, "exA": a, "exB": b, "t": t, "dur": dur, "side": side, "label": label, "why": why, "tau": tau, "cover": cover, "c0": c0,
                        "f_gap": f_gap | f_audit, "f_stale": f_stale, "f_funding": f_fund, "f_fake": f_fake, **feats})
    for c in CARRY:
        out[c] = sub[c].to_numpy()
    return out


def build(flagp, bwork, marksp, tradesp, deadp, auditp, rawp, out, par=4):
    from hold_common import DAYS, build_coins
    from hold_filter import load_marks
    tab = pickle.load(open(flagp, "rb"))
    df = pd.concat([tab["df"], tab["df_long"]], ignore_index=True)
    df = df[(df["view"] == VIEW) & (df["th"] == TH) & (df["dur"] >= 1000)].reset_index(drop=True)          # индекс = позиция (groupby.indices — позиционные)
    df["f_fake_old"] = df["f_gap"] | df["f_stale"] | df["f_tradfi"] | df["f_delist"] | df["f_funding"]    # слои по всему эпизоду (v4): для сравнения
    coins, _ = build_coins(str(Path(bwork, "parsed")), DAYS)
    dead = json.load(open(deadp))
    dead_iv = {}
    for ex in EX5:
        iv = [tuple(x) for x in dead.get(ex, [])] + [(int(x.timestamp() * 1000), int(y.timestamp() * 1000)) for x, y in OUTAGES.get(ex, []) + OUTAGES["*"]]
        dead_iv[ex] = merge(iv)
    G.update({"bwork": str(Path(bwork, "parsed")), "t1": tab["t1"], "coins": coins, "df": df, "idx": df.groupby(["coin", "exA", "exB"]).indices,
              "marks": load_marks(marksp), "trades": pickle.load(open(tradesp, "rb")), "dead_iv": dead_iv, "audit": load_audit(auditp), "raw": load_raw(rawp)})
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
        st["depth"] = np.log10(st["size0"].clip(lower=1.0))
        st["spread"] = st["spA"] + st["spB"]
    st["bingx"] = st["f_bingx"].astype(float)
    pickle.dump({"st": st, "days": tab["days"], "t0": tab["t0"], "t1": tab["t1"]}, open(out, "wb"), protocol=4)
    print({"units": len(st), "stay": int((st["label"] == 0).sum()), "conv": int((st["label"] == 1).sum()), "unknown": int((st["label"] < 0).sum())}, round(time.time() - t))


# ---------------- оценка ----------------

def bal_acc(y, pred):
    """y: 1 — «остался» (положительный класс), pred — булев «признак говорит: останется». -> (BA, доля найденных «остался», доля пропущенных «сходится»)."""
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


def apply_rule(rule, x):
    """Правило (порог, знак, …) → «останется». Пропуск признака = «сходится» (фильтр не срабатывает)."""
    thr, sg = rule[0], rule[1]
    with np.errstate(invalid="ignore"):
        return np.isfinite(x) & ((x >= thr) if sg == 1 else (x <= thr))


def fit_rule(y, x):
    """Порог и знак по подбору: максимум BA по ВСЕМ единицам подбора (пропуск = «сходится»). -> (порог, знак, BA) или None; знак +1: «остался», если x ≥ порога."""
    f = np.isfinite(x)
    if (y[f] == 1).sum() < 3 or (y[f] == 0).sum() < 3:
        return None
    cands = np.unique(np.quantile(x[f], np.linspace(0.02, 0.98, 49)))
    best = (None, 1, 0.0)
    for thr in cands:
        for sg in (1, -1):
            ba = bal_acc(y, apply_rule((thr, sg), x))[0]
            if ba > best[2]:
                best = (float(thr), sg, ba)
    return best


def perm_p(P, N, F, tp_obs, n_perm=20_000, seed=7):
    """Перестановочное p (справка): число найденных «остался» при случайной перестановке меток — гипергеометрическое (P «остался», N «сходится», F помеченных); BA монотонна по числу найденных."""
    tp = np.random.default_rng(seed).hypergeometric(P, N, F, size=n_perm) if P and N and F else np.zeros(n_perm, int)
    return float((np.sum(tp >= tp_obs) + 1) / (n_perm + 1))


def coin_counts(y, pred, cid, nc):
    """По монетам: tp, fn, tn, fp (колонки)."""
    w = lambda m: np.bincount(cid, weights=m.astype(float), minlength=nc)  # noqa: E731
    return np.stack([w(pred & (y == 1)), w(~pred & (y == 1)), w(~pred & (y == 0)), w(pred & (y == 0))], axis=1)


def ba_of(c):
    """BA по счётчикам (…, 4) -> (…)."""
    tp, fn, tn, fp = c[..., 0], c[..., 1], c[..., 2], c[..., 3]
    return 0.5 * (tp / np.maximum(tp + fn, 1) + tn / np.maximum(tn + fp, 1))


def boot_weights(nc, n_boot=N_BOOT, seed=11, chunk=2000):
    """Веса монет в повторах бутстрепа (кластер — монета): (n_boot, nc) float32; одни и те же повторы для всех сравниваемых правил (парный бутстреп)."""
    rng = np.random.default_rng(seed)
    p = np.full(nc, 1.0 / nc)
    return np.concatenate([rng.multinomial(nc, p, size=min(chunk, n_boot - k)).astype(np.float32) for k in range(0, n_boot, chunk)])


def boot_ba(counts, W):
    return ba_of((W @ counts.astype(np.float32)).astype(np.float64))


def ci(v, alpha):
    return float(np.quantile(v, alpha / 2)), float(np.quantile(v, 1 - alpha / 2))


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
    A, Bm = [], []
    for f in FEATURES:
        a, b = tr[f].to_numpy(float), te[f].to_numpy(float)
        med = np.nanmedian(a) if np.isfinite(a).any() else 0.0
        sd = np.nanstd(a) if np.isfinite(a).sum() > 1 and np.nanstd(a) > 0 else 1.0
        A.append((np.where(np.isfinite(a), a, med) - med) / sd)
        Bm.append((np.where(np.isfinite(b), b, med) - med) / sd)
        if (~np.isfinite(a)).any():                         # индикатор пропуска — по подбору, не по проверке
            A.append((~np.isfinite(a)).astype(float))
            Bm.append((~np.isfinite(b)).astype(float))
    return np.c_[tuple(A)], np.c_[tuple(Bm)]


def pair_key(df):
    return (df["coin"] + "|" + df["exA"] + "|" + df["exB"]).to_numpy()


def pair_rate(tr, te, min_n=3):
    """База (б): доля «остался» у пары на подборе. У единиц подбора — без самой единицы (leave-one-out), у проверки — по всему подбору; в паре < min_n единиц подбора — NaN."""
    ktr, kte = pair_key(tr), pair_key(te)
    g = pd.DataFrame({"k": ktr, "y": tr["y"].to_numpy()}).groupby("k")["y"].agg(["sum", "count"])
    s, c = g["sum"].reindex(ktr).to_numpy(float), g["count"].reindex(ktr).to_numpy(float)
    x_tr = np.where(c - 1 >= min_n, (s - tr["y"].to_numpy()) / np.maximum(c - 1, 1), np.nan)
    s2, c2 = g["sum"].reindex(kte).to_numpy(float), g["count"].reindex(kte).to_numpy(float)
    x_te = np.where(c2 >= min_n, s2 / np.maximum(c2, 1), np.nan)
    return x_tr, x_te


def split_days(days):
    """Сутки GMT+4 с покрытием окна ≥ 12 ч (по порядку), первая половина → блок «ранние», последняя половина → «поздние»; при нечётном числе средние сутки не берутся. -> (годные, ранние, поздние)."""
    ok = [k for k in range(len(days) - 1) if days[k + 1] - days[k] >= MIN_DAY_MS]
    h = len(ok) // 2
    return ok, ok[:h], (ok[len(ok) - h:] if h else [])


def dist_to_block(t, lo, hi):
    return np.maximum(np.maximum(lo - t, t - hi), 0)


def score(te, pred, cid, nc, W, dayno_names):
    """Показатели правила на проверке (пропуск признака = «сходится»; BA по всей проверке). -> dict."""
    y = te["y"].to_numpy(int)
    ba, tpr, tnr = bal_acc(y, pred)
    cnt = coin_counts(y, pred, cid, nc)
    top = int(np.argmax(cnt[:, 0])) if nc else 0
    rest = cnt.sum(0) - cnt[top]
    flagged = int(pred.sum())
    P, N = int((y == 1).sum()), int((y == 0).sum())
    days_s = []
    good = 0
    dn = te["dayno"].to_numpy()
    for k in sorted(set(dn)):
        mk = dn == k
        has = (y[mk] == 1).any() and (y[mk] == 0).any()
        b_k = bal_acc(y[mk], pred[mk])[0] if has else np.nan
        good += int(has and b_k > 0.5)
        days_s.append(f"{dayno_names[k]}: {int((y[mk] == 1).sum())} ост. / BA {b_k:.2f}" if has else f"{dayno_names[k]}: {int((y[mk] == 1).sum())} ост. / BA —")
    tp_obs = int((pred & (y == 1)).sum())
    return {"ba": ba, "tpr": tpr, "tnr": tnr, "prec": (tp_obs / flagged) if flagged else np.nan, "flag": flagged / max(len(y), 1), "ba_notop": float(ba_of(rest)),
            "p": perm_p(P, N, flagged, tp_obs), "cnt": cnt, "boot": boot_ba(cnt, W), "good": good, "days": "; ".join(days_s), "ndays": len(set(dn))}


def evaluate(path_in, path_out):
    d = pickle.load(open(path_in, "rb"))
    st, days = d["st"], d["days"]
    nd = len(days) - 1
    names = {k: pd.to_datetime(days[k] + TZ_MS, unit="ms").strftime("%d.%m") for k in range(nd)}
    st = st[st["dur"] >= DECIDE_MS].copy()
    L = [f"Единиц (эпизоды вида «от центра», θ = 50 bps, строгий вариант, ≥ 5 с): {len(st)}. Суток GMT+4 в окне: {nd}.", ""]
    ok, early, late = split_days(days)
    cov_h = {k: round((days[k + 1] - days[k]) / 3.6e6, 1) for k in range(nd)}
    L += ["| сутки GMT+4 | покрытие окна, ч | берутся | роль |", "|---|---|---|---|"]
    for k in range(nd):
        role = "ранние" if k in early else ("поздние" if k in late else ("буфер (средние сутки при нечётном числе)" if k in ok else "—"))
        L.append(f"| {names[k]} | {cov_h[k]} | {'да' if k in ok else 'нет (< 12 ч)'} | {role} |")
    L.append("")
    if len(early) < 1:
        L.append("**Вердикт: не измеримо — меньше двух полных суток.**")
        open(path_out, "w", encoding="utf-8").write("\n".join(L))
        return
    st["dayno"] = np.searchsorted(np.array(days[1:-1]), st["t"].to_numpy(), "right")
    # воронка отбора (п. Б1, Б2)
    ok_units = st["dayno"].isin(ok)
    L += ["| этап (единицы ≥ 5 с) | единиц |", "|---|---|", f"| всего | {len(st)} |", f"| в негодных сутках (< 12 ч покрытия) — не берутся | {int((~ok_units).sum())} |"]
    L += [f"| снято «подделкой» на [t − запас, t + 5 с]: {nm} | {int(st[c].sum())} |" for nm, c in (("gap / audit", "f_gap"), ("stale (сырьё)", "f_stale"), ("funding", "f_funding"), ("tradfi", "f_tradfi"), ("delist", "f_delist"))]
    L += [f"| снято слоями всего (на отрезке до решения) | {int(st['f_fake'].sum())} |", f"| для сравнения: снято слоями по всему эпизоду (v4) | {int(st['f_fake_old'].sum())} |"]
    lay = st[~st["f_fake"] & ok_units].copy()
    L += [f"| остаток после слоёв (в годных сутках) | {len(lay)} |"]
    for why, txt in (("c0", "центр NaN"), ("tail", "до конца окна < 30 мин"), ("hole", "окно метки пересекает dead / обрыв / аудит"), ("raw", "у одной из ног нет сырья (символа нет в rawgaps)"),
                     ("cover", "времени вне молчания сырья (> 5 с, любая нога) < 95 %")):
        s = lay[lay["why"] == why]
        L.append(f"| метка −1: {txt} (из них возврат был виден на валидных тиках: {int(np.isfinite(s['tau']).sum())}) | {len(s)} |")
    st = st[~st["f_fake"] & (st["label"] >= 0) & ok_units].copy()
    st["y"] = (st["label"] == 0).astype(int)               # 1 — «остался»
    L += [f"| единиц с известной меткой в годных сутках | {len(st)} (остался {int(st['y'].sum())}, сходится {int((st['y'] == 0).sum())}) |", ""]
    L += ["| сутки | единиц | остался | сходится |", "|---|---|---|---|"]
    for k in ok:
        s = st[st["dayno"] == k]
        L.append(f"| {names[k]} | {len(s)} | {int(s['y'].sum())} | {int((s['y'] == 0).sum())} |")
    L += ["", f"Испытаний для поправки (признаки {len(FEATURES)} + совместная модель) × 2 направления + {PRIOR_PASSES} прошлых прохода v1–v3 = {N_TRIALS}; уровень скорректированного интервала 1 − 0,05/{N_TRIALS}; бутстреп по монетам, {N_BOOT} повторов; пропуск признака = «сходится».", ""]
    blocks = {"ранние": (days[early[0]], days[early[-1] + 1]), "поздние": (days[late[0]], days[late[-1] + 1])}
    parts = {f"А: подбор — сутки {', '.join(names[k] for k in early)} → проверка — {', '.join(names[k] for k in late)}": ("ранние", "поздние"),
             f"Б: подбор — сутки {', '.join(names[k] for k in late)} → проверка — {', '.join(names[k] for k in early)}": ("поздние", "ранние")}
    res = {}
    scope_narrow = False
    t_all = st["t"].to_numpy()
    for nm, (fit_b, test_b) in parts.items():
        fit_days, test_days = (early, late) if fit_b == "ранние" else (late, early)
        in_fit, in_te = st["dayno"].isin(fit_days).to_numpy(), st["dayno"].isin(test_days).to_numpy()
        keep_fit = in_fit & (dist_to_block(t_all, *blocks[test_b]) >= EMBARGO_FIT_MS)
        keep_te = in_te & (dist_to_block(t_all, *blocks[fit_b]) >= EMBARGO_TEST_MS)
        tr, te = st[keep_fit].copy(), st[keep_te].copy()
        ytr, yte = tr["y"].to_numpy(), te["y"].to_numpy()
        L += [f"### {nm}", "", f"Подбор {len(tr)} ({int(ytr.sum())} остался; выброшено эмбарго {int(in_fit.sum() - keep_fit.sum())}), проверка {len(te)} ({int(yte.sum())} остался, {int((yte == 0).sum())} сходится; выброшено эмбарго {int(in_te.sum() - keep_te.sum())}). Доля «остался» в проверке: {100 * yte.mean():.1f} %.", ""]
        lay_fit = lay["dayno"].isin(fit_days).to_numpy() & np.isfinite(lay["flow_min"].to_numpy())
        if lay_fit.any():                                    # метка −1 по покрытию по третям ликвидности (flow_min; пороги — по подбору): не режет ли правило тихие пары
            qs = np.quantile(lay.loc[lay_fit, "flow_min"], [1 / 3, 2 / 3])
            lay_d = lay[lay["dayno"].isin(fit_days + test_days)]
            fm = lay_d["flow_min"].to_numpy()
            terc = np.where(np.isfinite(fm), np.digitize(np.nan_to_num(fm), qs), 3)
            trows = []
            for k, tn in enumerate(("нижняя", "средняя", "верхняя", "flow_min неизвестен (нет записи сделок)")):
                m = terc == k
                n_, nc_, nr_ = int(m.sum()), int((lay_d["why"].to_numpy()[m] == "cover").sum()), int((lay_d["why"].to_numpy()[m] == "raw").sum())
                scope_narrow |= k < 3 and nc_ / max(n_, 1) > COVER_SCOPE
                trows.append({"треть ликвидности (flow_min)": tn, "порог flow_min": f"< {qs[0]:.2f}" if k == 0 else (f"{qs[0]:.2f}…{qs[1]:.2f}" if k == 1 else (f"≥ {qs[1]:.2f}" if k == 2 else "—")), "единиц после слоёв": n_,
                              "метка −1 по покрытию": nc_, "доля, %": round(100 * nc_ / max(n_, 1), 1), "метка −1: нет сырья": nr_})
            L += [_md(pd.DataFrame(trows)), ""]
        if (yte == 1).sum() < MIN_STAY:
            L += [f"«Остался» в проверке {int(yte.sum())} < {MIN_STAY}: **не измеримо: мало случаев**; числа ниже приводятся с этой пометкой.", ""]
        coin_ids, cid = np.unique(te["coin"].to_numpy(), return_inverse=True)
        nc = len(coin_ids)
        W = boot_weights(nc)
        rows, sc = [], {}

        def run(label, x_tr, x_te, model=False):
            rule = fit_rule(ytr, x_tr)
            if rule is None:
                rows.append({"признак": label, "покрытие, %": round(100 * float(np.isfinite(x_te).mean()), 1)})
                return None
            pred = apply_rule(rule, x_te)
            s = score(te, pred, cid, nc, W, names)
            s["auc"] = auc(yte, x_te if rule[1] == 1 else -x_te)
            s["rule"], s["cov"] = rule, 100 * float(np.isfinite(x_te).mean())
            sc[label] = s
            return s
        base_edge = run("edge0 (база)", tr["edge0"].to_numpy(float), te["edge0"].to_numpy(float))
        x_tr_pr, x_te_pr = pair_rate(tr, te)
        base_pair = run("доля «остался» у пары на подборе (база)", x_tr_pr, x_te_pr)
        for f in FEATURES:
            run(f, tr[f].to_numpy(float), te[f].to_numpy(float))
        Xtr, Xte = design(tr, te)
        w = logit_fit(Xtr, ytr.astype(float))
        run("совместная модель (логистическая)", logit_pred(w, Xtr), logit_pred(w, Xte))
        for label, s in sc.items():
            row = {"признак": label, "покрытие, %": round(s["cov"], 1), "порог": round(s["rule"][0], 3), "знак": "≥" if s["rule"][1] == 1 else "≤", "BA проверка": round(s["ba"], 3),
                   "найдено «остался», %": round(100 * s["tpr"], 1), "пропущено «сходится», %": round(100 * s["tnr"], 1), "точность, %": round(100 * s["prec"], 1) if s["prec"] == s["prec"] else None,
                   "помечено, %": round(100 * s["flag"], 1), "BA без лучшей монеты": round(s["ba_notop"], 3), "AUC": round(s["auc"], 3) if s["auc"] == s["auc"] else None, "p (справка)": round(s["p"], 4)}
            lo95, hi95 = ci(s["boot"], 0.05)
            loA, hiA = ci(s["boot"], ALPHA_ADJ)
            row["BA 95 %"], row["BA скорр."] = f"{lo95:.2f}…{hi95:.2f}", f"{loA:.2f}…{hiA:.2f}"
            if label.endswith("(база)"):
                rows.append(row)
                continue
            dlo = {}
            for bn, b in (("edge0", base_edge), ("пара", base_pair)):
                if b is None:
                    dlo[bn] = None
                    continue
                dv = s["boot"] - b["boot"]
                dlo[bn] = ci(dv, ALPHA_ADJ)[0]
                row[f"Δ к {bn}: BA, нижняя скорр."] = f"{s['ba'] - b['ba']:+.3f}, {dlo[bn]:+.3f}"
            row["сутки проверки с BA > 0,5"] = f"{s['good']} из {s['ndays']}"
            row["по суткам"] = s["days"]
            rows.append(row)
            need = 2 if s["ndays"] >= 2 else 1
            res[(nm, label)] = {"stay_ok": int(yte.sum()) >= MIN_STAY, "ba": s["ba"], "ba_lo": loA, "d_edge": dlo["edge0"], "d_pair": dlo["пара"], "day_ok": s["good"] >= need}
        L += [_md(pd.DataFrame(rows)), ""]
    # правило вердикта протокола (дословно как в протоколе § 5)
    verdict, unmeasurable = [], False
    for f in FEATURES + ["совместная модель (логистическая)"]:
        r = [res.get((nm, f)) for nm in parts]
        if any(x is None for x in r):
            continue
        if not all(x["stay_ok"] for x in r):
            unmeasurable = True
            continue
        if all(x["ba"] >= BA_MIN and x["ba_lo"] > 0.5 and x["d_edge"] is not None and x["d_edge"] > 0 and x["d_pair"] is not None and x["d_pair"] > 0 and x["day_ok"] for x in r):
            verdict.append(f)
    L += [f"**Вердикт по правилу протокола (BA ≥ {BA_MIN}; нижняя скорр. граница BA по монетам > 0,5; нижняя скорр. граница прироста BA над edge0 и над долей «остался» у пары > 0; BA > 0,5 минимум в двух сутках проверки (при двух — в обеих); «остался» в проверке ≥ {MIN_STAY} — всё в обоих направлениях):** "
          + (", ".join(verdict) if verdict else ("не измеримо: мало случаев (в одной из частей «остался» < " + str(MIN_STAY) + ")" if unmeasurable else "ни один признак не годится")), "",
          *([f"Охват вердикта: **только пары с покрытием ≥ 95 %** — метка −1 по покрытию сняла > {int(100 * COVER_SCOPE)} % единиц хотя бы в одной трети ликвидности (таблицы выше); решение не меняется, сужается охват.", ""] if scope_narrow else []),
          "Все числа — «найдено на этих данных»: признаки выбраны после прошлых взглядов на те же сутки (v1–v3), поправка — в счёте испытаний выше."]
    open(path_out, "w", encoding="utf-8").write("\n".join(L))


def _md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if (v is None or (isinstance(v, float) and v != v)) else str(v) for v in r.tolist()) + " |")
    return "\n".join(out)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(*sys.argv[2:10], int(sys.argv[10]) if len(sys.argv) > 10 else 4)
    else:
        evaluate(sys.argv[2], sys.argv[3])
