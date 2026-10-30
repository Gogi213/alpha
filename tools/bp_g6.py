"""TK-458 B2 шаг G6.4: сессионная активность окон NY в крипте против контроля. Протокол docs/research/P-TK458-b2-g6-2026-10-30.md.
python -I tools/bp_g6.py <каталог csv.gz> <выход-префикс> [SYM ...]"""
import sys, glob, os, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

NY = ZoneInfo("America/New_York"); UTC = dt.timezone.utc
WIN = {"asia": (19, 7), "frankfurt": (2, 1), "london": (3, 5), "ny": (8, 3)}  # старт NY-час, длина ч


def hourly(path):
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4]); d.columns = ["t", "o", "h", "l", "c"]
    d["hr"] = d.t // 3600000
    g = d.groupby("hr").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"))
    a = np.full((int(g.index.max() - g.index.min()) + 1, 3), np.nan)
    a[(g.index - g.index.min()).values] = g.values
    return int(g.index.min()), a


def starts(d0, d1):
    out = []
    for day in pd.date_range(d0, d1):
        for w, (h, L) in WIN.items():
            base = day.date() if w != "asia" else (day - pd.Timedelta(days=1)).date()
            s = dt.datetime(base.year, base.month, base.day, h, tzinfo=NY).astimezone(UTC)
            out.append((w, day.date(), int(s.timestamp() // 3600), L))
    return out


def act(a, h0, s, L):
    i = s - h0
    if i < 0 or i + L > len(a): return np.nan
    x = a[i:i + L]
    if np.isnan(x).any(): return np.nan
    r = (x[:, 1].max() - x[:, 2].min()) / x[0, 0] * 1e4
    return np.log(r / np.sqrt(L)) if r > 0 else np.nan


def coin(path):
    sym = os.path.basename(path).split(".")[0]
    h0, a = hourly(path); rows = []
    for w, day, s, L in starts(pd.Timestamp(h0 * 3600, unit="s").date(), pd.Timestamp((h0 + len(a)) * 3600, unit="s").date()):
        v = act(a, h0, s, L)
        c = [act(a, h0, s + j, L) for j in range(L, 24 - L + 1)]
        if np.isnan(v) or not c or np.isnan(c).any(): continue
        rows.append((s * 3600000, sym, "1h*L", "def.session_window:" + w, v - float(np.mean(c))))
    return rows


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    fs = [f"{src}/{s}.csv.gz" for s in sys.argv[3:]] or sorted(glob.glob(src + "/*.csv.gz"))
    rows = [r for f in fs for r in coin(f)]
    pd.DataFrame(rows, columns=["ts_utc_ms", "coin", "tf", "def_id", "delta"]).to_csv(out + "-events.csv.gz", index=False)
    print(len(rows), "events")
