#!/usr/bin/env python3
"""TK-493 F7, ворота G9 (ПК, numpy): независимая реализация теста Энгла-Грейнджера на 30 парах против `form` (Rust, Deck).

Читает панели формирования (копии ~/xdl9/form/<биржа>/<SYM>.sap, float64), строит ту же выборку, что form (окно 02.10-14.10,
13 суток, половины по границе блока 12 x 768 минут = 9216), и считает ПРЯМО по рядам (lstsq с константой, затем ADF остатка без
лага и без константы, как определено в eg.rs): t_first, t_second по половинам и beta по всему окну.
Реализация написана заново через массивы и lstsq, а не через суммы 15 слагаемых, как в egmat.rs / eg.rs.
Допуск: относительное расхождение t <= 1e-6 (суммы f64 против lstsq), beta <= 1e-8.
"""
import json, os, struct, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

GRID_W0 = (S.D_TRADE0 - S.EG_DAYS) * S.MIN_DAY        # абсолютная минута начала окна EG (02.10 00:00)
LEN = S.EG_DAYS * S.MIN_DAY
MID = 12 * 6 * 128                                    # 12 блоков по 6 кусков по CH=128


def read_sap(path):
    with open(path, "rb") as f:
        h = f.read(24)
        assert h[:4] == b"SAP1"
        first_min, n = struct.unpack("<qQ", h[8:24])
        op = np.frombuffer(f.read(8 * n), dtype="<f8")
        cl = np.frombuffer(f.read(8 * n), dtype="<f8")
    return first_min, cl


def window_ln(path):
    fm, cl = read_sap(path)
    row = np.full(LEN, np.nan)
    lo, hi = max(fm, GRID_W0), min(fm + len(cl), GRID_W0 + LEN)
    if hi > lo:
        row[lo - GRID_W0:hi - GRID_W0] = np.log(cl[lo - fm:hi - fm])
    return row


def eg_range(lx, ly, k_lo, k_hi, min_obs=300):
    k = np.arange(max(k_lo, 1), k_hi)
    ok = np.isfinite(lx[k]) & np.isfinite(lx[k - 1]) & np.isfinite(ly[k]) & np.isfinite(ly[k - 1])
    k = k[ok]
    n = len(k)
    if n < min_obs:
        return None
    x, y, x1, y1 = lx[k], ly[k], lx[k - 1], ly[k - 1]
    A = np.column_stack([np.ones(n), x])
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    u, u1 = y - a - b * x, y1 - a - b * x1
    d = u - u1
    g = float(np.dot(d, u1) / np.dot(u1, u1))
    res = d - g * u1
    s2 = float(np.dot(res, res) / (n - 1))
    return dict(beta=float(b), t=g / np.sqrt(s2 / float(np.dot(u1, u1))), n=n)


def eg_pair(path_y, path_x):
    """Направление как в form: y = a + b*x; нога x - вторая в строке detail."""
    ly, lx = window_ln(path_y), window_ln(path_x)
    h1 = eg_range(lx, ly, 1, MID)
    h2 = eg_range(lx, ly, MID, LEN)
    full = eg_range(lx, ly, 1, LEN)
    return h1, h2, full


def main():
    pairs = json.load(open(os.path.join(S.SEL, "g9_pairs.json"), encoding="utf-8"))
    base = os.path.join(S.SEL, "g9_sap")
    rows, worst_t, worst_b = [], 0.0, 0.0
    for p in pairs:
        ex = p["catalog"].split("-")[0]
        h1, h2, full = eg_pair(os.path.join(base, ex, p["y"] + ".sap"), os.path.join(base, ex, p["x"] + ".sap"))
        if h1 is None or h2 is None or full is None:
            rows.append([p["catalog"], p["y"], p["x"], "нет данных numpy"] + [""] * 8)
            continue
        dt1 = abs(h1["t"] - p["t_first"]) / max(1.0, abs(p["t_first"]))
        dt2 = abs(h2["t"] - p["t_second"]) / max(1.0, abs(p["t_second"]))
        db = abs(full["beta"] - p["beta"]) / max(1.0, abs(p["beta"]))
        worst_t, worst_b = max(worst_t, dt1, dt2), max(worst_b, db)
        rows.append([p["catalog"], p["y"], p["x"], "ok" if max(dt1, dt2) <= 1e-6 and db <= 1e-8 else "РАСХОЖДЕНИЕ",
                     "%.9f" % p["t_first"], "%.9f" % h1["t"], "%.9f" % p["t_second"], "%.9f" % h2["t"],
                     "%.9f" % p["beta"], "%.9f" % full["beta"], "%.2e" % max(dt1, dt2), "%.2e" % db])
    S.write_csv(os.path.join(S.DOCS, "g9_eg_vs_numpy.csv"),
                ["catalog", "y", "x", "status", "t_first_form", "t_first_numpy", "t_second_form", "t_second_numpy", "beta_form",
                 "beta_numpy", "rel_dt_max", "rel_dbeta"], rows)
    bad = [r for r in rows if r[3] != "ok"]
    print("пар", len(rows), "расхождений", len(bad), "макс отн. dt %.2e, dbeta %.2e" % (worst_t, worst_b))
    return len(bad)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
