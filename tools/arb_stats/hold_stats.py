"""Таблицы отчёта arb-holding из таблицы эпизодов с флагами (hold_filter): счёт по порогам длительности, распределение, снятие по причинам, исполнимость.

Запуск: python3 hold_stats.py <flagged.pkl> <выход.md> <cases.csv>
"""
import pickle
import sys

import numpy as np
import pandas as pd

from hold_common import BUCKETS_S, FEE_RT_BPS, THETAS
from hold_filter import FILTERS

THR_S = (1,) + BUCKETS_S
DELAYS_S = (1, 3, 5)
KINDS = ("c-t", "z-t", "x1-t")
SIZE_CAP = 100.0                      # $ на круг, как в прежних отчётах
VIEW = {"z": "от нуля", "c": "от центра"}
KIND_RU = {"c-t": "к центру", "z-t": "к нулю", "x1-t": "из − в +"}


def fmt_s(s):
    return f"{s} с" if s < 120 else (f"{s // 60} мин" if s < 3600 else f"{s // 3600} ч")


def md(df, floatfmt=None):
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(v) for v in r.tolist()) + " |")
    return "\n".join(out)


def cover(tab):
    """Дней покрытия пары по виду и число суток GMT+4 с покрытием ≥ 6 ч: {(coin, a, b): {view: (дней, суток)}}."""
    t1, days = tab["t1"], tab["days"]
    res = {}
    for k, p in tab["pairs"].items():
        d = {}
        for v in ("z", "c"):
            s = p["start"][v]
            d[v] = ((t1 - s) / 86_400_000, sum(1 for a, b in zip(days[:-1], days[1:]) if min(b, t1) - max(a, s) >= 6 * 3_600_000))
        res[k] = d
    return res


def counts_by_threshold(df, tab, cov, th, view):
    """Строка на порог длительности: эпизодов, пар, эпизодов/сут (все пары / окно), пар с ≥ 1 эп./сут, пар с эпизодом в каждые сутки покрытия."""
    sub = df[(df["th"] == th) & (df["view"] == view)]
    win_days = (tab["t1"] - tab["t0"]) / 86_400_000
    rows = []
    for thr in THR_S:
        s = sub[sub["dur"] >= thr * 1000]
        if not len(s):
            rows.append([fmt_s(thr), 0, 0, "0", 0, 0])
            continue
        g = s.groupby(["coin", "exA", "exB"])
        cnt, ndays = g.size(), g["day"].nunique()
        rate_ge1 = sum(c / max(cov[k][view][0], 1e-9) >= 1.0 for k, c in cnt.items())
        every = sum(ndays[k] >= cov[k][view][1] for k in cnt.index)
        rows.append([fmt_s(thr), len(s), len(cnt), f"{len(s) / win_days:.1f}", int(rate_ge1), int(every)])
    return pd.DataFrame(rows, columns=["длительность ≥", "эпизодов", "пар", "эпизодов/сут", "пар с ≥ 1 эп./сут", "пар с эпизодом каждые сутки"])


def duration_hist(df, th, view, mask=None):
    edges = [1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 10 ** 9]
    sub = df[(df["th"] == th) & (df["view"] == view)]
    if mask is not None:
        sub = sub[mask[sub.index]]
    h, _ = np.histogram(sub["dur"].to_numpy() / 1000, edges)
    return h


def filter_table(df, th, view, thrs=(1, 5, 60, 600)):
    """Снятие по причинам: «слой» — первая сработавшая причина (по порядку), «затронуто» — независимые флаги. Колонки — пороги длительности."""
    sub = df[(df["th"] == th) & (df["view"] == view)]
    rows = []
    for name in ("всего",) + FILTERS + ("остаётся",):
        r = [name]
        for thr in thrs:
            s = sub[sub["dur"] >= thr * 1000]
            if name == "всего":
                r.append(f"{len(s)}")
            elif name == "остаётся":
                r.append(f"{int(s['keep'].sum())}")
            else:
                r.append(f"{int((s['layer'] == name).sum())} ({int(s['f_' + name].sum())})")
        rows.append(r)
    return pd.DataFrame(rows, columns=["причина: слой (затронуто)"] + [f"≥ {fmt_s(t)}" for t in thrs])


def sim_cols(d, kind):
    p = f"{kind}_{d}_"
    return {c: p + c for c in ("net", "gross", "size", "tx", "te", "edge_tx", "c_tx", "how")}


def trade_row(sub, d, kind, win_days, n_days=4):
    c = sim_cols(d, kind)
    net, gross, size = sub[c["net"]].to_numpy(), sub[c["gross"]].to_numpy(), sub[c["size"]].to_numpy()
    entered = np.isfinite(sub[c["tx"]].to_numpy())
    closed = np.isfinite(net)
    cap = np.minimum(size, SIZE_CAP)
    usd = np.where(closed, cap * net / 1e4, 0.0)
    plc = np.where(closed, cap * (-gross - FEE_RT_BPS) / 1e4, 0.0)
    days = sub["day"].to_numpy()
    per_day = [usd[days == k].sum() for k in range(n_days)]
    how = sub[c["how"]].to_numpy()
    return {"эпизодов": len(sub), "вошли": int(entered.sum()), "закрыто по правилу": int((closed & (how == 1)).sum()), "по потолку 1 ч": int((closed & (how == 2)).sum()),
            "net, медиана bps": round(float(np.median(net[closed])), 1) if closed.any() else None, "доля круга > 0": round(float((net[closed] > 0).mean()), 2) if closed.any() else None,
            "$/сут": round(float(usd.sum() / win_days), 1), "плацебо $/сут": round(float(plc.sum() / win_days), 1), "суток с плюсом": f"{int((np.array(per_day) > 0).sum())} из {len(per_day)}"}


def trade_table(df, tab, th, view, mask=None, delays=DELAYS_S, kinds=KINDS):
    sub = df[(df["th"] == th) & (df["view"] == view)]
    if mask is not None:
        sub = sub[mask[sub.index]]
    win_days = (tab["t1"] - tab["t0"]) / 86_400_000
    rows = []
    for d in delays:
        for kind in kinds:
            r = trade_row(sub, d, kind, win_days, len(tab["days"]) - 1)
            rows.append({"задержка, с": d, "выход": KIND_RU[kind], **r})
    return pd.DataFrame(rows)


def cases_csv(df, path):
    """Полный список эпизодов ≥ 1 с: пара, биржи, начало (UTC и GMT+4), длительность, разрыв, вид, θ, возраст ног, причины снятия."""
    t = pd.to_datetime(df["t"], unit="ms", utc=True)
    out = pd.DataFrame({"монета": df["coin"], "биржа_A": df["exA"], "биржа_B": df["exB"], "символ_A": df["symA"], "символ_B": df["symB"], "вид": df["view"].map(VIEW), "θ_%": df["th"] / 100,
                        "направление": np.where(df["side"] == 1, "A дороже (продать A, купить B)", "A дешевле (купить A, продать B)"),
                        "начало_UTC": t.dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:-3], "начало_GMT+4": (t + pd.Timedelta(hours=4)).dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:-3],
                        "длительность_с": (df["dur"] / 1000).round(3), "не_закончился_до_конца_окна": df["open"], "разрыв_в_начале_bps": df["edge0"].round(1), "разрыв_макс_bps": df["edgemax"].round(1),
                        "центр_bps": df["c0"].round(1), "размер_вершины_$": df["size0"].round(0), "возраст_A_с": (df["ageA"] / 1000).round(2), "возраст_B_с": (df["ageB"] / 1000).round(2),
                        "обновлений_A": df["nA"], "обновлений_B": df["nB"], "слой_снятия": df["layer"], "остаётся": df["keep"].astype(int)})
    out.sort_values(["монета", "биржа_A", "биржа_B", "вид", "θ_%", "начало_UTC"]).to_csv(path, index=False, encoding="utf-8")


def pair_stats(sub, d, kind, win_days, n_days, win_h):
    """По парам (монета × биржи) для одной настройки (θ, вид уже выбраны в sub): круги, $/сут (≤ 100 $ на круг), $ по суткам, рейтинг и плацебо как в прежних отчётах
    (кругов ≥ окно_ч / 6 и медиана net > 0 в каждых из суток покрытия; плацебо — тот же счёт для обратного знака: net_pl = −gross − комиссия)."""
    c = sim_cols(d, kind)
    x = sub[["coin", "exA", "exB", "day", c["net"], c["gross"], c["size"]]].copy()
    x.columns = ["coin", "exA", "exB", "day", "net", "gross", "size"]
    x = x[np.isfinite(x["net"])]
    if not len(x):
        return pd.DataFrame()
    x["usd"] = np.minimum(x["size"], SIZE_CAP) * x["net"] / 1e4
    x["pl"] = -x["gross"] - FEE_RT_BPS
    x["usd_pl"] = np.minimum(x["size"], SIZE_CAP) * x["pl"] / 1e4
    g = x.groupby(["coin", "exA", "exB"])
    out = g.agg(кругов=("net", "size"), net_мед=("net", "median"), usd=("usd", "sum"), usd_pl=("usd_pl", "sum"), size_мед=("size", "median")).reset_index()
    out["$/сут"] = out["usd"] / win_days
    out["плацебо $/сут"] = out["usd_pl"] / win_days
    per_day = x.groupby(["coin", "exA", "exB", "day"])
    med = per_day["net"].median().unstack("day")
    medp = per_day["pl"].median().unstack("day")
    cnt = per_day["net"].size().unstack("day")
    usd_d = per_day["usd"].sum().unstack("day").reindex(columns=range(n_days)).fillna(0.0)
    out = out.set_index(["coin", "exA", "exB"])
    need = win_h / 6
    out["по суткам $"] = [" / ".join(f"{v:+.1f}" for v in usd_d.loc[k].tolist()) for k in out.index]
    out["суток с плюсом"] = [int((usd_d.loc[k] > 0).sum()) for k in out.index]
    ok = (out["кругов"] >= need).to_numpy()
    out["рейтинг"] = [bool(o and (med.loc[k].dropna() > 0).all() and len(med.loc[k].dropna()) >= n_days) for k, o in zip(out.index, ok)]
    out["плацебо"] = [bool(o and (medp.loc[k].dropna() > 0).all() and len(medp.loc[k].dropna()) >= n_days) for k, o in zip(out.index, ok)]
    return out.reset_index()


if __name__ == "__main__":
    tab = pickle.load(open(sys.argv[1], "rb"))
    df = tab["df"]
    cov = cover(tab)
    lines = []
    for th in THETAS:
        for v in ("z", "c"):
            lines += [f"### Счёт по порогам: θ = {th / 100:g} %, {VIEW[v]}, без фильтров", md(counts_by_threshold(df, tab, cov, th, v)), ""]
            lines += [f"### То же, остаётся после фильтров: θ = {th / 100:g} %, {VIEW[v]}", md(counts_by_threshold(df[df['keep']], tab, cov, th, v)), ""]
            lines += [f"### Снятие по причинам: θ = {th / 100:g} %, {VIEW[v]}", md(filter_table(df, th, v)), ""]
    open(sys.argv[2], "w", encoding="utf-8").write("\n".join(lines))
    cases_csv(df, sys.argv[3])
