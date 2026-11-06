#!/usr/bin/env python3
"""TK-493 F7 (ПК, numpy): диагностика отобранных пар A1, нулевой тест G9, список исключённых контрактов каталогов. Только формирование.

  a1_pairs_flags.csv  - для каждой отобранной пары статистика каждой ноги отдельно (ADF без лага и без константы по ряду ln-цены ноги, центрированному
                        по окну 02.10-14.10, максимум по двум половинам, как у пары): стационарная нога (привязанный к номиналу ETF) даёт «коинтеграцию»
                        с чем угодно. Порога нет: колонки для среза отчёта; факты о SGOV и BIL - в отчёте.
  (нулевой тест G9 - g9_null_test.csv - теперь пишет sel_fdr.py: контрольное разбиение 4 x три сдвига, в единицах баз и пар)
  catalog_excluded.csv- основные контракты, не вошедшие в каталог A (доля валидных минут ниже порога колена), с причиной.
"""
import csv, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

import math
LEN = S.EG_DAYS * S.MIN_DAY
MID = (math.ceil(LEN / 768) // 2) * 768          # как в sel_g9.py: form делит 21 блок пополам по 10
LO = S.EG_FROM


def adf_leg(u):
    """ADF лаг 0 без константы по ряду (ln-цена, NaN допустимы) на половинах окна EG (03.10-13.10); -> max(t_first, t_second) или nan."""
    ts = []
    for a, b in ((1, MID), (MID, len(u))):
        k = np.arange(max(a, 1), b)
        ok = np.isfinite(u[k]) & np.isfinite(u[k - 1])
        k = k[ok]
        n = len(k)
        if n < S.N_RET_MIN:
            return float("nan")
        mean = np.nanmean(u[np.arange(a if a > 0 else 0, b)])
        y, y1 = u[k] - mean, u[k - 1] - mean
        d = y - y1
        den = float(np.dot(y1, y1))
        if den <= 0:
            return float("nan")
        g = float(np.dot(d, y1) / den)
        res = d - g * y1
        s2 = float(np.dot(res, res) / (n - 1))
        if s2 <= 0:
            return float("-inf")
        ts.append(g / np.sqrt(s2 / den))
    return max(ts)


def main():
    rows, C, am = S.load_formation()
    ci = {(d["exchange"], S.safe(d["symbol"])): i for i, d in enumerate(rows)}
    con = {(r["exchange"], S.safe(r["symbol"])): r for r in S.read_csv(os.path.join(S.SEL, "contracts.csv"))}
    sess = S.session_mask(C.shape[1])

    def leg(ex, stem):
        v = np.log(C[ci[(ex, stem)]].astype(np.float64))
        if con[(ex, stem)]["us_mask"] == "1":
            v[~sess] = np.nan
        return v[LO:LO + LEN]

    out, per_cat = [], {}
    for r in S.read_csv(os.path.join(S.SEL, "a1_pairs.csv")):
        ex = r["catalog"].split("-", 1)[0]
        ty, tx = adf_leg(leg(ex, r["y"])), adf_leg(leg(ex, r["x"]))
        t = float(r["t"])
        out.append([r["catalog"], r["y"], r["x"], r["t"], "%.4f" % ty, "%.4f" % tx])
        c = per_cat.setdefault(r["catalog"], [0, 0])
        c[0] += 1; c[1] += int(min(ty, tx) < -10)
    S.write_csv(os.path.join(S.DOCS, "a1_pairs_flags.csv"), ["catalog", "y", "x", "t_pair", "t_leg_y", "t_leg_x"], out)
    # исключённые из каталога A
    ex_rows = []
    for k, r in sorted(con.items()):
        if r["role"] == "main" and r["in_catalog_a"] == "0":
            ex_rows.append([r["exchange"], r["symbol"], r["slug"], r["share"], r["catalog_reason"]])
    S.write_csv(os.path.join(S.DOCS, "catalog_excluded.csv"), ["exchange", "symbol", "slug", "share", "reason"], ex_rows)
    print("пар A1", len(out), "пар с ногой t_leg < -10 (справочно):", sum(v[1] for v in per_cat.values()), per_cat)
    print("исключено из каталогов A (ниже порога колена): %d" % len(ex_rows))


if __name__ == "__main__":
    main()
