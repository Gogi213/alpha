#!/usr/bin/env python3
"""П-42 §13.21 (TK-595): стадия меток — 0 испытаний, 0 сделок, 0 P&L. Только половина A таблицы эпизодов `ep_u0` TK-594.

  python3 retitr_labels.py columns <выход.txt> [--flow feat_flow.parquet] [--deriv feat_deriv.parquet] [--book feat_book.parquet]
      строит файл колонок-кандидатов (группа, колонка, источник) из схем таблиц — его коммитят ДО чтения z
  python3 retitr_labels.py run <columns.txt> <выход.csv> [--flow …] [--deriv …] [--book …] [--ep /data/tk594/ep_u0.parquet] [--nboot 2000] [--seed 595]
      z по стороне × признак × направление; топ-5 на сторону (лучший признак группы, z > 0) — в <выход>.top5.csv
  python3 retitr_labels.py pairs <columns.txt> <выход.csv> …
      описательно: 2×2 (медианы границы A) OI-признаки × {mv5, tfv300, fund_last}: n, mean d, SE по квадрантам (в деньги не идёт)
  python3 retitr_labels.py selftest

Метка: d = y − c_f, строки с c_n ≥ 5 и c_f не NaN (как tk592_lib.prepare), половина A (дни 14…43). Метрика: по стороне и направлению
отбросить нижний (`lo`: x < q25) или верхний (`hi`: x > q75) хвост 25 % по границе A той же стороны; пропуск (NaN) = «проходит»;
z = (mean d(оставшихся) − mean d(всех)) / SE, SE — бутстреп по монетам (nboot, зерно seed). Признаки с пропусками > 25 % исключаются;
если q25 (q75) совпадает с минимумом (максимумом) и хвост пуст — «вырожден», пропускается. Всё — «найдено на этих данных» (отбор по A).
Соединение дополнительных таблиц — по `ep_id` (число строк после соединения обязано совпасть с числом строк A).
"""
import re
import sys

import numpy as np
import pandas as pd

EP = "/data/tk594/ep_u0.parquet"
# колонки `ep_u0`, исключённые из кандидатов: ключи, исходы, метки, пул (стадия P), календарь, служебные
EXCL = set("""univ side sym level ep_no ep_id sig_ms day half n_sig_ep last_sig_ms lp_e8 natr_e8 dist_e8 hour4 wday tick_ok
lpx_e8 indep c_n c_f c_s y y_s in_pk in_ow in_pool natr_bp turn1h turn24h""".split())
EXCL_PREFIX = ("ok_", "o_", "ox_", "tw_", "opp_", "reach_", "mae_", "ctrl_", "ep_traded_", "first_traded_", "tc_", "tx_", "tr_", "mfe")
GROUPS = [  # (регулярка по имени, группа): лучший признак группы идёт в топ-5
    (r"^(tfv\d+|tfn\d+|tfp300)$", "flow_tf"), (r"^oi_c", "oi_c"), (r"^fund_", "fund"), (r"^d?ls_", "ls"),
    (r"^bd_imb", "bd_imb"), (r"^bd_thin", "bd_thin"), (r"^cons(60|120)$", "cons"), (r"^c5_", "c5"),
]
MAX_NA = 0.25


def group_of(name):
    for rx, g in GROUPS:
        if re.match(rx, name):
            return g
    return name


def ep_feature_cols(df):
    cols = []
    for c in df.columns:
        if c in EXCL or c.startswith(EXCL_PREFIX):
            continue
        if pd.api.types.is_numeric_dtype(df[c]) or df[c].dtype == bool:
            cols.append(c)
    return cols


def cmd_columns(out, ep, extra):
    df = pd.read_parquet(ep)
    rows = [(group_of(c), c, "ep_u0") for c in ep_feature_cols(df)]
    seen = {c for _, c, _ in rows}
    for src, path in extra.items():
        t = pd.read_parquet(path)
        # таблицы TK-594 flow и deriv несут колонки друг друга целиком пустыми: берём только колонки, не целиком пустые и не взятые раньше
        for c in t.columns:
            if c != "ep_id" and c not in seen and t[c].notna().any() and (pd.api.types.is_numeric_dtype(t[c]) or t[c].dtype == bool):
                rows.append((group_of(c), c, src))
                seen.add(c)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# П-42 §13.21 стадия меток: колонки-кандидаты (группа<TAB>колонка<TAB>источник); исключены: ключи, исходы, метки, пул (natr_bp, turn1h, turn24h), календарь (hour4, wday)\n")
        for g, c, s in rows:
            f.write("%s\t%s\t%s\n" % (g, c, s))
    print(len(rows), "колонок;", len({g for g, _, _ in rows}), "групп")


def load(ep, extra, cols):
    d = pd.read_parquet(ep)
    d = d[(d["half"] == "A") & (d["c_n"] >= 5) & d["c_f"].notna()].reset_index(drop=True)
    d["d"] = d["y"].astype(float) - d["c_f"].astype(float)
    n0 = len(d)
    for src, path in extra.items():
        t = pd.read_parquet(path)
        need = [c for _, c, s in cols if s == src]
        d = d.merge(t[["ep_id"] + [c for c in need if c in t.columns]], on="ep_id", how="left", validate="one_to_one")
        assert len(d) == n0, "соединение %s изменило число строк" % src
    return d


def read_cols(path):
    out = []
    for ln in open(path, encoding="utf-8"):
        if ln.startswith("#") or not ln.strip():
            continue
        g, c, s = ln.rstrip("\n").split("\t")
        out.append((g, c, s))
    return out


def boot_weights(ncoin, nboot, seed):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, ncoin, size=(nboot, ncoin))
    w = np.zeros((nboot, ncoin))
    for i in range(nboot):
        w[i] = np.bincount(idx[i], minlength=ncoin)
    return w


def stat_z(dv, coin, mask, w):
    """z и точечная разность для маски «оставшиеся»; бутстреп по монетам: сводные суммы по монете × веса повторов."""
    n_all = np.bincount(coin, minlength=w.shape[1]).astype(float)
    s_all = np.bincount(coin, weights=dv, minlength=w.shape[1])
    n_k = np.bincount(coin[mask], minlength=w.shape[1]).astype(float)
    s_k = np.bincount(coin[mask], weights=dv[mask], minlength=w.shape[1])
    pt = s_k.sum() / n_k.sum() - s_all.sum() / n_all.sum()
    na = w @ n_all
    nk = w @ n_k
    ok = (nk > 0) & (na > 0)
    bs = (w @ s_k)[ok] / nk[ok] - (w @ s_all)[ok] / na[ok]
    se = float(bs.std(ddof=1))
    return pt, se, (pt / se if se > 0 else np.nan), int(mask.sum())


def cmd_run(cols_path, out, ep, extra, nboot, seed):
    cols = read_cols(cols_path)
    d = load(ep, extra, cols)
    res = []
    skipped = []
    for side in ("long", "short"):
        s = d[d["side"] == side].reset_index(drop=True)
        coin = pd.factorize(s["sym"])[0]
        w = boot_weights(coin.max() + 1, nboot, seed)
        dv = s["d"].values
        for g, c, src in cols:
            if c not in s.columns:
                skipped.append((side, c, "нет колонки"))
                continue
            x = s[c].astype(float).values
            na = np.isnan(x)
            if na.mean() > MAX_NA:
                skipped.append((side, c, "пропусков %.0f %%" % (100 * na.mean())))
                continue
            xx = x[~na]
            for direction in ("lo", "hi"):
                if direction == "lo":
                    thr = np.quantile(xx, 0.25)
                    keep = na | (x >= thr)
                    degenerate = (x < thr).sum() == 0
                else:
                    thr = np.quantile(xx, 0.75)
                    keep = na | (x <= thr)
                    degenerate = (x > thr).sum() == 0
                if degenerate:
                    skipped.append((side, c + ":" + direction, "хвост пуст"))
                    continue
                pt, se, z, nk = stat_z(dv, coin, keep, w)
                res.append(dict(side=side, group=g, feature=c, source=src, dir=direction, thr=thr, n_all=len(s), n_kept=nk, dmean=pt, se=se, z=z))
    r = pd.DataFrame(res).sort_values(["side", "z"], ascending=[True, False])
    r.to_csv(out, index=False)
    top = []
    for side in ("long", "short"):
        t = r[(r["side"] == side) & (r["z"] > 0)]
        best = t.sort_values("z", ascending=False).groupby("group", sort=False).head(1)  # лучший признак-направление группы
        top.append(best.head(5))
    pd.concat(top).to_csv(out + ".top5.csv", index=False)
    pd.DataFrame(skipped, columns=["side", "feature", "reason"]).to_csv(out + ".skipped.csv", index=False)
    print("строк", len(r), "исключено", len(skipped))
    for t in top:
        print(t[["side", "group", "feature", "dir", "z"]].to_string(index=False))


def cmd_pairs(cols_path, out, ep, extra):
    cols = read_cols(cols_path)
    d = load(ep, extra, cols)
    oi = [c for g, c, s in cols if g == "oi_c"]
    other = ["mv5", "tfv300", "fund_last"]
    recs = []
    for side in ("long", "short"):
        s = d[d["side"] == side].reset_index(drop=True)
        coin = pd.factorize(s["sym"])[0]
        w = boot_weights(coin.max() + 1, 2000, 595)
        for a in oi:
            for b in other:
                if a not in s.columns or b not in s.columns:
                    continue
                xa, xb = s[a].astype(float).values, s[b].astype(float).values
                ok = ~np.isnan(xa) & ~np.isnan(xb)
                ma, mb = np.median(xa[ok]), np.median(xb[ok])
                for qa, fa in (("lo", xa < ma), ("hi", xa >= ma)):
                    for qb, fb in (("lo", xb < mb), ("hi", xb >= mb)):
                        m = ok & fa & fb
                        if m.sum() < 30:
                            recs.append(dict(side=side, a=a, b=b, qa=qa, qb=qb, n=int(m.sum()), dmean=np.nan, se=np.nan))
                            continue
                        pt, se, z, nk = stat_z(s["d"].values, coin, m, w)
                        recs.append(dict(side=side, a=a, b=b, qa=qa, qb=qb, n=nk, dmean=float(s["d"].values[m].mean()), se=se))
    pd.DataFrame(recs).to_csv(out, index=False)
    print("пар×квадрантов", len(recs))


def selftest():
    rng = np.random.default_rng(1)
    n = 4000
    coin = rng.integers(0, 40, n)
    x = rng.normal(size=n)
    dv = rng.normal(size=n) * 0.3 + 0.2 * (x > 0)  # признак реально разделяет
    w = boot_weights(40, 400, 595)
    thr = np.quantile(x, 0.25)
    pt, se, z, nk = stat_z(dv, coin, x >= thr, w)
    assert z > 3 and nk == (x >= thr).sum(), (z, nk)
    # NaN проходит: x_nan = все NaN → оставшиеся = все → разность 0
    pt0, se0, z0, nk0 = stat_z(dv, coin, np.ones(n, bool), w)
    assert abs(pt0) < 1e-12 and nk0 == n
    # детерминизм: те же зерно и веса → тот же SE
    assert stat_z(dv, coin, x >= thr, boot_weights(40, 400, 595))[1] == se
    assert group_of("tfv300") == "flow_tf" and group_of("oi_c5") == "oi_c" and group_of("mv5") == "mv5"
    print("selftest OK")


if __name__ == "__main__":
    c = sys.argv[1]
    a = sys.argv
    kw = {}
    pos = []
    i = 2
    while i < len(a):
        if a[i].startswith("--"):
            kw[a[i][2:]] = a[i + 1]
            i += 2
        else:
            pos.append(a[i])
            i += 1
    extra = {k: kw[k] for k in ("flow", "deriv", "book") if k in kw}
    ep = kw.get("ep", EP)
    if c == "selftest":
        selftest()
    elif c == "columns":
        cmd_columns(pos[0], ep, extra)
    elif c == "run":
        cmd_run(pos[0], pos[1], ep, extra, int(kw.get("nboot", 2000)), int(kw.get("seed", 595)))
    elif c == "pairs":
        cmd_pairs(pos[0], pos[1], ep, extra)
