#!/usr/bin/env python3
"""TK-493, П-39 §9: (1) нуль на направленный навык: каждой сделке по ключу (тег, пара, t_sig) случайный знак, общий для всех вариантов (сохраняет их зависимость), net' = знак·(gross + фандинг) − комиссия;
печатаются распределение максимума нижней границы блочного t90 y_d по вариантам и доля перестановок, где хоть один вариант проходит условия (а)–(г); (2) устойчивость отбора: 1000 блочных бутстрепов по суткам
(блок 4), доля бутстрепов, где вариант проходит условия (б), (в), (г).
Урезка (честно): в обоих расчётах условие (г) проверяется по трём лучшим СУТКАМ, но не по трём лучшим АКТИВАМ (для этого нужны суммы по активам на каждую перестановку) - доля прохода в нуле и в бутстрепе поэтому
верхняя оценка при полном правиле §12.1. Условие (а) (число сделок, активы, сутки) от знака не зависит и берётся из исходных сделок. Строки D-*-d0 в максимум не входят (§4.1).
Сверка кода: при знаках «+1» нижние границы векторного расчёта равны столбцу yd_lo_t таблицы (проверка в начале, допуск 1e-9).
"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402
import conv_explore as EX       # noqa: E402
import explore_stats as ES      # noqa: E402

P = ES.P
TF = ES.TF
ND_SIG = EX.D_LAST - EX.P0_DEFAULT + 1             # 34
D0, P0, H1_LAST = EX.D0, EX.P0_DEFAULT, EX.H1_LAST


class V:
    """Предрасчитанные массивы варианта для перестановок."""

    def __init__(self, vd, kidx):
        R = vd.rows
        self.id = vd.id
        nd = EX.D_LAST - D0 + 1
        self.n = len(R)
        day = np.where(R[:, TF["mtm"]] == 1, nd - 1, (R[:, TF["f_out"]] // EX.MIN_DAY).astype(int)) if self.n else np.zeros(0, int)
        order = np.argsort(day, kind="stable")
        self.day = day[order]
        self.gf = (R[:, TF["gross"]] + R[:, TF["fund"]])[order] if self.n else np.zeros(0)
        self.fee = R[:, TF["fee"]][order] if self.n else np.zeros(0)
        self.k = kidx[order] if self.n else np.zeros(0, int)
        self.mtm = (R[:, TF["mtm"]] == 1)[order] if self.n else np.zeros(0, bool)
        den = vd.act.sum(axis=0).astype(float)
        n_open = int(self.mtm.sum())
        den_eff = den.copy()
        if n_open and den_eff[nd - 1] == 0:
            den_eff[nd - 1] = n_open
        self.used = np.array([d for d in range(nd) if D0 + d >= P0 and den_eff[d] > 0], int)
        self.den = den_eff[self.used]
        self.starts = np.flatnonzero(np.r_[True, self.day[1:] != self.day[:-1]]) if self.n else np.zeros(0, int)
        self.days_present = self.day[self.starts] if self.n else np.zeros(0, int)
        self.h1 = (self.day + D0 <= H1_LAST)
        self.nd = nd
        sc = ES.make_series(vd.rows, vd.act)
        self.series_ref = sc["primary"]
        keys = vd.keys()
        self.assets = len(set(keys.tolist()))
        dwt = len({int(x) for x in self.day[~self.mtm]}) if self.n else 0
        n1, n2 = int(self.h1.sum()), int((~self.h1).sum())
        self.cond_a = (self.n >= 49 and n1 >= 19 and n2 >= 30 and self.assets >= 5 and dwt >= 12)
        self.eligible = not vd.id.endswith(("-d0",))


def block_lo_matrix(y, alpha=0.10):
    """Нижняя граница блочного t по строкам y (B x n): блоки подряд по ⌈n^(1/3)⌉, как portfolio.block_t_interval."""
    B, n = y.shape
    b = P.block_len(n)
    nb = math.ceil(n / b)
    if nb < 3:
        return np.full(B, np.nan)
    mean = y.mean(axis=1)
    ss = np.zeros(B)
    for k in range(nb):
        seg = y[:, k * b:(k + 1) * b]
        r = seg.sum(axis=1) - mean * seg.shape[1]
        ss += r * r
    se = np.sqrt(nb / (nb - 1) * ss / (n * n))
    q = P._t_ppf_cached(1.0 - alpha / 2.0, nb - 1)
    return mean - q * se


def eval_signs(v, S):
    """S: (Bc x K) знаки; -> (lo, cond_b, cond_g) по перестановкам. Для знака +1 это исходные сделки."""
    vals = S[:, v.k].astype(np.float64) * v.gf - v.fee if v.n else np.zeros((S.shape[0], 0))
    Bc = S.shape[0]
    if v.n:
        ds = np.add.reduceat(vals, v.starts, axis=1)
        num = np.zeros((Bc, v.nd))
        num[:, v.days_present] = ds
    else:
        num = np.zeros((Bc, v.nd))
    y = num[:, v.used] / v.den
    lo = block_lo_matrix(y)
    tot = vals.sum(axis=1) / max(v.n, 1)
    s1 = vals[:, v.h1].sum(axis=1) / max(int(v.h1.sum()), 1)
    s2 = vals[:, ~v.h1].sum(axis=1) / max(int((~v.h1).sum()), 1)
    cb = (tot > 0) & (s1 > 0) & (s2 > 0)
    srt = np.sort(y, axis=1)
    cg = srt[:, :-3].mean(axis=1) > 0 if y.shape[1] > 3 else np.zeros(Bc, bool)
    return lo, cb, cg, y


def cmd_null(a):
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    params = os.path.join(a.code_root, EX.PARAMS_REL)
    variants = {v["id"]: v for v in EX.parse_variants(params)}
    suppl = os.path.join(a.code_root, EX.SUPPL_REL)
    if os.path.isfile(suppl):
        variants.update({v["id"]: v for v in EX.parse_variants(suppl)})
    if a.extra_params:
        variants.update({v["id"]: v for v in EX.parse_variants(a.extra_params)})
    data = ES.load_all(a.out, a.tags.split(","), variants)
    ids = sorted(data)
    keymap, vs = {}, []
    for vid in ids:
        vd = data[vid]
        kidx = np.array([keymap.setdefault((vd.src.split("/")[0], vd.pairs[i][0], int(t)), len(keymap)) for i, t in zip(vd.pidx, vd.rows[:, TF["t_sig"]])], int) if len(vd.rows) else np.zeros(0, int)
        vs.append(V(vd, kidx))
    K = max(len(keymap), 1)
    print("вариантов %d, уникальных ключей сделок %d" % (len(vs), K), flush=True)
    # сверка с таблицей: знаки +1
    import csv
    tab = {}
    p_tab = os.path.join(a.out, "tables", "variants_main.csv")
    if os.path.isfile(p_tab):
        with open(p_tab, encoding="utf-8", newline="") as f:
            f.readline()
            tab = {r["id"]: r for r in csv.DictReader(f)}
    ones = np.ones((1, K), np.int8)
    bad = 0
    for v in vs:
        lo, _, _, _ = eval_signs(v, ones)
        t = tab.get(v.id, {}).get("yd_lo_t", "")
        if t != "" and np.isfinite(lo[0]) and abs(lo[0] - float(t)) > 1e-9:
            bad += 1
            print("СВЕРКА: %s вектор %.12g таблица %s" % (v.id, lo[0], t))
    print("сверка нижних границ с таблицей: расхождений %d из %d" % (bad, len(vs)), flush=True)
    B = a.boot
    rng_seed = 37
    chunk = 50
    maxlo = np.full(B, -np.inf)
    anypass = np.zeros(B, bool)
    pass_cnt = np.zeros(len(vs))
    for c0 in range(0, B, chunk):
        Bc = min(chunk, B - c0)
        rng = np.random.default_rng([rng_seed, c0])
        S = (rng.integers(0, 2, size=(Bc, K), dtype=np.int8) * 2 - 1).astype(np.int8)
        for j, v in enumerate(vs):
            lo, cb, cg, _ = eval_signs(v, S)
            if not v.eligible:
                continue
            maxlo[c0:c0 + Bc] = np.maximum(maxlo[c0:c0 + Bc], np.where(np.isfinite(lo), lo, -np.inf))
            ok = v.cond_a & cb & (lo > 0) & cg
            anypass[c0:c0 + Bc] |= ok
            pass_cnt[j] += ok.sum()
    obs = {v.id: float(eval_signs(v, ones)[0][0]) for v in vs if v.eligible}
    obs_max = max(x for x in obs.values() if np.isfinite(x))
    q = np.quantile(maxlo, [0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    null = dict(B=B, seed=rng_seed, variants=len(vs), eligible=sum(v.eligible for v in vs), unique_trade_keys=K,
                max_lower_bound_quantiles={"5%": q[0], "25%": q[1], "50%": q[2], "75%": q[3], "95%": q[4], "99%": q[5]},
                observed_max_lower_bound=obs_max, observed_argmax=max(obs, key=lambda k: obs[k] if np.isfinite(obs[k]) else -1e9),
                share_permutations_observed_max_not_below=float(np.mean(maxlo >= obs_max)),
                share_permutations_any_variant_passes_a_g=float(anypass.mean()),
                per_variant_pass_share={v.id: float(pass_cnt[j] / B) for j, v in enumerate(vs) if v.eligible},
                note="условие (г) по трём лучшим суткам без трёх лучших активов - верхняя оценка доли прохода; нуль - «нет направленного навыка» (знак), не «net = 0» и не поправка к издержкам")
    XG.write_json(os.path.join(a.out, "tables", "null_directional.json"), null)
    print("нуль: максимум нижней границы по вариантам - медиана %.4f, 95%% %.4f; наблюдённый %.4f; доля перестановок с прохождением (а)-(г): %.3f" % (q[2], q[4], obs_max, anypass.mean()), flush=True)
    # устойчивость отбора
    rng = np.random.default_rng(rng_seed + 1)
    nb_ = 4
    share = {}
    for v in vs:
        if not v.eligible or len(v.used) < 8:
            continue
        nu = len(v.used)
        starts = rng.integers(0, nu, size=(B, math.ceil(nu / nb_)))
        idx = ((starts[:, :, None] + np.arange(nb_)[None, None, :]) % nu).reshape(B, -1)[:, :nu]
        vals_day = np.zeros(v.nd)
        cnt_day = np.zeros(v.nd)
        h1_day = np.array([D0 + d <= H1_LAST for d in range(v.nd)])
        if v.n:
            np.add.at(vals_day, v.day, v.gf * 1.0 - v.fee)
            np.add.at(cnt_day, v.day, 1.0)
        num_u, cnt_u, den_u, h1_u = vals_day[v.used], cnt_day[v.used], v.den, h1_day[v.used]
        y = (num_u / den_u)[idx]
        lo = block_lo_matrix(y)
        sn, sc = num_u[idx], cnt_u[idx]
        h1m = h1_u[idx]
        tot = sn.sum(axis=1) / np.maximum(sc.sum(axis=1), 1)
        s1 = (sn * h1m).sum(axis=1) / np.maximum((sc * h1m).sum(axis=1), 1)
        s2 = (sn * ~h1m).sum(axis=1) / np.maximum((sc * ~h1m).sum(axis=1), 1)
        cb = (tot > 0) & (s1 > 0) & (s2 > 0)
        cg = np.sort(y, axis=1)[:, :-3].mean(axis=1) > 0
        share[v.id] = (float(cb.mean()), float((lo > 0).mean()), float(cg.mean()), float((cb & (lo > 0) & cg).mean()))
    rows = [[k] + list(x) for k, x in sorted(share.items())]
    XG.write_csv(os.path.join(a.out, "tables", "stability.csv"), ["id", "share_b_net_pos_full_and_halves", "share_c_lower_bound_pos", "share_g_wo_top3days_pos", "share_b_c_g"], rows)
    print("устойчивость: строк %d" % len(rows), flush=True)
