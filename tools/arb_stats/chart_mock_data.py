"""Данные для макета вкладки «Арбитраж» (график «конец слота + полоса», «разрыв сейчас», режим «от нуля») по <live>/parsed на настоящих данных.

Запуск на srv: python3 chart_mock_data.py <live> <выход.pkl> [PAR]   (≈ 5 мин на 3 ядрах; перед ним — arb_live.parse_all, чтобы parsed был свежим)
Пишет pickle {ключ пары: {"std": как arb_live.hook без рядов, "g": разрыв сейчас (bps со знаком), "gs": [Sell, Buy] закрытия последней минуты,
"z": сводка эпизодов «от нуля», "ch": ряды слотов (bps): Se/Sl/Sh — Sell конец/мин/макс слота, Be/Bl/Bh — то же для Buy, C — центр}}.
Разрыв сейчас g: Sell > 0 → +Sell (продать на A, купить на B уже выгодно), Buy < 0 → +Buy (отрицательное: купить на A, продать на B), иначе 0 (Sell ≤ Buy всегда, оба знака сразу невозможны).
"""
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze  # noqa: E402
import arb_live  # noqa: E402
import core  # noqa: E402

TH = core.FEE_RT_BPS
_orig = core.analyze_pair


def zero_eps(T, S, Bu, depth, t0, t1):
    """Эпизоды порога комиссий, отсчитанные от нуля (а не от почасового центра): вход Sell ≥ TH или −Buy ≥ TH, выход — середина вернулась к нулю."""
    inw = T >= t0
    Tw, Sw, Bw, dw = T[inw], S[inw], Bu[inw], depth[inw]
    if len(Tw) < 50:
        return []
    M = (Sw + Bw) / 2
    rows = []
    for dirn, ent, ex, e in ((1, Sw >= TH, M <= 0, Sw), (-1, -Bw >= TH, M >= 0, -Bw)):
        for i, j in core.episodes(Tw, ent, ex):
            jj = j if j >= 0 else len(Tw) - 1
            tau = int(Tw[j] - Tw[i]) if j >= 0 else int(t1 - Tw[i])
            gross = None
            if j >= 0:
                gross = float(Sw[i] - Bw[j]) if dirn == 1 else float(Sw[j] - Bw[i])
            rows.append((int(Tw[i]), dirn, tau, j < 0, float(e[i:jj + 1].max()), gross, float(dw[i])))
    return rows


def patched(A, B, t0, t1):
    r = _orig(A, B, t0, t1)
    if r is None:
        return None
    row, bars, eps, hist = r
    T, S, Bu, ia, ib = core.pair_series(A, B, t1, stale_ms=None)
    depth = np.minimum(A["dep"][ia], B["dep"][ib])
    row["_z"] = zero_eps(T, S, Bu, depth, t0, t1)
    return row, bars, eps, hist


def slot_series(bars, t0, t1):
    """Ряды слотов SLOT_MIN минут: конец / мин / макс Sell и Buy, центр (bps). Минуты без записей — последнее известное значение (как «как было»)."""
    m_first = bars["m0"]
    m_last = t1 // 60000 - 1
    nm = m_last - m_first + 1
    sb, bb, mins = bars["S"], bars["B"], bars["m0"] + bars["mins"]
    idx = mins - m_first
    ok = idx < nm

    def dense(a):       # a — (4, n) open, high, low, close → close растянут вперёд, high/low только где есть записи
        close = np.full(nm, np.nan)
        close[idx[ok]] = a[3][ok]
        c = np.where(np.isnan(close), np.nan, close)
        # ffill
        last = np.where(~np.isnan(c), np.arange(nm), 0)
        np.maximum.accumulate(last, out=last)
        c = c[last]
        hi, lo = c.copy(), c.copy()
        hi[idx[ok]] = a[1][ok]
        lo[idx[ok]] = a[2][ok]
        return c, lo, hi

    Sc, Sl, Sh = dense(sb)
    Bc, Bl, Bh = dense(bb)
    cen = bars["centre"]
    cm = np.clip(np.arange(nm) + m_first - bars["centre_m0"], 0, len(cen) - 1)
    C = cen[cm]
    tm0 = t0 // 60000
    n = int(np.ceil((t1 - t0) / (core.SLOT_MIN * 60000)))
    sl = (np.arange(nm) + m_first - tm0) // core.SLOT_MIN
    out = {k: np.full(n, np.nan) for k in ("Se", "Sl", "Sh", "Be", "Bl", "Bh", "C")}
    for j in range(n):
        w = np.flatnonzero(sl == j)
        if not len(w):
            continue
        out["Se"][j], out["Be"][j], out["C"][j] = Sc[w[-1]], Bc[w[-1]], C[w[-1]]
        out["Sl"][j], out["Sh"][j] = Sl[w].min(), Sh[w].max()
        out["Bl"][j], out["Bh"][j] = Bl[w].min(), Bh[w].max()
    return {k: [None if not np.isfinite(x) else round(float(x), 2) for x in v] for k, v in out.items()}, (float(Sc[-1]), float(Bc[-1]))


def hook(row, bars, eps, hist):
    t0, t1 = analyze.G["t0"], analyze.G["t1"]
    std = arb_live.hook(row, bars, eps, hist)
    if std is None:
        return None
    std.pop("S"), std.pop("B"), std.pop("C")
    ch, (s_now, b_now) = slot_series(bars, t0, t1)
    g = s_now if s_now > 0 else b_now if b_now < 0 else 0.0
    z = row["_z"]
    zt = np.array([r[0] for r in z], np.int64)
    zs = core.ep_table_stats(z)
    zs["cnt"] = [int((zt >= t1 - h * 3600_000).sum()) for h in arb_live.PERIODS_H]
    zs["big"] = core.big_counts(z, t1) if z else [0, 0, 0]
    return {"std": std, "g": round(g, 2), "gs": [round(s_now, 2), round(b_now, 2)], "z": zs, "ch": ch}


def main(live, out, par=3):
    core.analyze_pair = patched
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    t1 = (now - 120_000) // 60_000 * 60_000
    t0 = max(arb_live.START_MS, t1 - arb_live.WINDOW_MS)
    _, day = arb_live.symmap_for(datetime.fromtimestamp(now / 1000, timezone.utc).date().isoformat())
    _, det, _, _, _ = analyze.compute(str(Path(live, "parsed")), day, t0, t1, "e", hook=hook, par=par)
    det = {k: v for k, v in det.items() if v}
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "det": det, "slot": core.SLOT_MIN * 60, "fee": TH, "names": arb_live.NAMES}, f, protocol=4)
    print({"pairs": len(det), "t0": t0, "t1": t1})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
