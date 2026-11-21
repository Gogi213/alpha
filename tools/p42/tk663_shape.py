#!/usr/bin/env python3
"""TK-663: почему эквити лонга ровное, а шорта «рваное» — описательный разбор убытков на 775 контрактах (01.07–13.11, 500 $, после комиссий).
Движок не запускается, сделки не пересчитываются (В-213): готовые трейды тех же прогонов, что у tk662_equity.py (S_b775 = S_base, S_an1 = A_n_1, S_mk775 = S_mk1, L_b775 = L_base, L_n24, L_mk2),
склейка окон `tk604_stats.splice`, R:R — `tk620_rr.rr_stats`. Сверка n и Σ с карточкой TK-662 (S_base 319 / +223,14; A_n_1 2 907 / +1 017,69; L_base 428 / +193,17; L_n24 181 / +294,31) — расхождение = остановка.
Сутки — UTC по времени закрытия сделки (кривая «Эквити» рисуется по закрытию); ряд по дню входа — как проверка (так считает TK-662).
Метрики «под водой»: новый максимум = эквити конца суток строго выше прежнего пика (старт — 0); «под водой» = ниже пика; эпизод = подряд идущие сутки под водой. Часы — те же на ряду сделок (по закрытию).
BTC — минутки BTCUSDT (`/data/p42-tk596/{jul,aug}/klines/BTCUSDT`, `/data/klines/binance/BTCUSDT`): ход суток = close последней минуты / close минуты перед 00:00 UTC; ход «за сделку» = как в tk602_shape (знак по стороне, + = в пользу сделки).
Запуск на srv: /opt/venv/bin/python tk663_shape.py <код TK-639 (/data/p42-tk639/code)> <каталог выхода>
Выход: tables.md (все таблицы), summary.json, losses-<линия>.csv (все убыточные сделки), worstdays-<линия>.csv, coins-<линия>.csv. Только описание, без вердикта и без подбора фильтров."""
import csv
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

EPS = 1e-9
MIN_MS = 60_000
DAY_MS = 86_400_000
STOP_KINDS = ("stop", "nt")            # как в tk620_rr
BTC_FLAT = 1.0                          # |ход BTC за сутки| < 1 % — «боковик»
HOUR_BIN = 3
NPERM = 2000
SEED = 663
# имя линии: (ключ tk662, роль, (сделок, Σ) по карточке TK-662, допуск Σ)
LINES = {
    "S_base": ("S_b775", "main", (319, 223.14), 0.006),
    "A_n_1": ("S_an1", "main", (2907, 1017.69), 0.006),
    "S_mk1": ("S_mk775", "ref", (272, 199.77), 0.006),
    "L_base": ("L_b775", "main", (428, 193.17), 0.006),
    "L_n24": ("L_n24", "main", (181, 294.31), 0.006),
    "L_mk2": ("L_mk2", "ref", (327, 180.83), 0.006),
}
MAIN = [k for k, v in LINES.items() if v[1] == "main"]


# ───────────────────────────── метрики «под водой» ─────────────────────────────

def uw(x):
    """x — P&L по шагам (сутки или сделки). Шаги: новые максимумы, зазоры между ними, эпизоды под водой, макс. просадка."""
    x = np.asarray(x, float)
    n = len(x)
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    prev = np.concatenate([[0.0], peak[:-1]])
    new = cum > prev + EPS
    under = cum < peak - EPS
    hi = np.nonzero(new)[0]
    pts = np.concatenate([[-1], hi]).astype(int)
    gaps = np.diff(pts)
    tail = n - 1 - int(pts[-1])
    segs = [(int(pts[i]) + 1, int(pts[i + 1])) for i in range(len(gaps))] + [(int(pts[-1]) + 1, n - 1)]
    lens = [int(g) for g in gaps] + [tail]
    kmax = int(np.argmax(lens))
    eps, i = [], 0
    while i < n:
        if not under[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and under[j + 1]:
            j += 1
        dd = peak[i:j + 1] - cum[i:j + 1]
        tr = i + int(np.argmax(dd))
        pk = int(hi[hi < i].max()) if (hi < i).any() else -1
        eps.append({"start": i, "end": j, "len": j - i + 1, "depth": float(dd.max()), "trough": tr, "peak": pk, "rec": (j + 1) if j + 1 < n else None})
        i = j + 1
    big = max(eps, key=lambda e: e["depth"]) if eps else None
    return {"n": n, "total": float(cum[-1]) if n else 0.0, "highs": int(len(hi)), "gap_longest": lens[kmax], "gap_seg": segs[kmax],
            "gap_mean": float(gaps.mean()) if len(gaps) else float("nan"), "tail": tail, "episodes": len(eps), "under_share": float(under.mean()),
            "under_longest": max((e["len"] for e in eps), default=0), "maxdd": big["depth"] if big else 0.0, "dd": big, "eps": eps, "cum": cum, "peak": peak}


def selftest():
    r = uw([1, 1, -1, -1, 1, 1, 3, -2, 0, 0])      # cum 1 2 1 0 1 2 5 3 3 3 ; новые максимумы: 0 1 6 ; под водой: 2 3 4 | 7 8 9 (сутки 5 = касание пика)
    assert r["highs"] == 3 and r["episodes"] == 2 and r["gap_longest"] == 5 and r["tail"] == 3 and abs(r["maxdd"] - 2.0) < 1e-12, r
    assert r["under_longest"] == 3 and abs(r["under_share"] - 0.6) < 1e-12 and r["gap_seg"] == (2, 6) and r["dd"]["peak"] == 1 and r["dd"]["rec"] == 5
    r = uw([-1.0, -1.0, 3.0])                       # старт 0: сутки 0–1 под водой, на 2-е — новый максимум
    assert r["highs"] == 1 and r["episodes"] == 1 and r["gap_longest"] == 3 and r["dd"]["peak"] == -1
    r = uw([0.0, 0.0])
    assert r["highs"] == 0 and r["episodes"] == 0 and r["gap_longest"] == 2


# ───────────────────────────── форматирование ─────────────────────────────

def num(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    s = ("{:+,.%df}" if sign else "{:,.%df}") % nd
    return s.format(x).replace(",", " ").replace(".", ",").replace("-", "−")


def pct(x, nd=0):
    return "—" if x is None or x != x else num(x * 100, nd) + " %"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def dstr(d):
    return (D0 + dt.timedelta(days=int(d))).strftime("%d.%m")


# ───────────────────────────── загрузка ─────────────────────────────

def load_btc(base_dirs, g0_ms):
    frames = []
    for p in base_dirs:
        for fn in sorted(os.listdir(p)):
            if len(fn) == 14 and fn.endswith(".csv"):
                frames.append(pd.read_csv(os.path.join(p, fn), usecols=["open_time", "high", "low", "close"]))
    k = pd.concat(frames).drop_duplicates("open_time").sort_values("open_time")
    idx = ((k["open_time"].to_numpy(np.int64) - g0_ms) // MIN_MS).astype(np.int64)
    n = int(idx.max()) + 1
    out = {c: np.full(n, np.nan) for c in ("high", "low", "close")}
    for c in out:
        out[c][idx] = k[c].to_numpy(float)
    miss = int(np.isnan(out["close"]).sum())
    for c in out:
        out[c] = pd.Series(out[c]).ffill().to_numpy()
    return out, miss


def load_lines(code):
    """Сделки линий тем же путём, что tk662_equity.py (его проверки против res/cells* и universe.json остаются)."""
    sys.path.insert(0, code)
    global st, rr
    import tk604_stats as st   # noqa: E402
    import tk620_rr as rr      # noqa: E402
    import tk625_run as r5     # noqa: E402
    import tk638_post as p8    # noqa: E402
    import tk639_post as p9    # noqa: E402
    u = r5.Uni("h775")
    UNI = json.load(open("/data/pages/ml/data/universe.json", encoding="utf-8"))
    assert UNI["days"] == st.NDAYS and UNI["d0"] == str(st.D0), "окно universe.json / tk604_stats разное"
    W = [w.name for w in st.TRADE_WINDOWS]
    gmk = "/data/p42-tk626/gmk/out"
    dirs = {
        "S_b775": lambda w: "/data/p42-tk621/out/all_t24/%s/S_base" % w,
        "S_mk775": lambda w: os.path.join(gmk, w, "cd0838447e287"),
        "S_an1": lambda w: "/data/p42-tk657/cells/%s-S/A_n_1" % w,
        "L_b775": lambda w: "/data/p42-tk621/out/all_t24/%s/L_base" % w,
        "L_n24": lambda w: p8.kind_dir(u, "ladd", w, "L", "N24_q60"),
        "L_mk2": lambda w: os.path.join(gmk, w, "cf280a1e8fc7c"),
    }
    g7 = {"S_an1"}
    res = {}
    for name, (key, role, (n_ref, s_ref), tol) in LINES.items():
        side = "S" if name.startswith(("S_", "A_")) else "L"
        d = {w: (dirs[key](w), side) for w in W}
        if key in g7:      # G7 §13.43 — как tk662_equity.load_cell_window_g7
            tables, daily = p8.read_tables(d)
            for w in st.TRADE_WINDOWS:
                tb, dl = tables[w.name], daily[w.name]
                assert abs(float(sum(dl.values())) - float(tb.net.sum())) <= 1e-4 + 2e-6 * len(tb.net), ("G7 окна", w.name, side)
            ser = st.splice(tables, u.coins(), daily=None)
            kinds, pnl = p8.kinds_pnl(tables)
            assert len(pnl) == len(ser.val) and np.allclose(pnl, ser.val, atol=1e-6), side
        else:
            ser, kinds, pnl = p8.load_cell(u, side, d)
        n, s = len(ser.val), float(np.sum(ser.val))
        assert n == n_ref and abs(s - s_ref) < tol, ("сверка с карточкой TK-662", name, n, n_ref, s, s_ref)
        res[name] = pd.DataFrame({"sym": [str(x) for x in ser.sym_name], "t_in": np.asarray(ser.t_in, np.int64), "t_out": np.asarray(ser.t_out, np.int64),
                                  "day_in": np.asarray(ser.day, np.int64), "pnl": np.asarray(ser.val, float), "kind": [str(k) for k in kinds], "side": side})
    return res


def enrich(df, btc, d0_ms, gb0_ms):
    n = len(btc["close"])
    ie = (df.t_in.to_numpy() - gb0_ms) // MIN_MS - 1
    ix = (df.t_out.to_numpy() - gb0_ms) // MIN_MS
    sgn = np.where(df.side == "S", -1.0, 1.0)
    ok = (ix < n) & (ie >= 0)
    al = np.full(len(df), np.nan)
    al[ok] = sgn[ok] * (btc["close"][ix[ok]] / btc["close"][ie[ok]] - 1) * 100
    df = df.copy()
    df["day_out"] = ((df.t_out - d0_ms) // DAY_MS).astype(int)
    df["hour_out"] = ((df.t_out // 3_600_000) % 24).astype(int)
    df["hold_min"] = (df.t_out - df.t_in) / MIN_MS
    df["btc_aligned"] = al
    df["month"] = [dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m") for t in df.t_out]
    return df.sort_values(["t_out", "t_in", "sym"], kind="stable").reset_index(drop=True)


def btc_days(btc, nd, gb0_ms):
    """Ход BTC за сутки d (UTC): close последней минуты / close минуты перед 00:00 − 1, %; размах (high−low)/prev close, %. Нет данных → NaN."""
    n = len(btc["close"])
    mv, rg = np.full(nd, np.nan), np.full(nd, np.nan)
    for d in range(nd):
        i0, i1 = (d + 1) * 1440, (d + 2) * 1440
        if i1 <= n:
            pc = btc["close"][i0 - 1]
            mv[d] = (btc["close"][i1 - 1] / pc - 1) * 100
            rg[d] = (btc["high"][i0:i1].max() - btc["low"][i0:i1].min()) / pc * 100
    return mv, rg


def sharpe(s):
    s = np.asarray(s, float)
    sd = float(np.std(s, ddof=1)) if len(s) > 1 else 0.0
    return float(s.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0


def pairs(key, mask):
    """Число пар сделок отмеченного вида с одним ключом (сутки или сутки+час): Σ C(c, 2)."""
    c = np.bincount(key[mask])
    return int((c * (c - 1) // 2).sum())


def perm_pairs(key, mask, rng):
    obs = pairs(key, mask)
    k = int(mask.sum())
    sims = np.empty(NPERM, int)
    for b in range(NPERM):
        m = np.zeros(len(key), bool)
        m[rng.choice(len(key), k, replace=False)] = True
        sims[b] = pairs(key, m)
    return obs, float(sims.mean()), float(np.percentile(sims, 5)), float(np.percentile(sims, 95)), float((sims >= obs).mean())


# ───────────────────────────── основной счёт ─────────────────────────────

def main():
    global D0
    code, outdir = sys.argv[1:3]
    selftest()
    os.makedirs(outdir, exist_ok=True)
    raw = load_lines(code)
    D0 = st.D0
    NW = st.NDAYS
    d0_ms = int(dt.datetime.combine(D0, dt.time(), dt.timezone.utc).timestamp() * 1000)
    gb0_ms = d0_ms - DAY_MS
    btc, miss = load_btc(["/data/p42-tk596/jul/klines/BTCUSDT", "/data/p42-tk596/aug/klines/BTCUSDT", "/data/klines/binance/BTCUSDT"], gb0_ms)
    print("BTC: минут %d, пропусков (протянуты) %d" % (len(btc["close"]), miss))
    T = {k: enrich(v, btc, d0_ms, gb0_ms) for k, v in raw.items()}
    ND = {k: max(NW, int(v.day_out.max()) + 1) for k, v in T.items()}
    mv, rg = btc_days(btc, max(ND.values()), gb0_ms)
    rng = np.random.default_rng(SEED)
    out, md = {}, []
    P = md.append

    P("# Таблицы TK-663 (сгенерировано `tools/p42/tk663_shape.py`; 775 контрактов, 01.07–13.11, 500 $, после комиссий, без фандинга)\n")
    P("Сутки — UTC по закрытию сделки. Линии: S_base, A_n_1 (шорт; A_n_1 — «найдено на этих данных»), L_base, L_n24 (лонг); S_mk1 и L_mk2 — по одной справочной строке.\n")

    # ── 0. сводка линий
    rows, S0 = [], {}
    for k, df in T.items():
        r = rr.rr_stats([dict(pnl=p, kind=q) for p, q in zip(df.pnl, df.kind)])
        sd_out = np.bincount(df.day_out, weights=df.pnl, minlength=ND[k])
        sd_in = np.bincount(df.day_in, weights=df.pnl, minlength=NW)
        wins = df.pnl[df.pnl > 0]
        post = int((df.t_out >= d0_ms + NW * DAY_MS).sum())
        S0[k] = dict(n=len(df), sum=float(df.pnl.sum()), mean=float(df.pnl.mean()), rr=r["rr"], n_win=r["n_win"], n_loss=r["n_loss"], mean_win=r["mean_win"], mean_loss=r["mean_loss"],
                     stops=r["stops"], sharpe_out=sharpe(sd_out), sharpe_in=sharpe(sd_in), closes_after_1311=post, nd=ND[k])
        s = r["stops"]
        rows.append([k, len(df), num(df.pnl.sum(), 2, True), num(df.pnl.mean(), 2, True), r["n_win"], pct(r["n_win"] / len(df)), num(r["mean_win"], 2, True), r["n_loss"], num(r["mean_loss"], 2, True),
                     num(r["rr"], 2), "%d · %s $ (%s %%)" % (s["n"], num(s["mean_loss"], 1, True), num(s["mean_loss_pct"], 1)), "%s / %s" % (num(S0[k]["sharpe_out"], 2), num(S0[k]["sharpe_in"], 2)), post])
    P("## 1. Сводка линий\n")
    P("R:R = средний выигрыш ÷ |средний проигрыш|; стопы — выходы `stop` + `nt` (убыток на стоп в $ и % маржи 250 $ при плече 2); Sharpe — годовой по суточным суммам (ряд по закрытию / по дню входа, как TK-662).\n")
    P("\n".join(table(["линия", "сделок", "Σ, $", "на сделку, $", "выигрышей", "доля", "средний выигрыш, $", "убытков", "средний убыток, $", "R:R", "стопы: n · средний", "Sharpe (закр. / вход)", "закрыто после 13.11"], rows)))
    out["lines"] = S0

    # ── 1. «под водой», сутки
    UW, UWt, UWi = {}, {}, {}
    rows, rows_i = [], []
    for k, df in T.items():
        sd = np.bincount(df.day_out, weights=df.pnl, minlength=ND[k])
        a = uw(sd)
        UW[k] = a
        dd = a["dd"]
        per_day = df.pnl.sum() / ND[k]
        rows.append([k, a["episodes"], a["highs"], a["gap_longest"], "%s–%s" % (dstr(a["gap_seg"][0]), dstr(a["gap_seg"][1])), num(a["gap_mean"], 1), a["tail"], num(a["maxdd"], 2),
                     num(a["maxdd"] / per_day, 0) if per_day > 0 else "—",
                     "%s → %s" % (dstr(dd["peak"] + 1), dstr(dd["trough"])) if dd else "—",
                     ("%d (пик→восст.: %d)" % (dd["rec"] - dd["trough"], dd["rec"] - dd["peak"]) if dd["rec"] is not None else "не восстановлена на %s" % dstr(ND[k] - 1)) if dd else "—",
                     a["under_longest"], pct(a["under_share"])])
        ai = uw(np.bincount(df.day_in, weights=df.pnl, minlength=NW))
        UWi[k] = ai
        rows_i.append([k, ai["episodes"], ai["highs"], ai["gap_longest"], num(ai["gap_mean"], 1), num(ai["maxdd"], 2), pct(ai["under_share"])])
    P("## 2. «Под водой» по суткам (спец. п. 1)\n")
    P("Зазор = календарных суток между соседними новыми максимумами конца суток (первый — от старта, последний «хвост» — до конца окна); средний — по закрытым зазорам; «просадка в сутках средней прибыли» = макс. просадка ÷ (Σ ÷ число суток); восстановление — суток от дна (и от пика) до первых суток не под водой.\n")
    P("\n".join(table(["линия", "эпизодов просадки", "новых максимумов", "самый длинный зазор, сут.", "где", "средний зазор, сут.", "хвост на конец", "макс. просадка, $", "просадка в сутках средней прибыли", "пик → дно", "восстановление, сут.", "самый длинный эпизод под водой, сут.", "доля суток под водой"], rows)))
    P("Тот же расчёт по дню входа (ряд TK-662), для сверки: \n")
    P("\n".join(table(["линия", "эпизодов", "новых максимумов", "самый длинный зазор, сут.", "средний зазор, сут.", "макс. просадка, $", "доля суток под водой"], rows_i)))

    # ── 1б. форма суток: доля плюсовых / минусовых / пустых суток и средний плюс и минус (почему новых максимумов мало)
    rows = []
    for k, df in T.items():
        sd = np.bincount(df.day_out, weights=df.pnl, minlength=ND[k])
        up, dn = sd[sd > EPS], sd[sd < -EPS]
        per_stop = S0[k]["stops"]["mean_loss"]
        slope = df.pnl.sum() / ND[k]
        rows.append([k, int((sd > EPS).sum()), int((sd < -EPS).sum()), int((np.abs(sd) <= EPS).sum()), pct(float((sd > EPS).mean())), num(up.mean(), 2, True), num(dn.mean(), 2, True),
                     num(up.mean() / abs(dn.mean()), 2), num(slope, 2, True), num(abs(per_stop) / slope, 1) if per_stop == per_stop and slope > 0 else "—"])
    P("## 2б. Форма суток (по закрытию)\n")
    P("Сутки +/−/пустые; средний плюсовой и минусовой день; «R:R суток» = средний плюсовой ÷ |средний минусовой|; «суток прибыли на один стоп» = |средний стоп| ÷ (Σ ÷ суток) — сколько суток средней прибыли стирает один стоп.\n")
    P("\n".join(table(["линия", "суток +", "суток −", "пустых", "доля плюсовых", "средний плюсовой, $", "средний минусовой, $", "R:R суток", "прибыль в сутки, $", "суток прибыли на один стоп"], rows)))

    # ── 2. «под водой», сделки как часы
    rows = []
    for k, df in T.items():
        a = uw(df.pnl.to_numpy())
        UWt[k] = a
        rate = len(df) / ND[k]
        dd = a["dd"]
        rows.append([k, a["episodes"], a["highs"], a["gap_longest"], num(a["gap_longest"] / rate, 1), num(a["gap_mean"], 1), num(a["gap_mean"] / rate, 1), a["tail"], num(a["maxdd"], 2),
                     (dd["rec"] - dd["trough"] if dd["rec"] is not None else "нет") if dd else "—", a["under_longest"], pct(a["under_share"])])
    P("## 3. «Под водой» по сделкам (часы — сделки, порядок по закрытию; спец. п. 1)\n")
    P("«в сутках по средней частоте» = зазор в сделках ÷ (сделок на сутки в среднем) — сколько суток заняло бы то же число сделок при средней частоте линии.\n")
    P("\n".join(table(["линия", "эпизодов", "новых максимумов", "самый длинный зазор, сделок", "в сутках по средней частоте", "средний зазор, сделок", "в сутках по средней частоте", "хвост, сделок", "макс. просадка, $", "восстановление от дна, сделок", "самый длинный эпизод под водой, сделок", "доля сделок под водой"], rows)))

    # ── 3. анатомия самого длинного зазора между максимумами (сутки)
    rows = []
    for k in MAIN + ["S_mk1", "L_mk2"]:
        df, a = T[k], UW[k]
        lo, hi = a["gap_seg"]
        sub = df[(df.day_out >= lo) & (df.day_out <= hi)]
        w, l = sub[sub.pnl > 0], sub[sub.pnl < 0]
        stp = sub[sub.kind.isin(STOP_KINDS)]
        rows.append([k, "%s–%s (%d сут.)" % (dstr(lo), dstr(hi), hi - lo + 1), len(sub), num(len(sub) / (hi - lo + 1), 2), num(len(df) / ND[k], 2), len(w), num(w.pnl.sum(), 1, True), len(l), num(l.pnl.sum(), 1, True),
                     "%d · %s" % (len(stp), num(stp.pnl.sum(), 1, True)), num(sub.pnl.min(), 1, True) if len(sub) else "—", num(sub.pnl.sum(), 1, True)])
    P("## 4. Анатомия самого длинного зазора между новыми максимумами (по суткам)\n")
    P("\n".join(table(["линия", "зазор", "сделок закрыто", "сделок/сут. внутри", "сделок/сут. в среднем", "выигрышей", "Σ выигрышей, $", "убытков", "Σ убытков, $", "стопов · Σ, $", "худшая сделка, $", "Σ зазора, $"], rows)))

    # ── 4. частота сделок
    rows = []
    for k, df in T.items():
        ce = np.bincount(df.day_in, minlength=NW)
        cx = np.bincount(df.day_out, minlength=ND[k])
        disp = float(ce.var(ddof=1) / ce.mean()) if ce.mean() > 0 else float("nan")
        heavy = float(ce[ce >= 5].sum() / ce.sum())
        rows.append([k, num(ce.mean(), 2), pct(float((ce == 0).mean())), int(ce.max()), num(disp, 1), pct(heavy), pct(float((cx == 0).mean())), num(cx.mean(), 2), int(cx.max())])
    P("## 5. Частота сделок (спец. п. 2)\n")
    P("Вход — по дню открытия (136 суток), закрытие — по дню выхода; индекс дисперсии = дисперсия ÷ среднее суточного числа входов (1 — как у случайных независимых входов, > 1 — кучность); «доля сделок в сутки ≥ 5 входов».\n")
    P("\n".join(table(["линия", "входов в сутки", "суток без входов", "максимум входов в сутки", "индекс дисперсии", "доля сделок в сутки ≥ 5 входов", "суток без закрытий (плоская эквити)", "закрытий в сутки", "максимум закрытий"], rows)))

    # ── 4b. лонг + шорт вместе (слова владельца: «частоту сделок еще покажи мне в день лонг+ шорт вместе»)
    PAIRS = (("L_base", "S_base"), ("L_n24", "A_n_1"))
    rows, rows_u = [], []
    out["pairs"] = {}
    for lk, sk in PAIRS:
        a, b = T[lk], T[sk]
        nd = max(ND[lk], ND[sk])
        en = {"long": np.bincount(a.day_in, minlength=NW), "short": np.bincount(b.day_in, minlength=NW)}
        en["both"] = en["long"] + en["short"]
        cl = {"long": np.bincount(a.day_out, minlength=nd), "short": np.bincount(b.day_out, minlength=nd)}
        cl["both"] = cl["long"] + cl["short"]
        pn = {"long": np.bincount(a.day_out, weights=a.pnl, minlength=nd), "short": np.bincount(b.day_out, weights=b.pnl, minlength=nd)}
        pn["both"] = pn["long"] + pn["short"]
        both = pd.concat([a, b]).sort_values(["t_out", "t_in", "sym"], kind="stable")
        for part, nm in (("long", lk), ("short", sk), ("both", "%s + %s" % (lk, sk))):
            e, c = en[part], cl[part]
            rows.append([nm if part != "both" else "**%s**" % nm, num(e.mean(), 2), pct(float((e == 0).mean())), int(e.max()), num(e.var(ddof=1) / e.mean(), 1), pct(float((c == 0).mean())), num(c.mean(), 2), int(c.max())])
        ub, ut = uw(pn["both"]), uw(both.pnl.to_numpy())
        rate = len(both) / nd
        dd = ub["dd"]
        rows_u.append(["%s + %s" % (lk, sk), num(float(both.pnl.sum()), 2, True), "%s / %s" % (len(a), len(b)), ub["episodes"], ub["highs"], ub["gap_longest"], "%s–%s" % (dstr(ub["gap_seg"][0]), dstr(ub["gap_seg"][1])), num(ub["gap_mean"], 1), ub["tail"],
                       num(ub["maxdd"], 2), "%s → %s" % (dstr(dd["peak"] + 1), dstr(dd["trough"])) if dd else "—", pct(ub["under_share"]), ut["gap_longest"], num(ut["gap_longest"] / rate, 1), pct(ut["under_share"])])
        out["pairs"]["%s+%s" % (lk, sk)] = {"sum": float(both.pnl.sum()), "n": len(both), "gap_longest": ub["gap_longest"], "gap_mean": ub["gap_mean"], "maxdd": ub["maxdd"], "episodes": ub["episodes"], "highs": ub["highs"], "under_share": ub["under_share"],
                                           "trade_gap_longest": ut["gap_longest"], "sharpe_out": sharpe(pn["both"])}
        days = [(D0 + dt.timedelta(days=d)).isoformat() for d in range(nd)]
        pd.DataFrame({"date": days, "entries_long": np.pad(en["long"], (0, nd - NW)), "entries_short": np.pad(en["short"], (0, nd - NW)), "entries_total": np.pad(en["both"], (0, nd - NW)),
                      "closes_long": cl["long"], "closes_short": cl["short"], "closes_total": cl["both"],
                      "pnl_usd_long": pn["long"].round(4), "pnl_usd_short": pn["short"].round(4), "pnl_usd_total": pn["both"].round(4)}).to_csv(os.path.join(outdir, "perday-%s+%s.csv" % (lk, sk)), index=False, encoding="utf-8")
    P("## 5б. Лонг + шорт вместе (добавлено по слову владельца)\n")
    P("Построчно: лонг, шорт, вместе; входов — по дню открытия (136 суток), закрытий — по дню выхода; P&L суток — по закрытию. CSV по суткам: `perday-<лонг>+<шорт>.csv` (входы и закрытия лонга, шорта и вместе; P&L $ по закрытию).\n")
    P("\n".join(table(["линия", "входов в сутки", "суток без входов", "максимум входов в сутки", "индекс дисперсии", "суток без закрытий", "закрытий в сутки", "максимум закрытий"], rows)))
    P("Зазоры между новыми максимумами эквити пары (сумма двух линий, сутки по закрытию; сделки — по закрытию обеих сторон подряд):\n")
    P("\n".join(table(["пара", "Σ, $", "сделок (лонг / шорт)", "эпизодов просадки", "новых максимумов", "самый длинный зазор, сут.", "где", "средний зазор, сут.", "хвост", "макс. просадка, $", "пик → дно", "доля суток под водой", "самый длинный зазор, сделок", "в сутках по средней частоте", "доля сделок под водой"], rows_u)))

    # ── 5. убытки
    P("## 6. Убыточные сделки (спец. п. 3)\n")
    P("Убыточная = P&L < 0 после комиссий; все сделки — CSV `losses-<линия>.csv`. Час, месяц и сутки — по закрытию (когда убыток реализован); «ход BTC» — за сутки закрытия (|ход| < 1 % — боковик).\n")
    loss_rows, kind_blocks, month_rows, hour_cols, btc_rows, clus_rows, coin_blocks, conc_rows, worst = [], [], {}, {}, [], [], {}, [], {}
    for k in MAIN + ["S_mk1", "L_mk2"]:
        df = T[k]
        ls = df[df.pnl < 0]
        r = S0[k]
        s = r["stops"]
        tot_loss = float(ls.pnl.sum())
        loss_rows.append([k, len(ls), num(tot_loss, 2, True), num(ls.pnl.mean(), 2, True), num(r["mean_win"], 2, True), num(r["rr"], 2), num(ls.pnl.min(), 2, True), "%d · %s" % (s["n"], num(s["mean_loss"], 1, True))])
        ls.assign(utc_in=[dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") for t in ls.t_in],
                  utc_out=[dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") for t in ls.t_out],
                  exit_day=[dstr(d) + ".2026" for d in ls.day_out], btc_day_pct=[round(float(mv[d]), 3) if mv[d] == mv[d] else "" for d in ls.day_out]).rename(columns={"sym": "coin", "pnl": "pnl_usd", "kind": "exit_kind", "hour_out": "exit_hour_utc"})[
            ["coin", "utc_in", "utc_out", "hold_min", "exit_kind", "pnl_usd", "month", "exit_day", "exit_hour_utc", "btc_day_pct", "btc_aligned"]].round({"hold_min": 2, "pnl_usd": 4, "btc_aligned": 3}).to_csv(
            os.path.join(outdir, "losses-%s.csv" % k), index=False, encoding="utf-8")
        # по виду выхода
        g = []
        for kd in sorted(df.kind.unique()):
            a, b = df[df.kind == kd], df[(df.kind == kd) & (df.pnl < 0)]
            g.append([kd, len(a), int((a.pnl > 0).sum()), num(a.pnl[a.pnl > 0].sum(), 1, True), len(b), num(b.pnl.sum(), 1, True), num(b.pnl.mean(), 2, True) if len(b) else "—", num(b.pnl.min(), 2, True) if len(b) else "—",
                      num(b.hold_min.mean(), 0) if len(b) else "—", pct(b.pnl.sum() / tot_loss if tot_loss else float("nan")), num(a.pnl.sum(), 1, True)])
        kind_blocks.append((k, g))
        # по месяцам (закрытия)
        for m in sorted(df.month.unique()):
            a, b = df[df.month == m], df[(df.month == m) & (df.pnl < 0)]
            month_rows.setdefault(m, {})[k] = (float(a.pnl.sum()), len(b), float(b.pnl.sum()))
        # по часу
        for hb in range(0, 24, HOUR_BIN):
            a = df[(df.hour_out >= hb) & (df.hour_out < hb + HOUR_BIN)]
            b = a[a.pnl < 0]
            hour_cols.setdefault(hb, {})[k] = (len(a), len(b), float(b.pnl.sum()), float(a.pnl.sum()))
        # по дню BTC
        for nm, f in (("падение ≤ −1 %", lambda d: mv[d] <= -BTC_FLAT), ("боковик", lambda d: abs(mv[d]) < BTC_FLAT), ("рост ≥ +1 %", lambda d: mv[d] >= BTC_FLAT)):
            msk = np.array([bool(f(d)) if mv[d] == mv[d] else False for d in df.day_out])
            a, b = df[msk], df[msk & (df.pnl < 0).to_numpy()]
            days_n = len({int(d) for d in range(ND[k]) if mv[d] == mv[d] and f(d)})
            btc_rows.append([k, nm, days_n, len(a), num(a.pnl.sum(), 1, True), num(a.pnl.mean(), 2, True) if len(a) else "—", len(b), num(b.pnl.sum(), 1, True), int(a.kind.isin(STOP_KINDS).sum()), num(a.pnl[a.kind.isin(STOP_KINDS)].sum(), 1, True)])
        nanbtc = int(np.isnan(mv[df.day_out.to_numpy()]).sum())
        # по монетам
        cg = ls.groupby("sym").pnl.agg(["count", "sum"]).sort_values("sum").head(10)
        allc = df.groupby("sym").pnl.sum()
        coin_blocks[k] = [[c, int(r_["count"]), num(r_["sum"], 2, True), pct(r_["sum"] / tot_loss), num(allc[c], 2, True)] for c, r_ in cg.iterrows()]
        full = ls.groupby("sym").pnl.agg(n_losses="count", sum_loss="sum").sort_values("sum_loss")
        full["share_of_loss"] = full.sum_loss / tot_loss
        full["coin_total_pnl"] = [allc[c] for c in full.index]
        full.round(4).to_csv(os.path.join(outdir, "coins-%s.csv" % k), encoding="utf-8")
        # кластеризация
        dkey = df.day_out.to_numpy()
        hkey = dkey * 24 + df.hour_out.to_numpy()
        for nm, msk in (("стопы (`stop`+`nt`)", df.kind.isin(STOP_KINDS).to_numpy()), ("все убытки", (df.pnl < 0).to_numpy())):
            if msk.sum() < 2:
                continue
            o1 = perm_pairs(dkey, msk, rng)
            o2 = perm_pairs(hkey, msk, rng)
            cnt = np.bincount(dkey[msk])
            dl = pd.Series(df.pnl.to_numpy()[msk]).groupby(dkey[msk]).sum().sort_values()
            clus_rows.append([k, nm, int(msk.sum()), int((cnt >= 2).sum()), pct(float(cnt[cnt >= 2].sum() / msk.sum())), int(cnt.max()), pct(float(dl.head(5).sum() / dl.sum())),
                              "%d / %s (%s–%s), p %s" % (o1[0], num(o1[1], 1), num(o1[2], 0), num(o1[3], 0), num(o1[4], 3)),
                              "%d / %s (%s–%s), p %s" % (o2[0], num(o2[1], 1), num(o2[2], 0), num(o2[3], 0), num(o2[4], 3))])
        # концентрация прибыли
        srt = df.pnl.sort_values(ascending=False)
        sd = pd.Series(df.pnl.to_numpy()).groupby(df.day_out.to_numpy()).sum().sort_values(ascending=False)
        tot, gw = float(df.pnl.sum()), float(df.pnl[df.pnl > 0].sum())
        conc_rows.append([k, num(tot, 2, True), len(df), pct(float(srt.head(1).sum() / tot)), pct(float(srt.head(5).sum() / tot)), pct(float(srt.head(10).sum() / tot)), num(tot - float(srt.head(10).sum()), 1, True),
                          pct(float(sd.head(1).sum() / tot)), pct(float(sd.head(5).sum() / tot)), pct(float(sd.head(10).sum() / tot)), pct(float(srt.head(10).sum() / gw)),
                          pct(float((df.pnl > 0).mean())), num(r["mean_win"], 2, True), num(r["mean_loss"], 2, True), num(r["rr"], 2)])
        # худшие сутки
        sdf = df.assign(ls=df.pnl < 0, stp=df.kind.isin(STOP_KINDS))
        days = sdf.groupby("day_out").agg(pnl=("pnl", "sum"), trades=("pnl", "size"), losers=("ls", "sum"), stops=("stp", "sum"), worst=("pnl", "min")).sort_values("pnl").head(10)
        wr = []
        for d, rw in days.iterrows():
            x = sdf[sdf.day_out == d]
            st_ = x[x.stp].sort_values("pnl")
            names = ", ".join(c.replace("USDT", "") for c in (st_.sym if len(st_) else x[x.pnl < 0].sort_values("pnl").sym).head(4)) + (" +%d" % (max(len(st_), int(rw.losers) if not len(st_) else 0) - 4) if (len(st_) if len(st_) else int(rw.losers)) > 4 else "")
            hh = "%02d–%02d" % (st_.hour_out.min(), st_.hour_out.max()) if len(st_) else "—"
            al = x.btc_aligned[x.pnl < 0].mean()
            wr.append([dstr(d), num(rw.pnl, 1, True), int(rw.trades), int(rw.losers), int(rw.stops), num(rw.worst, 1, True), names, hh, num(mv[d], 1, True) if mv[d] == mv[d] else "—", num(rg[d], 1) if rg[d] == rg[d] else "—",
                       num(al, 2, True) if al == al else "—"])
        worst[k] = (wr, float(days.pnl.sum()), float(days.pnl.sum() / df.pnl[df.pnl < 0].sum()))
        pd.DataFrame([{"day": dstr(d) + ".2026", "pnl": round(float(rw.pnl), 4), "trades": int(rw.trades), "losers": int(rw.losers), "stops": int(rw.stops), "worst": round(float(rw.worst), 4), "btc_day_pct": round(float(mv[d]), 3) if mv[d] == mv[d] else ""} for d, rw in days.iterrows()]).to_csv(
            os.path.join(outdir, "worstdays-%s.csv" % k), index=False, encoding="utf-8")
    P("### 6.1. Итоги убытков\n")
    P("\n".join(table(["линия", "убытков", "Σ убытков, $", "средний убыток, $", "средний выигрыш, $", "R:R", "худший, $", "стопов · средний, $"], loss_rows)))
    P("### 6.2. По виду выхода (все сделки вида и их убыточная часть)\n")
    for k, g in kind_blocks:
        P("**%s**\n" % k)
        P("\n".join(table(["выход", "сделок", "из них выигрышей", "Σ выигрышей, $", "убытков", "Σ убытков, $", "средний убыток, $", "худший, $", "средн. удержание убыточных, мин", "доля Σ убытков", "Σ вида, $"], g)))
    P("### 6.3. По месяцам закрытия: Σ P&L всех сделок · убытков · Σ убытков, $\n")
    head = ["месяц"] + [x for k in MAIN for x in ("%s Σ" % k, "убытков", "Σ убытков")]
    rows = [[m] + [x for k in MAIN for x in (num(month_rows[m][k][0], 1, True), month_rows[m][k][1], num(month_rows[m][k][2], 1, True))] for m in sorted(month_rows)]
    P("\n".join(table(head, rows)))
    P("### 6.4. По часу закрытия (UTC, окна по 3 ч): сделок · убытков · Σ убытков, $ · Σ P&L всех, $\n")
    head = ["часы"] + [x for k in MAIN for x in ("%s сделок" % k, "убытков", "Σ убытков", "Σ всех")]
    rows = [["%02d–%02d" % (hb, hb + HOUR_BIN)] + [x for k in MAIN for x in (hour_cols[hb][k][0], hour_cols[hb][k][1], num(hour_cols[hb][k][2], 1, True), num(hour_cols[hb][k][3], 1, True))] for hb in sorted(hour_cols)]
    P("\n".join(table(head, rows)))
    P("### 6.5. По ходу BTC в сутки закрытия\n")
    P("\n".join(table(["линия", "день BTC", "суток", "сделок закрыто", "Σ всех, $", "на сделку, $", "убытков", "Σ убытков, $", "стопов", "Σ стопов, $"], btc_rows)))
    P("### 6.6. Монеты: 10 монет с наибольшей суммой убытков (полный список — `coins-<линия>.csv`)\n")
    for k in MAIN:
        P("**%s** — монет с убытками %d\n" % (k, int(T[k][T[k].pnl < 0].sym.nunique())))
        P("\n".join(table(["монета", "убытков", "Σ убытков, $", "доля убытков линии", "Σ монеты (все сделки), $"], coin_blocks[k])))
    P("### 6.7. Кластеризация убытков по закрытию\n")
    P("«Пар» = число пар сделок вида с одними и теми же сутками (час — сутки+час); сравнение с перестановкой: те же n сделок выбираются случайно среди всех сделок линии (фиксированные времена закрытия, %d перестановок, seed %d): пар в среднем (5–95 %%) и p = доля перестановок с не меньшим числом пар.\n" % (NPERM, SEED))
    P("\n".join(table(["линия", "вид", "n", "суток с ≥ 2", "доля сделок вида в таких сутках", "максимум в сутки", "5 худших суток: доля Σ вида", "пар в сутки: набл. / перест. (5–95 %)", "пар в сутки+час: набл. / перест. (5–95 %)"], clus_rows)))
    P("### 6.8. 10 худших суток по закрытию\n")
    P("Монеты — стопнутые (если стопов нет — самые убыточные); часы — диапазон закрытий стопов UTC; «ход против» = средний ход BTC за сделку по убыточным в пользу стороны (− = BTC шёл против сделок).\n")
    for k in ("S_base", "A_n_1", "L_base", "L_n24"):
        wr, ssum, share = worst[k]
        P("**%s** — 10 худших суток: Σ %s $ = %s всей суммы убытков линии\n" % (k, num(ssum, 1, True), pct(share)))
        P("\n".join(table(["сутки", "P&L, $", "сделок", "убытков", "стопов", "худшая, $", "монеты", "часы стопов", "BTC за сутки, %", "размах BTC, %", "ход против, %"], wr)))
    P("## 7. Концентрация прибыли (спец. п. 4)\n")
    P("Доли — от Σ линии (могут быть > 100 %, если остальное в минусе); сутки — по закрытию; «Σ без топ-10 сделок» — сумма всех остальных сделок.\n")
    P("\n".join(table(["линия", "Σ, $", "сделок", "топ-1 сделка", "топ-5 сделок", "топ-10 сделок", "Σ без топ-10 сделок, $", "топ-1 сутки", "топ-5 суток", "топ-10 суток", "топ-10 сделок: доля Σ выигрышей", "доля выигрышей", "средний выигрыш, $", "средний убыток, $", "R:R"], conc_rows)))
    out["underwater_day"] = {k: {x: v[x] for x in ("episodes", "highs", "gap_longest", "gap_seg", "gap_mean", "tail", "maxdd", "under_longest", "under_share")} for k, v in UW.items()}
    out["underwater_trade"] = {k: {x: v[x] for x in ("episodes", "highs", "gap_longest", "gap_mean", "tail", "maxdd", "under_longest", "under_share")} for k, v in UWt.items()}
    open(os.path.join(outdir, "tables.md"), "w", encoding="utf-8").write("\n".join(md))
    json.dump(out, open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("готово:", os.path.join(outdir, "tables.md"))


if __name__ == "__main__":
    main()
