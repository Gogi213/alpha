#!/usr/bin/env python3
"""TK-493 F7 шаг 1 (ПК): полосы США / не-США, доля валидных минут относительно маски, колено Kneedle по каталогам
(биржа x полоса), метки 24/7 | сессия, разбиения для нуля A1. ТОЛЬКО формирование 01.10-14.10.

Выходы в data/xdl9/sel/ (gitignored) и малые таблицы в docs/findings/xdl9-selection-2026-11-06/:
  session_assets.csv   - по активам акций/ETF/индексов: решение США / не-США и причина
  contracts.csv        - все контракты теста: полоса, маска, доля, вошёл ли в каталог A (A1/A2/B1), в ноги C1/D2, причина
  catalogs.csv         - по каталогу (биржа x полоса): кандидатов, колено, порог, вошло, метка 24/7|сессия
  splits.csv           - разбиения A/B для эмпирического нуля (seed 37; 3 разбиения + 1 контрольное)
Правила и числа: см. отчёт docs/findings/xdl9-selection-2026-11-06.md; всё без источника - «назначено без Судьи, подтвердить».
"""
import re, sys, os, json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

SEED = 37
N_SPLITS = 4        # 3 для порога t* + 1 контрольное (G9 нулевой тест)


def nonus_tags(a):
    """Правила не-США по полям биржи / имени (не по данным): список причин."""
    why = []
    if a["exchange"] == "binance" and re.search(r"(HK|KR|CN)_EQUITY", a["class_src"]):
        why.append("binance:" + re.search(r"(HK|KR|CN)_EQUITY", a["class_src"]).group(0))
    if "нога в HKD" in a.get("reason", "") or re.match(r"^HK\d{4}USDT$", a["symbol"]):
        why.append("нога HKD")
    m = re.search(r"2(JPY|KRW|HKD|EUR|GBP|CNY|CNH|TWD|INR)-USDT$", a["symbol"]) if a["exchange"] == "bingx" else None
    if m:
        why.append("bingx:валюта " + m.group(1))
    return why


def main():
    rows, C, am = S.load_formation(lazy=True)
    n = C.shape[1]
    sess = S.session_mask(n)
    wkend = S.weekend_mask(n)
    chance = float(sess.sum()) / n            # доля минут кассы в неделе (3900 / 20160), не назначенное число
    sessR = sess[1:]

    # ---------------- контракты теста и доля дисперсии в кассе (для акций/ETF/индексов); по одной строке, без матриц float64
    cont = []
    for i, d in enumerate(rows):
        a = am[(d["exchange"], d["symbol"])]
        if a["role"] == "excluded":
            continue
        a = dict(a)
        a["i"] = i
        row = np.asarray(C[i], dtype=np.float64)
        fin = np.isfinite(row)
        a["n_valid_now"] = int(fin.sum())
        r = np.log(row[1:]) - np.log(row[:-1])
        vr = np.isfinite(r)
        a["n_ret"] = int(vr.sum())
        a["var_share"] = float("nan")
        if a["big"] in ("акции+ETF", "индексы") and a["n_ret"] >= S.N_RET_MIN:
            r2 = np.where(vr, r, 0.0) ** 2
            tot = r2.sum()
            if tot > 0:
                a["var_share"] = float(r2[sessR].sum() / tot)
        cont.append(a)

    # ---------------- активы: США / не-США
    by_asset = {}
    for a in cont:
        if a["big"] not in ("акции+ETF", "индексы"):
            continue
        by_asset.setdefault(a["asset_id"], []).append(a)
    asset_rows, nonus = [], {}
    for aid, lst in sorted(by_asset.items()):
        tags = sorted({t for a in lst for t in nonus_tags(a)})
        vs = [a["var_share"] for a in lst if a["var_share"] == a["var_share"] and a["role"] == "main"]
        med = float(np.median(vs)) if vs else float("nan")
        data_flag = bool(vs) and med < chance
        if tags:
            dec, why = "не-США", "; ".join(tags)
        elif data_flag:
            dec, why = "не-США", "данные: медиана доли дисперсии в кассе %.3f < %.4f" % (med, chance)
        else:
            dec = "США"
            why = "по умолчанию (П-37 §9)" if not vs else "данные: медиана %.3f >= %.4f" % (med, chance)
        nonus[aid] = dec == "не-США"
        asset_rows.append([aid, lst[0]["big"], ";".join(sorted({a["exchange"] for a in lst})), len(lst), len(vs),
                           "" if med != med else "%.4f" % med, "; ".join(tags), dec, why])
    S.write_csv(os.path.join(S.SEL, "session_assets.csv"),
                ["asset_id", "big", "exchanges", "contracts", "measured", "median_var_share_session", "tags", "decision", "why"],
                asset_rows)

    # ---------------- полоса, маска, доля
    for a in cont:
        us = (a["big"] in ("акции+ETF", "индексы")) and not nonus.get(a["asset_id"], False)
        a["us_mask"] = int(us)
        a["slug"] = S.slug_of(a, us)
        i = a["i"]
        fin = np.isfinite(np.asarray(C[i]))
        if us:
            a["share"] = float((fin & sess).sum()) / float(sess.sum())
            a["n_valid_masked"] = int((fin & sess).sum())
        else:
            a["share"] = float(fin.sum()) / n
            a["n_valid_masked"] = int(fin.sum())

    # ---------------- каталоги A (A1/A2/B1): основные контракты, колено доли по (биржа x полоса)
    cat = {}
    for a in cont:
        if a["a1a2b1"] != "1":
            continue
        cat.setdefault((a["exchange"], a["slug"]), []).append(a)
    cat_rows = []
    wk = {}
    for (ex, slug), lst in sorted(cat.items()):
        kn = S.kneedle([a["share"] for a in lst])
        thr = kn[1] if kn else None
        for a in lst:
            a["in_catalog_a"] = int(thr is None or a["share"] >= thr)
            a["catalog_reason"] = "" if a["in_catalog_a"] else "доля валидных минут %.4f < порога %.4f" % (a["share"], thr)
        # метка 24/7 | сессия: медиана выходного оборота (прокси: число валидных минут в выходные) по контрактам (биржа x укрупнённый класс)
        cat_rows.append([ex, slug, S.SLUG_BIG[slug], len(lst), "" if not kn else kn[0], "" if not kn else "%.6f" % thr,
                         "" if not kn else kn[2], sum(a["in_catalog_a"] for a in lst)])
    # метка 24/7 | сессия по (биржа x укрупнённый класс) на ВСЕХ основных контрактах класса (без маски)
    bigc = {}
    for a in cont:
        if a["a1a2b1"] == "1":
            bigc.setdefault((a["exchange"], a["big"]), []).append(a)
    label = {}
    for (ex, big), lst in sorted(bigc.items()):
        wv = [int((np.isfinite(np.asarray(C[a["i"]])) & wkend).sum()) for a in lst]
        label[(ex, big)] = "24/7" if float(np.median(wv)) > 0 else "сессия"
    for r in cat_rows:
        r.append(label[(r[0], r[2])])
    S.write_csv(os.path.join(S.SEL, "catalogs.csv"),
                ["exchange", "slug", "big", "candidates", "knee_index", "threshold_share", "knee_form", "included", "label_247"], cat_rows)

    # ---------------- контракты (A и ноги C1/D2)
    out = []
    for a in cont:
        out.append([a["exchange"], a["symbol"], a["asset_id"], a["role"], a["big"], a["slug"], a["us_mask"],
                    "%.5f" % a["share"], a["n_valid_masked"], a.get("in_catalog_a", 0), a["leg_c1d2"],
                    a.get("catalog_reason", ""), a["i"]])
    S.write_csv(os.path.join(S.SEL, "contracts.csv"),
                ["exchange", "symbol", "asset_id", "role", "big", "slug", "us_mask", "share", "n_valid_masked", "in_catalog_a",
                 "leg_c1d2", "catalog_reason", "row"], out)

    # ---------------- разбиения A/B для нуля (seed 37, один поток случайных чисел по отсортированным каталогам)
    rs = np.random.RandomState(SEED)
    sp = []
    for (ex, slug), lst in sorted(cat.items()):
        inc = sorted(a["symbol"] for a in lst if a["in_catalog_a"])
        if len(inc) < 4:
            continue
        for k in range(1, N_SPLITS + 1):
            perm = rs.permutation(len(inc))
            na = (len(inc) + 1) // 2
            half = ["A"] * len(inc)
            for rank, j in enumerate(perm):
                half[j] = "A" if rank < na else "B"
            for s_, h in zip(inc, half):
                sp.append([ex + "-" + slug, ex, slug, k, s_, h])
    S.write_csv(os.path.join(S.SEL, "splits.csv"), ["catalog", "exchange", "slug", "split", "symbol", "half"], sp)
    meta = dict(chance_session_share=chance, session_minutes=int(sess.sum()), seed=SEED, n_splits=N_SPLITS,
                n_contracts=len(cont), n_catalogs=len(cat), nonus_assets=sum(nonus.values()), assets=len(nonus))
    json.dump(meta, open(os.path.join(S.SEL, "catalog_meta.json"), "w"), indent=1)
    print(meta)


if __name__ == "__main__":
    main()
