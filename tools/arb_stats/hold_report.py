"""Сборка таблиц отчёта arb-holding (Markdown) и CSV по парам из flagged.pkl (hold_filter) и resim.pkl (hold_resim) для одного варианта эпизодов (строгий / допускающий).

Запуск: python3 hold_report.py <flagged.pkl> <resim.pkl|-> <tables.md> <pairs.csv> <cases.csv|->
Два прочтения «нога устарела > 5 с»: «сырьё» (keep, layer: нога молчала > 5 с по сырым записям книги, смены размера считаются) и «цена» (keep_p, layer_p: лучшая цена ноги не менялась > 5 с).
"""
import pickle
import sys

import numpy as np
import pandas as pd

import hold_stats as hst
from hold_common import THETAS
from hold_filter import FILTERS

VIEWS = ("z", "c")
HOLD_GROUPS = ((1, 5), (5, 60), (60, 600), (600, 10 ** 9))
READ = {"keep": ("сырьё: молчание ноги > 5 с", "layer", "f_stale"), "keep_p": ("цена: лучшая цена не менялась > 5 с", "layer_p", "f_stale_price")}


def combo_name(th, v):
    return f"θ = {th / 100:g} %, {hst.VIEW[v]}"


def counts_combined(df, tab, cov, th, v):
    a = hst.counts_by_threshold(df, tab, cov, th, v)
    cols = {"длительность ≥": a["длительность ≥"], "до фильтров: эпизодов": a["эпизодов"], "до: пар": a["пар"], "до: пар с ≥ 1 эп./сут": a["пар с ≥ 1 эп./сут"]}
    for keep, tag in (("keep", "сырьё"), ("keep_p", "цена")):
        k = hst.counts_by_threshold(df[df[keep]], tab, cov, th, v)
        cols.update({f"{tag}: эпизодов": k["эпизодов"], f"{tag}: пар": k["пар"], f"{tag}: эп./сут": k["эпизодов/сут"], f"{tag}: пар с ≥ 1 эп./сут": k["пар с ≥ 1 эп./сут"],
                     f"{tag}: пар с эпизодом каждые сутки": k["пар с эпизодом каждые сутки"]})
        if keep == "keep":
            nb = hst.counts_by_threshold(df[df[keep] & ~df["f_bingx"]], tab, cov, th, v)
            cols.update({"сырьё без BingX: эпизодов": nb["эпизодов"], "сырьё без BingX: пар": nb["пар"]})
    return pd.DataFrame(cols)


def dist_table(df, th, v):
    edges = [1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 10 ** 9]
    names = ["1–2 с", "2–5 с", "5–10 с", "10–30 с", "30–60 с", "1–2 мин", "2–5 мин", "5–10 мин", "10–30 мин", "30–60 мин", "≥ 1 ч"]
    sub = df[(df["th"] == th) & (df["view"] == v)]
    a, _ = np.histogram(sub["dur"] / 1000, edges)
    k, _ = np.histogram(sub.loc[sub["keep"], "dur"] / 1000, edges)
    p, _ = np.histogram(sub.loc[sub["keep_p"], "dur"] / 1000, edges)
    return pd.DataFrame({"длительность": names, "до фильтров": a, "после (сырьё)": k, "доля (сырьё), %": np.round(100 * k / np.maximum(a, 1), 1), "после (цена)": p, "доля (цена), %": np.round(100 * p / np.maximum(a, 1), 1)})


def top_pairs(sub, thr, tab, n=15):
    s = sub[sub["dur"] >= thr * 1000]
    if not len(s):
        return pd.DataFrame()
    win_days = (tab["t1"] - tab["t0"]) / 86_400_000
    nd = len(tab["days"]) - 1
    g = s.groupby(["coin", "exA", "exB"])
    out = g.agg(эпизодов=("dur", "size"), суток=("day", "nunique"), мед_длит_с=("dur", lambda x: round(float(np.median(x)) / 1000, 1)), макс_длит_с=("dur", lambda x: round(float(x.max()) / 1000, 1)),
                мед_размер_usd=("size0", lambda x: round(float(np.median(x)))), мед_разрыв_bps=("edge0", lambda x: round(float(np.median(x)), 1)), макс_разрыв_bps=("edgemax", lambda x: round(float(x.max()), 1))).reset_index()
    out.insert(4, "эп./сут", (out["эпизодов"] / win_days).round(2))
    per = s.groupby(["coin", "exA", "exB", "day"]).size().unstack("day").reindex(columns=range(nd)).fillna(0).astype(int)
    out["по суткам"] = ["/".join(str(int(x)) for x in per.loc[(c, a, b)].tolist()) for c, a, b in zip(out["coin"], out["exA"], out["exB"])]
    return out.sort_values("эпизодов", ascending=False).head(n)


def pairs_csv(df, tab, path):
    rows = []
    for (coin, a, b, th, v), g in df.groupby(["coin", "exA", "exB", "th", "view"]):
        k = g[g["keep"]]
        p = g[g["keep_p"]]
        r = {"монета": coin, "биржа_A": a, "биржа_B": b, "θ_%": th / 100, "вид": hst.VIEW[v], "макс_длительность_с": round(g["dur"].max() / 1000, 1),
             "макс_длительность_после_фильтров_сырьё_с": round(k["dur"].max() / 1000, 1) if len(k) else None, "макс_длительность_после_фильтров_цена_с": round(p["dur"].max() / 1000, 1) if len(p) else None}
        for thr in (1, 5, 10, 30, 60, 600, 3600):
            r[f"эпизодов ≥ {thr} с"] = int((g["dur"] >= thr * 1000).sum())
            r[f"после фильтров (сырьё) ≥ {thr} с"] = int((k["dur"] >= thr * 1000).sum())
            r[f"после фильтров (цена) ≥ {thr} с"] = int((p["dur"] >= thr * 1000).sum())
        r["по суткам ≥ 5 с после фильтров (сырьё)"] = "/".join(str(int(((k["day"] == d) & (k["dur"] >= 5000)).sum())) for d in range(len(tab["days"]) - 1))
        r["BingX"] = int(g["f_bingx"].iloc[0])
        rows.append(r)
    pd.DataFrame(rows).sort_values(["θ_%", "вид", "после фильтров (сырьё) ≥ 5 с"], ascending=[True, True, False]).to_csv(path, index=False, encoding="utf-8")


def trade_by_hold(sub, tab, d, kind):
    rows = []
    win_days = (tab["t1"] - tab["t0"]) / 86_400_000
    for lo, hi in HOLD_GROUPS:
        s = sub[(sub["dur"] >= lo * 1000) & (sub["dur"] < hi * 1000)]
        r = hst.trade_row(s, d, kind, win_days, len(tab["days"]) - 1) if len(s) else {}
        rows.append({"длительность": f"{hst.fmt_s(lo)} – {hst.fmt_s(hi) if hi < 10 ** 9 else '…'}", **r})
    return pd.DataFrame(rows)


def funding_row(sub, d, kind):
    """Доля закрытых кругов с выплатой фандинга внутри удержания и фандинг bps (медиана |·|, сумма) — если столбцы есть."""
    fc, pc, nc = f"{kind}_{d}_fund", f"{kind}_{d}_npay", f"{kind}_{d}_net"
    if fc not in sub.columns:
        return {}
    c = np.isfinite(sub[nc].to_numpy()) & np.isfinite(sub[pc].to_numpy())
    if not c.any():
        return {}
    pay = sub[pc].to_numpy()[c] > 0
    f = sub[fc].to_numpy()[c]
    return {"кругов с известным фандингом": int(c.sum()), "с выплатой внутри удержания, %": round(100 * float(pay.mean()), 1), "фандинг на круг, bps (медиана по выплатам)": round(float(np.median(f[pay])), 2) if pay.any() else None,
            "фандинг на круг, bps (среднее по всем)": round(float(f.mean()), 3)}


def per_day_table(df, tab, th, v, keep):
    """Устойчивость по суткам GMT+4: эпизодов и пар с эпизодами ≥ 5 с / ≥ 60 с по каждым суткам окна (до фильтров и после)."""
    days = tab["days"]
    sub = df[(df["th"] == th) & (df["view"] == v)]
    rows = []
    for d in range(len(days) - 1):
        lab = pd.to_datetime(days[d] + 4 * 3_600_000, unit="ms").strftime("%d.%m")
        hrs = (min(days[d + 1], tab["t1"]) - days[d]) / 3_600_000
        s = sub[sub["day"] == d]
        k = s[s[keep]]
        r = {"сутки (GMT+4)": lab, "часов окна": round(hrs, 1)}
        for thr in (5, 60):
            r[f"до: эпизодов ≥ {thr} с"] = int((s["dur"] >= thr * 1000).sum())
            r[f"после: эпизодов ≥ {thr} с"] = int((k["dur"] >= thr * 1000).sum())
            r[f"после: пар ≥ {thr} с"] = int(k[k["dur"] >= thr * 1000].groupby(["coin", "exA", "exB"]).ngroups)
        rows.append(r)
    return pd.DataFrame(rows)


def main(flagp, resimp, out_md, out_pairs, out_cases):
    tab = pickle.load(open(flagp, "rb"))
    df = tab["df"]
    R = pickle.load(open(resimp, "rb")) if resimp != "-" else None
    cov = hst.cover(tab)
    win_h = (tab["t1"] - tab["t0"]) / 3_600_000
    win_days = win_h / 24
    n_days = len(tab["days"]) - 1
    L = []
    for th in THETAS:
        for v in VIEWS:
            L += [f"### Счёт по порогам длительности — {combo_name(th, v)}", hst.md(counts_combined(df, tab, cov, th, v)), ""]
    for th in THETAS:
        for v in VIEWS:
            for keep, (nm, lay, st) in READ.items():
                L += [f"### Снятие по причинам, прочтение «{nm}» — {combo_name(th, v)}", hst.md(hst.filter_table(df, th, v, thrs=(1, 5, 60, 600), layer_col=lay, keep_col=keep, stale_col=st)), ""]
    for th in THETAS:
        for v in VIEWS:
            L += [f"### Распределение длительности — {combo_name(th, v)}", hst.md(dist_table(df, th, v)), ""]
    for th in THETAS:
        for v in VIEWS:
            for keep, (nm, _, _) in READ.items():
                L += [f"### По суткам, остаток («{nm}») — {combo_name(th, v)}", hst.md(per_day_table(df, tab, th, v, keep)), ""]
    for th in THETAS:
        for v in VIEWS:
            for keep, (nm, _, _) in READ.items():
                for thr in (5, 60, 600):
                    tp = top_pairs(df[(df["th"] == th) & (df["view"] == v) & df[keep]], thr, tab)
                    L += [f"### Пары после фильтров («{nm}») с эпизодами ≥ {hst.fmt_s(thr)} — {combo_name(th, v)}", hst.md(tp) if len(tp) else "нет", ""]
    if R is not None:
        for th in THETAS:
            for v in VIEWS:
                for keep, (nm, _, _) in READ.items():
                    kp = R[keep]
                    L += [f"### Исполнимость на оставшихся («{nm}») — {combo_name(th, v)}", hst.md(hst.trade_table(kp, tab, th, v)), ""]
                    s = kp[(kp["th"] == th) & (kp["view"] == v)]
                    fr = [{"задержка, с": d, "выход": hst.KIND_RU[k], **funding_row(s, d, k)} for d in hst.DELAYS_S for k in hst.KINDS]
                    if any(len(x) > 2 for x in fr):
                        L += [f"### Фандинг за удержание («{nm}») — {combo_name(th, v)}", hst.md(pd.DataFrame(fr)), ""]
                    for kind in hst.KINDS:
                        L += [f"### Исполнимость по длительности эпизода («{nm}»), задержка 1 с, выход {hst.KIND_RU[kind]} — {combo_name(th, v)}", hst.md(trade_by_hold(s, tab, 1, kind)), ""]
                if f"{hst.KINDS[0]}_1_net" in df.columns:
                    L += [f"### Исполнимость без фильтров (все эпизоды ≥ 1 с, круги по прежнему ряду) — {combo_name(th, v)}", hst.md(hst.trade_table(df, tab, th, v)), ""]
        kp = R["keep"]
        for th in THETAS:
            for v in VIEWS:
                s = kp[(kp["th"] == th) & (kp["view"] == v)]
                for kind in hst.KINDS:
                    for d in hst.DELAYS_S:
                        ps = hst.pair_stats(s, d, kind, win_days, n_days, win_h)
                        if not len(ps):
                            continue
                        ps = ps.sort_values("$/сут", ascending=False)
                        L += [f"### Пары по $/сут (оставшиеся, сырьё), задержка {d} с, выход {hst.KIND_RU[kind]} — {combo_name(th, v)}: пар с кругами {len(ps)}, в плюсе {int((ps['$/сут'] > 0).sum())}, рейтинг {int(ps['рейтинг'].sum())}, плацебо {int(ps['плацебо'].sum())}",
                              hst.md(ps.head(10)[["coin", "exA", "exB", "кругов", "net_мед", "size_мед", "$/сут", "плацебо $/сут", "по суткам $", "суток с плюсом", "рейтинг", "плацебо"]].round(2)), ""]
    open(out_md, "w", encoding="utf-8").write("\n".join(L))
    pairs_csv(df, tab, out_pairs)
    if out_cases != "-":
        hst.cases_csv(df, out_cases)


if __name__ == "__main__":
    main(*sys.argv[1:6])
