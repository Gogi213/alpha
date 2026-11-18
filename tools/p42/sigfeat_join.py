"""П-42 §13.17 (TK-588): склейка таблицы эпизодов — флаги вселенных, сделки, признаки «рынка» (cosig15, peers7, peer_sig30).

Использование: python3 sigfeat_join.py <каталог прогона движка out/eng> <рабочий каталог>
Читает `<work>/episodes_raw.parquet`, `signals_all.parquet`, `trade_feats.parquet`; пишет `episodes.parquet` (+ csv.gz) и `trades_ow_e.parquet`.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import *  # noqa: F401,F403

CACHE = os.environ.get("SIGFEAT_CACHE", "/data/p42-t566/cache")
E16 = "/data/p42-t566/out/e16"
HOLD = os.environ.get("SIGFEAT_MIN_DAY", "0") != "0"  # TK-592: отложенные сутки — без флагов pk/ow и без сделок
PEER_R = 0.6


def keyset(eng, variant):
    ks = set()
    for side in ("long", "short"):
        df = read_sigsx(f"{eng}/{variant}/{side}-sigsx.csv", usecols=["sym", "sig_ms"])
        ks |= {(side, a, int(b)) for a, b in zip(df["sym"], df["sig_ms"])}
    return ks


def trades(src):
    out = []
    for side in ("long", "short"):
        df = pd.read_csv(f"{src}/{side}-trades.csv")
        df["side"] = side
        out.append(df)
    return pd.concat(out, ignore_index=True)


def add_gap(ep: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    """`gap_prev_sig_min` — минут от предыдущего сигнала того же уровня до первого сигнала эпизода (нет предыдущего — NaN); `indep` — «независимый подход»:
    предыдущего сигнала нет или разрыв > 60 мин (набор чувствительности §13.17, дополнение 17.11)."""
    s = sig.sort_values(["univ", "side", "sym", "level", "sig_ms"]).copy()
    s["gap_prev_sig_min"] = s.groupby(["univ", "side", "sym", "level"])["sig_ms"].diff() / MIN_MS
    key = s[s["ep_first"]][["univ", "side", "sym", "level", "ep_no", "gap_prev_sig_min"]]
    n0 = len(ep)
    ep = ep.drop(columns=["gap_prev_sig_min", "indep"], errors="ignore").merge(key, on=["univ", "side", "sym", "level", "ep_no"], how="left")
    assert len(ep) == n0, "gap: дубли ключа эпизода"
    ep["indep"] = ep["gap_prev_sig_min"].isna() | (ep["gap_prev_sig_min"] > 60)
    return ep


def gap_only(work):
    """Дописывает gap_prev_sig_min / indep в готовую таблицу эпизодов (атомарной заменой файла)."""
    ep = pd.read_parquet(f"{work}/episodes.parquet")
    sig = pd.read_parquet(f"{work}/signals_all.parquet")
    ep = add_gap(ep, sig)
    ep.to_parquet(f"{work}/episodes.tmp.parquet")
    os.replace(f"{work}/episodes.tmp.parquet", f"{work}/episodes.parquet")
    ep.to_csv(f"{work}/episodes.csv.gz", index=False)
    print({u: {s: [int(((ep.univ == u) & (ep.side == s)).sum()), int(((ep.univ == u) & (ep.side == s) & ep.indep).sum())] for s in ("long", "short")} for u in ("U1", "U0")})


def main():
    eng, work = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 3 and sys.argv[3] == "--gap-only":
        return gap_only(work)
    ep = pd.read_parquet(f"{work}/episodes_raw.parquet")
    sig = pd.read_parquet(f"{work}/signals_all.parquet")
    tf = pd.read_parquet(f"{work}/trade_feats.parquet")
    keys = lambda d: list(zip(d["side"], d["sym"], d["sig_ms"].astype(int)))
    k_pk, k_ow = (set(), set()) if HOLD else (keyset(eng, "b0pk"), keyset(eng, "ow"))
    u1 = sig[sig.univ == "U1"]
    k_u1 = set(keys(u1))
    ep["key"] = keys(ep)
    ep["in_pk"] = ep["key"].isin(k_pk)
    ep["in_ow"] = ep["key"].isin(k_ow)
    ep["in_pool"] = ep["key"].isin(k_u1)
    # сделки: b0 (наш прогон) и ow_e (e16), сверка с числом
    tb0, tow = (pd.DataFrame(columns=["side", "sym", "sig_ms"]), pd.DataFrame(columns=["side", "sym", "sig_ms"])) if HOLD else (trades(f"{eng}/b0"), trades(E16 + "/ow_e"))
    info = {"trades_b0": {s: int((tb0.side == s).sum()) for s in ("long", "short")},
            "trades_ow_e": {s: int((tow.side == s).sum()) for s in ("long", "short")}}
    sig_u1 = u1.copy()
    sig_u1["key"] = keys(sig_u1)
    km = sig_u1.set_index("key")["ep_no"].to_dict()
    lvl = sig_u1.set_index("key")["level"].to_dict()
    for name, t in (() if HOLD else (("b0", tb0), ("ow_e", tow))):
        t["key"] = list(zip(t["side"], t["sym"], t["sig_ms"].astype(int)))
        t["found"] = t["key"].isin(k_u1)
        info[f"trades_{name}_found_in_u1"] = int(t["found"].sum())
        t["ep_id"] = [f"U1|{k[0]}|{k[1]}|{lvl.get(k, -1)}|{km.get(k, -1)}" for k in t["key"]]
        ep[f"ep_traded_{name}"] = ep["ep_id"].isin(set(t["ep_id"]))
        ep[f"first_traded_{name}"] = ep["key"].isin(set(t["key"]))
    # cosig15 — другие монеты с сигналом U1 той же стороны в (sig − 15 мин, sig); peers — по r5 за 7 предыдущих полных суток
    syms = sorted(f[:-4] for f in os.listdir(CACHE) if f.endswith(".b42"))
    ix = {s: i for i, s in enumerate(syms)}
    R5 = np.empty((len(syms), N_MIN // 5))
    for i, s in enumerate(syms):
        R5[i] = block_returns(load_b42(f"{CACHE}/{s}.b42"), 5)
    sig_by_side = {}
    for side, g in u1.groupby("side"):
        g = g.sort_values("sig_ms")
        sig_by_side[side] = (g["sig_ms"].to_numpy(), g["sym"].map(ix).to_numpy())
    peers_cache = {}

    def peers_for(day):
        if day not in peers_cache:
            a, b = (day - 7) * 288, day * 288
            r, n = corr_matrix(R5[:, a:b], 500)
            pm = (np.nan_to_num(r, nan=0.0) >= PEER_R)
            np.fill_diagonal(pm, False)
            peers_cache[day] = pm
        return peers_cache[day]

    cos, p7, psig = [], [], []
    for row in ep.itertuples():
        ms_arr, sy_arr = sig_by_side[row.side]
        i0 = np.searchsorted(ms_arr, row.sig_ms - 15 * MIN_MS, "right")
        i1 = np.searchsorted(ms_arr, row.sig_ms, "left")
        me = ix[row.sym]
        cos.append(len(set(sy_arr[i0:i1].tolist()) - {me}))
        pm = peers_for(int(row.day))[me]
        p7.append(int(pm.sum()))
        j0 = np.searchsorted(ms_arr, row.sig_ms - 30 * MIN_MS, "right")
        sset = set(sy_arr[j0:i1].tolist()) - {me}
        psig.append(int(sum(pm[x] for x in sset)))
    ep["cosig15"], ep["peers7"], ep["peer_sig30"] = cos, p7, psig
    ep = add_gap(ep.drop(columns=["key"]), sig)
    ep.to_parquet(f"{work}/episodes.parquet")
    ep.to_csv(f"{work}/episodes.csv.gz", index=False)
    # сделки ow_e с признаками разреженности на сигнале сделки
    if not HOLD:
        tow = tow.merge(tf, on=["side", "sym", "sig_ms"], how="left")
        tow.drop(columns=["key"]).to_parquet(f"{work}/trades_ow_e.parquet")
        tb0 = tb0.merge(tf, on=["side", "sym", "sig_ms"], how="left")
        tb0.drop(columns=["key"]).to_parquet(f"{work}/trades_b0.parquet")
    info["episodes"] = {u: int((ep.univ == u).sum()) for u in ("U1", "U0")}
    json.dump(info, open(f"{work}/join_info.json", "w"), ensure_ascii=False)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
