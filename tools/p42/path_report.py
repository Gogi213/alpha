"""П-42 §13.19, шаг 1: путь цены относительно уровня по каждой сделке набора (`*-detail.jsonl`, по умолчанию `ow_e`) — MFE/MAE, пересечение уровня,
возврат за уровень, что закрыло сделку, сколько отдано; плюсовые (награда против принятого риска) и минусовые (классы причин). Описательно, 0 испытаний.
Лента — aggTrades (`/data/aggtrades/binance`), от входа до выхода + 60 мин; цены — длинная рама (шорт × −1), как в движке. Медиану результатов не пишем.

Запуск на srv (systemd-run, nice 19):
  python3 path_report.py <каталог варианта с long-detail.jsonl и short-detail.jsonl> <выходной каталог> [--workers 6]
Выход: <out>/paths.csv (строка на сделку), <out>/summary.txt (сводки), <out>/summary.json.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np

AGG = os.environ.get("P42_AGG", "/data/aggtrades/binance")
T0 = 1788220800000  # 01.09.2026 00:00 UTC, мс
DAY = 86_400_000
HOUR = 3_600_000
START_DAY = 14  # 15.09 = сутки 14 от 01.09
NOTIONAL = 500.0
F_BP = (5, 10, 20, 30)  # глубины возврата, бп
POST_MS = HOUR
MAE_PCT = (0.2, 0.3, 0.5, 1.0, 2.0, 5.0)
MAE_NATR = (0.5, 1.0, 1.5, 2.0, 3.0)


def extract(sym, lo_ms, hi_ms):
    """Сделки монеты с transact_time в [lo_ms, hi_ms] (абс. мс): (t, p, bm) по времени; awk по распакованным суткам."""
    ts, ps, bs = [], [], []
    for d in range((lo_ms - T0) // DAY, (hi_ms - T0) // DAY + 1):
        day = (date(2026, 9, 1) + timedelta(d)).isoformat()
        z = f"{AGG}/{sym}/{sym}-aggTrades-{day}.zip"
        if not os.path.exists(z):
            continue
        prog = 'NR>1 && $6>=%d && $6<=%d {print $6, $2, ($7=="true")?1:0} NR>1 && $6>%d {exit}' % (lo_ms, hi_ms, hi_ms + HOUR)
        out = subprocess.run(["bash", "-c", f"unzip -p {z} | awk -F, '{prog}'"], capture_output=True, text=True).stdout
        if not out.strip():
            continue
        a = np.fromstring(out, sep=" ").reshape(-1, 3)
        ts.append(a[:, 0])
        ps.append(a[:, 1])
        bs.append(a[:, 2])
    if not ts:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, bool)
    t = np.concatenate(ts).astype(np.int64)
    p = np.rint(np.concatenate(ps) * 1e8).astype(np.int64)
    b = np.concatenate(bs).astype(bool)
    o = np.argsort(t, kind="stable")
    return t[o], p[o], b[o]


def analyse(r):
    """r — запись detail. Возвращает словарь признаков пути."""
    s = 1 if r["side"] == "long" else -1
    entry_abs, exit_abs = T0 + r["entry_ms"], T0 + r["exit_ms"]
    t, p8, _ = extract(r["sym"], entry_abs, exit_abs + POST_MS)
    ef = s * r["entry_px_e8"]
    lf = s * r["level_px_e8"]
    piv = [s * m[1] for m in r["members"]]
    pfar, ptop = min(piv), max(piv)
    natr = r["natr_e8"] / 1e8
    la = abs(lf)
    ps = s * p8
    hold = (t > entry_abs) & (t <= exit_abs)
    th, ph = t[hold], ps[hold]
    d = dict(id="", side=r["side"], sym=r["sym"], i=r["i"], kind=r["kind"], takes=r["takes"], net=r["net"], gross=r["gross"], fees=r["fees"],
             funding=r["funding"], natr_pct=natr * 100, entry_ms=r["entry_ms"], exit_ms=r["exit_ms"], sig_ms=r["sig_ms"], hold_s=(r["exit_ms"] - r["entry_ms"]) / 1000,
             half="A" if (r["entry_ms"] // DAY - START_DAY) < 30 else "B", n_ticks=int(hold.sum()),
             exit_tick_ok=bool(len(th) and th[-1] == exit_abs), dist_entry_lvl_bp=(lf - ef) / la * 1e4, pfar_bp=(pfar - lf) / la * 1e4, ptop_bp=(ptop - lf) / la * 1e4,
             n_members=len(piv), level_px=r["level_px_e8"] / 1e8, entry_px=r["entry_px_e8"] / 1e8)
    if len(th) == 0:
        d.update(mfe_pct=0.0, mae_pct=0.0, crossed=False)
        return d
    ea = abs(ef)
    mfe = max(0.0, (ph.max() - ef) / ea)
    mae = max(0.0, (ef - ph.min()) / ea)
    d.update(mfe_pct=mfe * 100, mae_pct=mae * 100, mfe_natr=mfe / natr, mae_natr=mae / natr,
             t_mfe_min=(th[int(np.argmax(ph))] - entry_abs) / 60000, t_mae_min=(th[int(np.argmin(ph))] - entry_abs) / 60000,
             height_bp=(ph.max() - lf) / la * 1e4, mae_below_lvl_bp=(lf - ph.min()) / la * 1e4)
    cr = np.nonzero(ph >= lf)[0]
    d["crossed"] = bool(len(cr))
    d["crossed_top"] = bool((ph > ptop).any())
    d["approach_bp"] = (lf - ph.max()) / la * 1e4  # > 0: ближайший подход, если не пересекли
    # тейки и «открытое» брутто по ленте
    legs = r["legs"]
    takes = [(L[0] + T0, L[1] / 1e8, L[2]) for L in legs if L[3]]
    xpx = legs[-1][1] / 1e8
    epx = r["entry_px_e8"] / 1e8
    tt = np.array([x[0] for x in takes], dtype=np.int64)
    tg = np.cumsum([x[2] / 1e6 * s * (x[1] - epx) / epx for x in takes]) if takes else np.zeros(0)
    tu = np.cumsum([x[2] for x in takes]) if takes else np.zeros(0)
    d["first_take_s"] = (tt[0] - entry_abs) / 1000 if len(tt) else np.nan
    pr = ph * s / 1e8  # настоящая цена
    k = np.searchsorted(tt, th, side="right")  # число исполненных тейков к тику
    if len(tg):
        real_g = np.where(k > 0, tg[np.clip(k - 1, 0, None)], 0.0)
        rem = (500_000_000 - np.where(k > 0, tu[np.clip(k - 1, 0, None)], 0)) / 1e6
    else:
        real_g = np.zeros(len(th))
        rem = np.full(len(th), 500.0)
    open_g = real_g + rem * s * (pr - epx) / epx
    peak = max(0.0, float(open_g.max()))
    d.update(peak_gross=peak, given_back=peak - r["gross"])
    # возврат после пересечения
    if len(cr):
        c = int(cr[0])
        d["cross_s"] = (th[c] - entry_abs) / 1000
        after = ph[c:]
        ta = th[c:]
        d["ret_depth_bp"] = max(0.0, float(((lf - after) / la).max() * 1e4))
        below = after < lf
        dt = np.diff(np.append(ta, exit_abs)) / 1000.0
        d["time_below_lvl_s"] = float(dt[below].sum())
        d["height_after_cross_bp"] = (after.max() - lf) / la * 1e4
        for f in F_BP:
            idx = np.nonzero(after <= lf - f * la / 1e4)[0]
            d[f"ret{f}_s"] = (ta[idx[0]] - ta[0]) / 1000 if len(idx) else np.nan
            if len(idx):
                j = c + int(idx[0])
                d[f"pnl_at_ret{f}"] = float(open_g[j])
                d[f"after_ret{f}"] = r["gross"] - float(open_g[j])
        d["cross_to_exit_s"] = (exit_abs - th[c]) / 1000
    # ход после выхода
    post = (t > exit_abs) & (t <= exit_abs + POST_MS)
    if post.any():
        xf = s * legs[-1][1]
        pp = ps[post]
        d["post_mfe_natr"] = max(0.0, float((pp.max() - xf) / abs(xf) / natr))
        d["post_mae_natr"] = max(0.0, float((xf - pp.min()) / abs(xf) / natr))
        # первое достижение +X / −X NATR от цены выхода за 60 мин: симметричный контроль «выбит до хода»
        for xn in (1.0, 1.5, 3.0):
            up = np.nonzero(pp >= xf + xn * natr * abs(xf))[0]
            dn = np.nonzero(pp <= xf - xn * natr * abs(xf))[0]
            iu, idn = (int(up[0]) if len(up) else 10**9), (int(dn[0]) if len(dn) else 10**9)
            d[f"post_first{xn}"] = "up" if iu < idn else ("down" if idn < iu else "none")
    return d


def s_sum(rows, key="net"):
    v = [x[key] for x in rows]
    return len(v), float(sum(v)), (float(sum(v)) / len(v) if v else 0.0)


def fmt_row(name, rows):
    n, sm, mn = s_sum(rows)
    pos = sum(1 for x in rows if x["net"] > 0)
    return f"{name:<34} n={n:>4}  сумма {sm:>9.2f} $  среднее {mn:>7.3f} $  в плюсе {100 * pos / n if n else 0:>5.1f} %"


def pct(v, q):
    return float(np.percentile(v, q)) if len(v) else float("nan")


def loser_class(x):
    if x["kind"] == "stop":
        return "К1 стоп, уровень не пересекали" if not x["crossed"] else "К2 стоп, пересекали и вернулись"
    if x["kind"] == "be":
        return "К3 безубыток, итог < 0"
    if x["kind"] == "trail":
        return "К4 трейл, итог < 0"
    return "К5 принудительное закрытие"


def summarize(rows, out):
    W = out.append
    for side in ("long", "short", "both"):
        R = [x for x in rows if side == "both" or x["side"] == side]
        W(f"\n===== {side} =====")
        W(fmt_row("все сделки", R))
        for h in ("A", "B"):
            W(fmt_row(f"  половина {h}", [x for x in R if x["half"] == h]))
        W("-- по виду выхода (все / плюс / минус)")
        for k in ("trail", "be", "stop", "forced"):
            K = [x for x in R if x["kind"] == k]
            if K:
                W(fmt_row(f"  {k}", K))
                W(fmt_row(f"    итог > 0", [x for x in K if x["net"] > 0]) if any(x["net"] > 0 for x in K) else f"    итог > 0: нет")
                W(fmt_row(f"    итог < 0", [x for x in K if x["net"] <= 0]) if any(x["net"] <= 0 for x in K) else f"    итог < 0: нет")
        cr = [x for x in R if x.get("crossed")]
        W(f"-- пересекли уровень: {len(cr)} из {len(R)} ({100 * len(cr) / max(1, len(R)):.1f} %); сумма {sum(x['net'] for x in cr):.2f} $ против не пересекших {sum(x['net'] for x in R if not x.get('crossed')):.2f} $ (n={len(R) - len(cr)})")
        if cr:
            cs = [x["cross_s"] for x in cr]
            W(f"   время от входа до пересечения, с: среднее {np.mean(cs):.1f}, p75 {pct(cs, 75):.1f}, p90 {pct(cs, 90):.1f}, макс {max(cs):.1f}")
        W(f"   ушли за верхний край зоны: {sum(1 for x in R if x.get('crossed_top'))}")
        # плюсовые
        P = [x for x in R if x["net"] > 0]
        if P:
            W(f"\n-- ПЛЮСОВЫЕ n={len(P)}  сумма {sum(x['net'] for x in P):.2f} $  среднее {np.mean([x['net'] for x in P]):.3f} $")
            rew = [x["gross"] / NOTIONAL / (x["natr_pct"] / 100) for x in P]
            mae_n = [x.get("mae_natr", 0) for x in P]
            risk_usd = [NOTIONAL * x["mae_pct"] / 100 for x in P]
            W(f"   награда (брутто/$500) в NATR: среднее {np.mean(rew):.2f}, p75 {pct(rew, 75):.2f}, p90 {pct(rew, 90):.2f}; принятый риск MAE в NATR: среднее {np.mean(mae_n):.2f}, p75 {pct(mae_n, 75):.2f}, p90 {pct(mae_n, 90):.2f}, p95 {pct(mae_n, 95):.2f}, макс {max(mae_n):.2f}")
            W(f"   MAE %: среднее {np.mean([x['mae_pct'] for x in P]):.3f}, p75 {pct([x['mae_pct'] for x in P], 75):.3f}, p90 {pct([x['mae_pct'] for x in P], 90):.3f}, p95 {pct([x['mae_pct'] for x in P], 95):.3f}, макс {max(x['mae_pct'] for x in P):.3f}")
            W(f"   награда/риск: сумма итогов {sum(x['net'] for x in P):.2f} $ / сумма принятого риска (MAE на $500) {sum(risk_usd):.2f} $ = {sum(x['net'] for x in P) / max(1e-9, sum(risk_usd)):.2f}; против номинального стопа 5 % ($25 × n = {25 * len(P):.0f} $): {sum(x['net'] for x in P) / (25 * len(P)):.3f}")
            W("   доля плюсовых с MAE ≤ …  % : " + "  ".join(f"{v}%: {100 * sum(1 for x in P if x['mae_pct'] <= v) / len(P):.1f}" for v in MAE_PCT))
            W("   доля плюсовых с MAE ≤ …  NATR: " + "  ".join(f"{v}: {100 * sum(1 for x in P if x.get('mae_natr', 0) <= v) / len(P):.1f}" for v in MAE_NATR))
            W(f"   MAE ниже уровня, бп: среднее {np.mean([x.get('mae_below_lvl_bp', 0) for x in P]):.1f}, p90 {pct([x.get('mae_below_lvl_bp', 0) for x in P], 90):.1f}, макс {max(x.get('mae_below_lvl_bp', 0) for x in P):.1f}")
            W(f"   отдано до выхода (пик открытого брутто − итог): сумма {sum(x.get('given_back', 0) for x in P):.2f} $, среднее {np.mean([x.get('given_back', 0) for x in P]):.3f} $")
            W("   плюсовые по полосам MAE (% от входа): " + "; ".join(
                f"{lo}–{hi}%: n={sum(1 for x in P if lo < x['mae_pct'] <= hi)} сумма {sum(x['net'] for x in P if lo < x['mae_pct'] <= hi):.1f} $"
                for lo, hi in ((-1, 0.3), (0.3, 0.5), (0.5, 1), (1, 2), (2, 5))))
            W("   плюсовые по полосам MAE ниже уровня (бп): " + "; ".join(
                f"{lo}–{hi}: n={sum(1 for x in P if lo < x.get('mae_below_lvl_bp', 0) <= hi)} сумма {sum(x['net'] for x in P if lo < x.get('mae_below_lvl_bp', 0) <= hi):.1f} $"
                for lo, hi in ((-1e9, 0), (0, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, 1e9))))
            for k in ("trail", "be", "stop", "forced"):
                K = [x for x in P if x["kind"] == k]
                if K:
                    W(f"   [{k}] n={len(K)} сумма {sum(x['net'] for x in K):.2f} $ MAE NATR среднее {np.mean([x.get('mae_natr', 0) for x in K]):.2f} MFE NATR среднее {np.mean([x.get('mfe_natr', 0) for x in K]):.2f}")
        # минусовые
        N = [x for x in R if x["net"] <= 0]
        if N:
            tot = sum(x["net"] for x in N)
            W(f"\n-- МИНУСОВЫЕ n={len(N)}  сумма {tot:.2f} $  среднее {tot / len(N):.3f} $")
            cls = defaultdict(list)
            for x in N:
                cls[loser_class(x)].append(x)
            for c in sorted(cls):
                C = cls[c]
                sm = sum(x["net"] for x in C)
                W(f"  {c}: n={len(C)}  сумма {sm:.2f} $ ({100 * sm / tot:.1f} % убытка)  среднее {sm / len(C):.3f} $  удержание среднее {np.mean([x['hold_s'] for x in C]) / 60:.1f} мин  MFE среднее {np.mean([x.get('mfe_pct', 0) for x in C]):.3f} % ({np.mean([x.get('mfe_natr', 0) for x in C]):.2f} NATR)  MAE среднее {np.mean([x.get('mae_pct', 0) for x in C]):.3f} %")
                if c.startswith("К1"):
                    ap = [x["approach_bp"] for x in C]
                    W(f"      ближайший подход к уровню, бп: среднее {np.mean(ap):.1f}, p25 {pct(ap, 25):.1f}, p75 {pct(ap, 75):.1f}, макс {max(ap):.1f}; MFE ≥ 1 NATR: {sum(1 for x in C if x.get('mfe_natr', 0) >= 1)}; вход ≤ 10 бп от уровня: {sum(1 for x in C if x['dist_entry_lvl_bp'] <= 10)}")
                if c.startswith("К2"):
                    W(f"      высота над уровнем, бп: среднее {np.mean([x.get('height_after_cross_bp', 0) for x in C]):.1f}, макс {max(x.get('height_after_cross_bp', 0) for x in C):.1f}; от пересечения до выхода, мин: среднее {np.mean([x.get('cross_to_exit_s', 0) for x in C]) / 60:.1f}")
                    for f in (10, 20):
                        v = [x[f"after_ret{f}"] for x in C if f"after_ret{f}" in x]
                        if v:
                            W(f"      после первого возврата глубже {f} бп (n={len(v)}): отдано {sum(v):.2f} $ (среднее {np.mean(v):.2f} $, «найдено на этих данных», гипотетически, не правило)")
                if c.startswith("К3"):
                    kn = [x for x in C if x.get("post_mfe_natr", 0) >= 1.0]
                    W(f"      «выбит до хода» по §13.19 (за 60 мин после выхода ≥ 1 NATR в сторону сделки): {len(kn)} из {len(C)}, сумма {sum(x['net'] for x in kn):.2f} $")
                    for xn in (1.0, 2.0, 3.0):
                        up = sum(1 for x in C if x.get("post_mfe_natr", 0) >= xn)
                        dn = sum(1 for x in C if x.get("post_mae_natr", 0) >= xn)
                        W(f"      контроль симметрии: за 60 мин после выхода ≥ {xn:g} NATR в сторону сделки — {up}, против стороны сделки — {dn} (из {len(C)})")
                    for xn in (1.0, 1.5, 3.0):
                        fu = sum(1 for x in C if x.get(f"post_first{xn}") == "up")
                        fd = sum(1 for x in C if x.get(f"post_first{xn}") == "down")
                        W(f"      первым достигнуто ±{xn:g} NATR от цены выхода (60 мин): в сторону сделки {fu}, против {fd}, ни то ни другое {len(C) - fu - fd}")
            # лонг/шорт отдельно по классам печатаются через side-циклы
        # возврат по всем сделкам
        if cr:
            W("-- возврат после пересечения (все сделки, пересекшие уровень): " + "; ".join(f"глубже {f} бп: {sum(1 for x in cr if not np.isnan(x.get(f'ret{f}_s', np.nan)))} из {len(cr)}" for f in F_BP))
            tb = [x["time_below_lvl_s"] for x in cr]
            W(f"   время под уровнем после пересечения, с: среднее {np.mean(tb):.1f}, p90 {pct(tb, 90):.1f}; глубина возврата, бп: среднее {np.mean([x['ret_depth_bp'] for x in cr]):.1f}, p90 {pct([x['ret_depth_bp'] for x in cr], 90):.1f}, макс {max(x['ret_depth_bp'] for x in cr):.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir")
    ap.add_argument("outdir")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    recs = []
    for side in ("long", "short"):
        for ln in open(os.path.join(a.indir, f"{side}-detail.jsonl"), encoding="utf-8"):
            recs.append(json.loads(ln))
    if a.limit:
        recs = recs[: a.limit]
    print(f"сделок {len(recs)}", flush=True)
    rows = [None] * len(recs)
    with ThreadPoolExecutor(a.workers) as ex:
        for k, d in enumerate(ex.map(analyse, recs)):
            rows[k] = d
            if (k + 1) % 50 == 0:
                print(f"{k + 1}/{len(recs)}", flush=True)
    cnt = defaultdict(int)
    for x in rows:
        pre = "L" if x["side"] == "long" else "S"
        cnt[pre] += 1
        x["id"] = f"{pre}{x['i'] + 1:04d}_{x['sym']}"
    keys = []
    for x in rows:
        for k in x:
            if k not in keys:
                keys.append(k)
    with open(os.path.join(a.outdir, "paths.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for x in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in x.items()})
    out = []
    bad = sum(1 for x in rows if not x["exit_tick_ok"] and x["kind"] != "forced")
    out.append(f"сделок {len(rows)}; выход не совпал с последней сделкой окна (не forced): {bad}")
    summarize(rows, out)
    txt = "\n".join(out)
    open(os.path.join(a.outdir, "summary.txt"), "w", encoding="utf-8").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
