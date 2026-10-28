#!/usr/bin/env python3
"""TK-438 шаг 4: сценарии S1..S11 по правилам (заявка в логе TK-438, реестр H-006..H-015) на событиях/контролях v11 (выборка 1200 событий на D).
Доли сценария событий vs контролей (бутстреп по монетам) и ход после пика +1/6/24 ч. Использует функции tk426-classes.py (без его main).
Использование: tk438-scen.py <features-09.csv> <features-08.csv> <xq.json> <свечи-09> <свечи-08> <выход-префикс>"""
import sys, os
F9, F8, XQ_, C9_, C8_, OUTP = sys.argv[1:7]
sys.argv = ["x", "-", "-", F9, F8, XQ_, C9_, C8_, OUTP]
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk426-classes.py"), encoding="utf-8").read()
src = src.rsplit("\nmain()", 1)[0]
exec(compile(src, "tk426-classes.py", "exec"))
B = int(os.environ.get("NB", 400)); MODE = os.environ.get("MODE", "both"); H = {"1ч": 60, "6ч": 360, "24ч": 1440}
rng = np.random.default_rng(438)


def after(df, root):
    out = {k: [] for k in H}
    for b, m, i, s, t1 in zip(df.биржа, df.рынок, df.инструмент, df.зн, df.t_peak):
        c = candles(root, b, m, i); sg = 1 if s == "+" else -1
        r = {k: np.nan for k in H}
        if c is not None:
            t, o, cl, q = c; p = int(t1 // MIN)
            j = np.searchsorted(t, p, side="right") - 1
            if j >= 0 and t[j] >= p - 2:
                for k, h in H.items():
                    if t[-1] >= p + h:
                        j2 = np.searchsorted(t, p + h, side="right") - 1
                        r[k] = sg * np.log(cl[j2] / cl[j]) * 1e4
        for k in H: out[k].append(r[k])
    return pd.DataFrame({"пик+" + k: v for k, v in out.items()})


def flags(d, c):
    """пороги — по контролям ячейки D×знак (§4, §7 рамки); NaN-признак -> флаг NaN (нет данных)"""
    def q(col, p, sub=None):
        x = c[col] if sub is None else c.loc[sub, col]
        x = x.dropna(); return x.quantile(p) if len(x) >= 20 else np.nan
    sg = d.зн.map({"+": 1, "-": -1})
    f = pd.DataFrame(index=d.index)
    def fl(cond, have): return pd.Series(np.where(have, cond, np.nan), index=d.index)
    f["S1_спот_ведёт"] = fl(d.lead_in > 0, d.lead_in.notna())
    f["S2_фьюч_ведёт"] = fl(d.lead_in < 0, d.lead_in.notna())
    f["S3_OI_растёт"] = fl(d.oi_in > q("oi_in", .95), d.oi_in.notna())
    f["S4_OI_падает"] = fl(d.oi_in < q("oi_in", .05), d.oi_in.notna())
    f["S5_фандинг_экстр"] = fl(d.fund_in.abs() > c.fund_in.abs().dropna().quantile(.95) if c.fund_in.notna().sum() >= 20 else False, d.fund_in.notna())
    f["S6_каскад_ликв"] = fl(d.liq_in > q("liq_in", .95), d.liq_in.notna())
    ab = c.btc_in.abs().dropna(); tb = ab.quantile(.95) if len(ab) >= 20 else np.nan
    f["S7_по_BTC"] = fl((d.btc_in.abs() > tb) & (sg * d.btc_in > 0), d.btc_in.notna())
    f["S7б_против_BTC"] = fl((d.btc_in.abs() > tb) & (sg * d.btc_in < 0), d.btc_in.notna())
    f["S8_листинг<7"] = fl(d.hist_days < 7, d.hist_days.notna())
    gate = d.биржа == "gate"; cg = c[c.биржа == "gate"]
    qg = cg["доля1м"].dropna().quantile(.95) if cg["доля1м"].notna().sum() >= 20 else np.nan
    f["S9_тонкий_Gate"] = pd.Series(np.where(gate, np.where(d["доля1м"].notna(), d["доля1м"] > qg, np.nan), np.nan), index=d.index)
    f["S10_накопление"] = fl((d.v_pre > q("v_pre", .95)) & (d.m_pre > q("m_pre", .95)), d.v_pre.notna() & d.m_pre.notna())
    f["S11_вынос_возврат"] = fl(d["откат"] >= 1, d["откат"].notna())
    return f


def boot_share(e, c, col):
    """доля флага у событий vs контролей, бутстреп по монетам"""
    ee = e[[col, "монета"]].dropna(); cc = c[[col, "монета"]].dropna()
    if len(ee) < 10 or len(cc) < 10: return None
    coins = np.array(sorted(set(ee.монета) | set(cc.монета)))
    ge = ee.groupby("монета")[col].agg(["sum", "count"]).reindex(coins).fillna(0).to_numpy()
    gc = cc.groupby("монета")[col].agg(["sum", "count"]).reindex(coins).fillna(0).to_numpy()
    se, sc = ee[col].mean(), cc[col].mean(); d0 = se - sc
    ix = rng.integers(0, len(coins), (B, len(coins)))
    de = ge[ix]; dc = gc[ix]
    ne = de[..., 1].sum(1); nc = dc[..., 1].sum(1)
    ok = (ne > 0) & (nc > 0)
    bd = (de[..., 0].sum(1)[ok] / ne[ok]) - (dc[..., 0].sum(1)[ok] / nc[ok])
    lo, hi = np.percentile(bd, [2.5, 97.5])
    p = 2 * min((bd <= 0).mean(), (bd >= 0).mean())
    return dict(n_e=len(ee), k_e=int(ee[col].sum()), n_c=len(cc), k_c=int(cc[col].sum()), доля_соб=se, доля_контр=sc, разность=d0, lo=lo, hi=hi, p=max(p, 1 / B), монет=len(coins))


def boot_med(e, c, col, mask_e, mask_c):
    ee = e.loc[mask_e, [col, "монета"]].dropna(); cc = c.loc[mask_c, [col, "монета"]].dropna()
    if len(ee) < 10 or len(cc) < 10: return None
    coins = np.array(sorted(set(ee.монета) | set(cc.монета)))
    pe = {k: v.to_numpy() for k, v in ee.groupby("монета")[col]}; pc = {k: v.to_numpy() for k, v in cc.groupby("монета")[col]}
    d0 = ee[col].median() - cc[col].median(); bd = []
    for _ in range(B):
        pick = coins[rng.integers(0, len(coins), len(coins))]
        xe = [pe[k] for k in pick if k in pe]; xc = [pc[k] for k in pick if k in pc]
        if xe and xc: bd.append(np.median(np.concatenate(xe)) - np.median(np.concatenate(xc)))
    bd = np.array(bd); lo, hi = np.percentile(bd, [2.5, 97.5]); p = 2 * min((bd <= 0).mean(), (bd >= 0).mean())
    return dict(n_e=len(ee), n_c=len(cc), мед_соб=ee[col].median(), мед_контр=cc[col].median(), разность=d0, lo=lo, hi=hi, p=max(p, 1 / B), монет=len(coins))


rows_s, rows_e = [], []
for mon, F, root in (("09", F9, C9_), ("08", F8, C8_)):
    f = pd.read_csv(F, low_memory=False)
    f["зн"] = np.where(pd.to_numeric(f.знак, errors="coerce") > 0, "+", "-")
    f["монета"] = norm_coin(f.инструмент); f["bx"] = f.биржа + "-" + f.рынок; f["D"] = f["θ"]
    e, c = prep(f, root)
    for d in (e, c):
        d.reset_index(drop=True, inplace=True)
        d["D"] = d.D.astype(int)
        for k, v in after(d, root).items(): d[k] = v.to_numpy()
    gb = (c.биржа == "gate") & (c.t_start <= GATE_BAD[1]) & (c.t_peak >= GATE_BAD[0])
    c = c[~gb].reset_index(drop=True)
    print(mon, "соб.", len(e), "контр.", len(c), flush=True)
    for D in sorted(e.D.unique()):
        for zn in ("+", "-"):
            ee = e[(e.D == D) & (e.зн == zn)].reset_index(drop=True); cc = c[(c.D == D) & (c.зн == zn)].reset_index(drop=True)
            if len(ee) < 10 or len(cc) < 10: continue
            fe, fc = flags(ee, cc), flags(cc, cc)
            for s in fe.columns:
                ee[s] = fe[s]; cc[s] = fc[s]
                r = boot_share(ee, cc, s)
                if r: rows_s.append(dict(месяц=mon, D=D, знак=zn, сценарий=s, **r))
                for h in (H if MODE != "shares" else ()):
                    col = "пик+" + h
                    r = boot_med(ee, cc, col, ee[s] == 1, cc[s] == 1)
                    if r: rows_e.append(dict(месяц=mon, D=D, знак=zn, сценарий=s, горизонт=h, **r))
            for h in (H if MODE != "shares" else ()):  # база: все события / все контроли
                r = boot_med(ee, cc, "пик+" + h, ee.index == ee.index, cc.index == cc.index)
                if r: rows_e.append(dict(месяц=mon, D=D, знак=zn, сценарий="все", горизонт=h, **r))
        print(mon, "D", D, "готово", flush=True)
if MODE != "end": pd.DataFrame(rows_s).to_csv(OUTP + "-shares.csv", index=False)
if MODE != "shares": pd.DataFrame(rows_e).to_csv(OUTP + "-end.csv", index=False)
print("OK")
