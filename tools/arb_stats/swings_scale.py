"""Проверка насыщения дисперсии середины пары до любой подгонки OU/AR(1): структурная функция, дисперсия в окнах, оценка Хёрста, зависимость полураспада от длины оценки.

Запуск: python3 swings_scale.py <swings.pkl> <выход.md>
Всё без вычитания уровня (структурная функция — по приращениям; дисперсия в окнах — внутри окна, без суточного уровня). Контроль: имитации OU (полураспад 20 мин) и случайного блуждания с шумом на той же длине.
Насыщение: у стационарного процесса с временем корреляции ≪ L дисперсия в окне и D(τ) = E(x_{t+τ} − x_t)² выходят на плато (D → 2σ²); у блуждания D ~ τ, у длинной памяти D ~ τ^{2H}, H > 0.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import swings_regime  # noqa: E402

TAUS = (1, 2, 5, 10, 20, 30, 60, 120, 180, 360, 720)
LENS = (10, 30, 60, 180, 360, 720, 1440)
HL_LENS = (180, 360, 720, 1440, 2880)


def grp_of(k):
    return "bx" if "bingx" in k[1:] else "ok"


def variogram(x):
    return np.array([np.mean((x[t:] - x[:-t]) ** 2) if len(x) > 3 * t else np.nan for t in TAUS])


def win_var(x):
    out = []
    for L in LENS:
        nb = len(x) // L
        out.append(np.mean([x[i * L:(i + 1) * L].var() for i in range(nb)]) if nb >= 2 else np.nan)
    return np.array(out)


def ar1_hl(x):
    """Полураспад (мин) по МНК AR(1) с константой; φ ≥ 1 → inf."""
    u, y = x[:-1], x[1:]
    du = u - u.mean()
    den = (du ** 2).sum()
    if den <= 0:
        return np.nan, np.nan
    phi = (du * (y - y.mean())).sum() / den
    return (np.log(2) / -np.log(phi) if 0 < phi < 1 else (np.inf if phi >= 1 else np.nan)), phi


def hl_by_len(x):
    out = {}
    for L in HL_LENS + (len(x),):
        nb = max(len(x) // L, 1)
        r = [ar1_hl(x[i * L:(i + 1) * L]) for i in range(nb)] if L < len(x) else [ar1_hl(x)]
        out[L] = (np.nanmedian([a for a, _ in r]), np.nanmedian([b for _, b in r]))
    return out


def slope(vg, lo, hi):
    ts = np.array(TAUS, float)
    m = (ts >= lo) & (ts <= hi) & np.isfinite(vg) & (vg > 0)
    return np.polyfit(np.log(ts[m]), np.log(vg[m]), 1)[0] if m.sum() >= 3 else np.nan


def med(a):
    a = np.array([v for v in a if np.isfinite(v)], float)
    return np.percentile(a, [10, 50, 90]) if len(a) else np.full(3, np.nan)


def f(x, d=2):
    return "—" if not np.isfinite(x) else f"{x:.{d}f}"


def simulate(kind, n, rng):
    if kind == "ou":
        phi = 0.5 ** (1 / 20.0)
        e = rng.normal(0, np.sqrt(1 - phi * phi), n)
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = phi * x[t - 1] + e[t]
        return x
    return np.cumsum(rng.normal(0, 0.1, n)) + rng.normal(0, 0.3, n)


def main(src, dst):
    import pickle
    d = pickle.load(open(src, "rb"))
    rows = {"ok": [], "bx": []}
    for k, v in d["pairs"].items():
        r = swings_regime.raw_series(v)
        if r is None:
            continue
        x = r[1]
        if x.std() < 1e-9:
            continue
        rows[grp_of(k)].append({"vg": variogram(x), "wv": win_var(x), "hl": hl_by_len(x), "var": x.var()})
    out = []
    p = out.append
    p("### Насыщение дисперсии середины (до любой подгонки)")
    p("Минутная сетка m = (Sell + Buy)/2, % ; D(τ) — средний квадрат приращения за τ мин; σ²(L) — дисперсия внутри окна L мин (среднее по непересекающимся окнам); медиана [p10; p90] по парам.")
    p("| группа | пар | D(60)/D(10) | D(360)/D(60) | D(720)/D(180) | σ²(360)/σ²(60) | σ²(1440)/σ²(60) | σ²(всё окно)/σ²(60) | VR(60) = D(60)/(60·D(1)) | наклон D: 1–10 мин | 10–360 мин | 60–720 мин |")
    p("|---|---|---|---|---|---|---|---|---|---|---|---|")
    tx = {t: i for i, t in enumerate(TAUS)}
    lx = {l: i for i, l in enumerate(LENS)}
    for g, rs in rows.items():
        def q(fn):
            a = med([fn(r) for r in rs])
            return f"{f(a[1])} [{f(a[0])}; {f(a[2])}]"
        p(f"| {g} | {len(rs)} | {q(lambda r: r['vg'][tx[60]] / r['vg'][tx[10]])} | {q(lambda r: r['vg'][tx[360]] / r['vg'][tx[60]])} | {q(lambda r: r['vg'][tx[720]] / r['vg'][tx[180]])} | "
          f"{q(lambda r: r['wv'][lx[360]] / r['wv'][lx[60]])} | {q(lambda r: r['wv'][lx[1440]] / r['wv'][lx[60]])} | {q(lambda r: r['var'] / r['wv'][lx[60]])} | "
          f"{q(lambda r: r['vg'][tx[60]] / (60 * r['vg'][tx[1]]))} | {q(lambda r: slope(r['vg'], 1, 10))} | {q(lambda r: slope(r['vg'], 10, 360))} | {q(lambda r: slope(r['vg'], 60, 720))} |")
    p("")
    p("Наклон D(τ) в логарифмах = 2H (H — показатель Хёрста приращений): 0 — насыщение (стационарный), 1 — блуждание, между — длинная память. Если D(360)/D(60) ≈ 1, дисперсия насытилась.")
    p("")
    p("### Полураспад AR(1) в зависимости от длины оценки")
    p("AR(1) с константой на непересекающихся блоках длины L мин (медиана по блокам пары, затем медиана [p10; p90] по парам), мин. Если полураспад растёт вместе с L — это артефакт, а не свойство процесса.")
    rng = np.random.default_rng(1)
    ctrl = {}
    for kind in ("ou", "rw"):
        ctrl[kind] = [hl_by_len(simulate(kind, 3366, rng)) for _ in range(150)]
    p("| выборка | " + " | ".join(f"L = {L}" for L in HL_LENS) + " | всё окно (≈ 3366) |")
    p("|---|" + "---|" * (len(HL_LENS) + 1))
    for name, lst in [(f"{g} ({len(rs)} пар)", [r["hl"] for r in rs]) for g, rs in rows.items()] + [("контроль: OU, полураспад 20 мин (150 имитаций)", ctrl["ou"]), ("контроль: блуждание + шум (150 имитаций)", ctrl["rw"])]:
        cells = []
        for L in HL_LENS + (None,):
            vals = [h[L if L is not None else max(h)][0] for h in lst]
            vals = [v for v in vals if np.isfinite(v)]
            inf = np.mean([not np.isfinite(h[L if L is not None else max(h)][0]) for h in lst])
            a = np.percentile(vals, [10, 50, 90]) if vals else [np.nan] * 3
            cells.append(f"{f(a[1], 0)} [{f(a[0], 0)}; {f(a[2], 0)}]" + (f" (φ ≥ 1: {inf * 100:.0f} %)" if inf > 0.01 else ""))
        p(f"| {name} | " + " | ".join(cells) + " |")
    p("")
    Path(dst).write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
