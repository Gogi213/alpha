"""П-42 §13.23 (TK-598): проверка цензуры `age_h` кэшем окна (без меток; читаются только признаки и дамп сигналов `sigsx`).

  python3 tk598_agecheck.py --feat-dir <каталог feat> --out <json>

`age_h = (t − first_bar·tf)/60` (`sigfeat_lib.py:265`), `first_bar` — самый ранний пивот группы уровня в сетке минуток окна (v21: `levels.rs`).
Строка «цензурирована кэшем», если `first_bar·60` не позже, чем через 7 суток от первой минуты с данными монеты в кэше окна. Классы:
`new_listing` — первая минута монеты > 0 и совпадает с датой первого файла минуток в полном архиве (монета листингована внутри окна);
`cache_start` — первая минута монеты = 0 (монета торговалась до начала сетки окна: возраст уровня обрезан началом кэша);
`other` — первая минута > 0, но дата первого файла архива иная (дыра в кэше / докачке).
По каждому (прогон, вариант, сторона): число строк, максимум и p99 `age_h`, строк с `age_h` > 14 суток, цензурированных по классам и числу монет.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os

import numpy as np
import pandas as pd

ARCHIVE = "/data/klines/binance"
PAST = ["/data/p42-past/klines", "/data/p42-past06/klines"]
# окно → (T0 сетки, каталог кэша минуток)
WIN = {
    "F-1": ("2026-09-01", "/data/p42-t566/cache80"),
    "jul-1": ("2026-06-17", "/data/p42-tk596/jul/cache"),
    "aug-1": ("2026-07-18", "/data/p42-tk596/aug/cache"),
    "hold-1": ("2026-10-31", "/data/p42-tk598/hold-in/cache"),
}
CENS_DAYS = 7


def first_valid_min(path):
    with open(path, "rb") as f:
        head = f.read(8)
    n = int.from_bytes(head[4:8], "little")
    o = np.memmap(path, dtype="<i8", mode="r", offset=8, shape=(n,))
    nz = np.flatnonzero(o != 0)
    return int(nz[0]) if len(nz) else -1


def archive_first_date(sym):
    """Самая ранняя дата минутных свечей монеты по всем деревьям полного архива: сентябрь–ноябрь (`klines`), 18.07–31.08 (`p42-past`), июнь–17.07 (`p42-past06`)."""
    ds = [os.path.basename(p)[:10] for p in glob.glob(f"{ARCHIVE}/{sym}/20??-??-??.csv")]
    for root in PAST:
        ds += [os.path.basename(p)[len(sym) + 4:len(sym) + 14] for p in glob.glob(f"{root}/{sym}/{sym}-1m-20??-??-??.zip")]
    return min(ds) if ds else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feat-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = {}
    fv, af = {}, {}
    for fp in sorted(glob.glob(os.path.join(a.feat_dir, "feat-*-*_u*.parquet"))):
        base = os.path.basename(fp)[len("feat-"):-len(".parquet")]
        run, variant = base.rsplit("-", 1)
        if run not in WIN:
            continue
        t0, cache = WIN[run]
        side = "long" if variant.startswith("L") else "short"
        feat = pd.read_parquet(fp, columns=["sym", "level", "sig_ms", "age_h"])
        sx = pd.read_csv(os.path.join(a.feat_dir, f"sigsx-{run}-{variant}.csv"), usecols=["sym", "level", "sig_ms", "first_bar"])
        j = feat.merge(sx, on=["sym", "level", "sig_ms"], how="left")
        if j["first_bar"].isna().any():
            raise SystemExit(f"{base}: нет first_bar у {int(j['first_bar'].isna().sum())} строк")
        t0d = dt.date.fromisoformat(t0)
        cls = []
        for sym, fb in zip(j["sym"], j["first_bar"].astype(np.int64)):
            key = (run, sym)
            if key not in fv:
                fv[key] = first_valid_min(os.path.join(cache, f"{sym}.b42"))
                af[key] = archive_first_date(sym)
            m = fv[key]
            if m < 0 or fb * 60 - m > CENS_DAYS * 1440:
                cls.append("")
                continue
            if m == 0:
                cls.append("cache_start")
            else:
                d = (t0d + dt.timedelta(days=m // 1440)).isoformat()
                cls.append("new_listing" if af[key] == d else "other")
        j["cls"] = cls
        age = j["age_h"].to_numpy(float)
        r = {"rows": int(len(j)), "age_h_max": float(np.nanmax(age)), "age_h_p99": float(np.nanpercentile(age, 99)),
             "rows_age_gt_14d": int((age > 14 * 24).sum()), "coins": int(j["sym"].nunique()), "censored_rows": int((j["cls"] != "").sum())}
        for c in ("new_listing", "cache_start", "other"):
            m = j["cls"] == c
            r[c] = {"rows": int(m.sum()), "coins": int(j.loc[m, "sym"].nunique()),
                    "age_h_max": float(j.loc[m, "age_h"].max()) if m.any() else None}
            if c != "new_listing" and m.any():
                r[c]["coins_list"] = sorted(j.loc[m, "sym"].unique().tolist())[:40]
        res.setdefault(run, {}).setdefault(side, {})[variant] = r
        print(run, side, variant, json.dumps({k: v for k, v in r.items()}, ensure_ascii=False, default=str)[:600], flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
