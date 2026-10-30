#!/usr/bin/env python3
"""TK-465: синтетика tk461_bp_events_b.py - случайные минутные свечи 3 монет x 60 сут; события строит исходный bp_g*.py, колонки -
tk461_bp_events_b.py; сверка: число событий сходится с файлом событий, колонки/стороны/время входа осмысленны.
python tools/compute/test_tk461_bp_events_b.py"""
import os, subprocess, sys, tempfile
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.join(HERE, "..")
PY = sys.executable


def gen(d, sym, seed):
    r = np.random.default_rng(seed); n = 60 * 1440
    t0 = 1735689600000  # 2025-01-01 UTC
    ret = r.standard_normal(n) * 0.0008 * (1 + 3 * (r.random(n) < 0.01))
    c = 100 * np.exp(np.cumsum(ret)); o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(r.standard_normal(n)) * 0.0004); l = np.minimum(o, c) * (1 - np.abs(r.standard_normal(n)) * 0.0004)
    pd.DataFrame(dict(t=t0 + np.arange(n, dtype=np.int64) * 60000, o=o, h=h, l=l, c=c, v=1.0)).to_csv(f"{d}/{sym}.csv.gz", index=False)


def run(*a):
    p = subprocess.run([PY, "-I", *a], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert p.returncode == 0, f"{a[0]} rc={p.returncode}: {(p.stdout or '')[-2000:]}"
    return (p.stdout or "").strip()


def main():
    with tempfile.TemporaryDirectory() as d:
        src = f"{d}/src"; os.makedirs(src)
        syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
        for i, s in enumerate(syms): gen(src, s, 458 + i)
        for g in ("g5", "g6", "g61", "g7", "g8", "g63"):
            run(os.path.join(TOOLS, f"bp_{g}.py"), src, f"{d}/bp-{g}", *syms)
        out = {}
        for g in ("g5", "g6", "g61", "g7", "g8", "g63"):
            evp = f"{d}/bp-{g}-out-events.csv.gz" if g == "g63" else f"{d}/bp-{g}-events.csv.gz"
            ev = pd.read_csv(evp)
            if g == "g63":
                ev = ev[ev.def_id.str.contains(":bull|:bear")]
            o = f"{d}/o-{g}.csv.gz"
            run(os.path.join(HERE, "tk461_bp_events_b.py"), g, evp, src, o)
            x = pd.read_csv(o); out[g] = (len(ev), x)
            print(g, "events", len(ev), "rows", len(x))
            assert len(ev) > 0, g
            assert list(x.columns)[:8] == ["sym", "tf", "ev", "dir", "side", "t0", "tt0", "atr_bps"], x.columns
            assert set(x.side) <= {1, -1}
        n, x = out["g5"]; assert len(x) == 2 * n and (x.groupby("dir").side.first().to_dict() == {"long": 1, "short": -1}), "g5"
        n, x = out["g61"]; assert len(x) == 2 * n, "g61"
        n, x = out["g6"]; assert len(x) == 2 * n and x.ss_bps.isna().all(), "g6"
        # время входа g5: tt0 + 60000 - минута, следующая за минутой снятия; снятие строго после конца диапазона Азии (>= начало + 420 мин)
        e5 = pd.read_csv(f"{d}/bp-g5-events.csv.gz")
        t = out["g5"][1]; t = t[t.dir == "long"].reset_index(drop=True)
        dm = (t.tt0.values - e5.ts_utc_ms.values) / 60000  # минута снятия от начала диапазона Азии: >= 420 и < 960
        assert ((dm >= 420) & (dm < 960)).all(), dm[:5]
        # g8: стоп - ρ протокола: ss>0 у всех, сторона = знак
        n, x = out["g8"]; assert len(x) == n and (x.ss_bps > 0).all() and x.ss_bps.equals(x.ot1_bps), "g8"
        assert (x.side == np.where(x.dir == "short", -1, 1)).all()
        # g7: хай-свип -> шорт
        n, x = out["g7"]; assert len(x) == n and (x.side == np.where(x.dir == "high", -1, 1)).all(), "g7"
        # g7: ss по §12.2 п.1 - до подтверждённого свинга k=2, > 0 у части событий; ss = ot1
        assert x.ss_bps.notna().any() and (x.ss_bps.dropna() > 0).all() and x.ss_bps.equals(x.ot1_bps), "g7 ss"
        # g63: как G4 - длина = число bull/bear out-событий, ss > 0, сторона bull -> +1, bear -> -1
        n, x = out["g63"]; assert len(x) == n and (x.ss_bps.dropna() > 0).all() and x.ss_bps.notna().any(), "g63"
        assert (x.side == np.where(x.dir == "bull", 1, -1)).all(), "g63 side"
        print("OK")


main()
