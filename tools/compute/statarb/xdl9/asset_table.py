#!/usr/bin/env python3
"""TK-493 F5: таблица «один актив» (П-37 r2 §4.2) - asset_map.csv (контракт), asset_pairs.csv (пара). ТОЛЬКО формирование 01.10-14.10 UTC.

Вход: data/xdl9/inst/*.csv, data/xdl9/asset/*.{f32,idx.csv} (asset_extract.py), pairstats.csv (asset_pairs_stats.py),
      calib_summary.json (asset_calib.py) - порог S.
Правило решения R2' (см. отчёт xdl9-asset-map-2026-11-06.md, раздел «Отступление от r2»): пара - «одно», если n_ret >= n_ret_min и
S <= stab_threshold (середина зазора калибровки), на обоих путях (М и К); corr и dev выводятся, но НЕ служат воротами, потому что
калибровка r2 по ним не дала зазора (XAU против XAUT/PAXG).
Кластеры внутри биржи: жадно по убыванию оборота (HKD-ноги Bitget - после USD-подобных), основной = первый; остальные - дубли.
"""
import collections, csv, itertools, json, math, os, sys
import numpy as np
import asset_lib as L, asset_cands as A

N_RET_MIN = 300          # НАЗНАЧЕНО без Судьи, подтвердить
MULT_TOL = 0.02          # П-37 Н4
USD_LIKE = {"USD", "USDT", "USDC", "USD1", "U"}


def load_pairstats():
    out = {}
    with open(os.path.join(L.DATA, "pairstats.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            for k in ("i1", "i2", "n_common", "n_ret", "k"):
                r[k] = int(r[k])
            for k in ("corr", "med_ratio", "dev", "S", "mean_lr", "sd_lr", "sd_l1"):
                r[k] = float(r[k])
            out[(r["i1"], r["i2"])] = r
    return out


def stats_of(ps, i, j):
    return ps.get((i, j)) or ps.get((j, i))


def is_one(s, th):
    """Межбиржевая пара «одно»: S <= середина зазора калибровки (консервативно против ложных пар)."""
    return s is not None and s["n_ret"] >= th["n_ret_min"] and s["S"] <= th["stab"]


def is_dup(s, th):
    """Дубль внутри биржи: S ниже наименьшего S среди заведомо «разных» (th['stab_hi']): различие калибровкой не доказать.
    Консервативно в обратную сторону - лишний дубль безвреден, недосмотренный дубль даёт в A1 фиктивную пару «актив с собой»."""
    return s is not None and s["n_ret"] >= th["n_ret_min"] and s["S"] < th["stab_hi"]


def main():
    th = dict(n_ret_min=N_RET_MIN, mult_tol=MULT_TOL)
    cal = json.load(open(os.path.join(L.DATA, "calib_summary.json"), encoding="utf-8"))
    th["stab"] = cal["S"]["mid"]
    th["stab_hi"] = cal["S"]["best_diff"]
    th["stab_lo"] = cal["S"]["worst_same"]
    rows, C = L.load_contracts()
    A.build(rows, C)
    ps = load_pairstats()
    by_i = {d["i"]: d for d in rows}

    # ---------------- роли контрактов
    for d in rows:
        d["role"] = ""; d["reason"] = ""; d["asset_id"] = ""; d["main_i"] = -1
        d["dup_corr"] = d["dup_S"] = d["dup_ratio"] = float("nan"); d["dup_n_ret"] = 0
        d["dup_unverified"] = 0
    groups = collections.defaultdict(list)
    for d in rows:
        if d["big"] == L.OUT_OF_TEST:
            d["role"] = "excluded"
            d["reason"] = d["out_reason"]
        elif d["n_valid"] == 0:
            d["role"] = "excluded"; d["reason"] = "нет валидных минут на формировании (n_valid=0)"
        else:
            groups[(d["exchange"], d["key"], d["big"])].append(d)
    n_clusters_multi = 0
    for (ex, key, big), v in groups.items():
        v.sort(key=lambda d: (A.is_hkd(d), -(d["turnover"] if d["turnover"] == d["turnover"] else 0), d["i"]))
        clusters = []          # list of main contracts
        for d in v:
            placed = False
            for m in clusters:
                s = stats_of(ps, d["i"], m["i"])
                if s is None:
                    continue
                hkd = A.is_hkd(d) and not A.is_hkd(m)
                if is_dup(s, th) or (hkd and s["n_ret"] >= th["n_ret_min"]):
                    d["role"] = "dup"; d["main_i"] = m["i"]; d["asset_id"] = m["asset_id"]
                    d["dup_corr"], d["dup_S"], d["dup_ratio"], d["dup_n_ret"] = s["corr"], s["S"], s["med_ratio"], s["n_ret"]
                    d["reason"] = A.dup_reason(d, m) + ("" if is_dup(s, th) else " [по политике HKD r2 §4.2; S=%.3f выше порога дубля]" % s["S"])
                    placed = True
                    break
                elif s["n_ret"] < th["n_ret_min"] and len(clusters) == 1:
                    # измерить дубль нельзя: остаёмся с именем (дубль непроверенный), чтобы не торговать второй копией
                    d["role"] = "dup"; d["main_i"] = m["i"]; d["asset_id"] = m["asset_id"]; d["dup_unverified"] = 1
                    d["dup_corr"], d["dup_S"], d["dup_ratio"], d["dup_n_ret"] = s["corr"], s["S"], s["med_ratio"], s["n_ret"]
                    d["reason"] = A.dup_reason(d, m) + " [не проверено измерением: n_ret=%d < %d]" % (s["n_ret"], th["n_ret_min"])
                    placed = True
                    break
            if not placed:
                d["role"] = "main"
                d["asset_id"] = key + ("" if not clusters else "#%d" % (len(clusters) + 1)) + "|" + big
                if clusters:
                    d["reason"] = "второй актив с тем же именем на бирже (измерением не дубль основного)"
                    n_clusters_multi += 1
                clusters.append(d)
    # ---------------- ноги для C1/D2: основной + HKD-ноги (самостоятельная нога, r2 Н29)
    for d in rows:
        d["leg_c1d2"] = 1 if (d["role"] == "main" or (d["role"] == "dup" and A.is_hkd(d) and "нога в HKD" in d["reason"])) else 0
        d["a1a2b1"] = 1 if d["role"] == "main" else 0

    # ---------------- таблица пар
    pair_rows = []
    cnt = collections.Counter()
    for (i, j), s in ps.items():
        a, b = by_i[i], by_i[j]
        scope = "между биржами" if a["exchange"] != b["exchange"] else "внутри биржи"
        class_match = a["big"] == b["big"]
        if not class_match:
            if s["n_ret"] < th["n_ret_min"]:
                verdict = "класс не совпал (связь не измерена: мало общих доходностей)"
            else:
                verdict = "класс не совпал (связь есть)" if s["S"] <= th["stab"] else "класс не совпал (связи нет)"
        elif s["n_ret"] < th["n_ret_min"]:
            verdict = "нет данных (мало общих доходностей)"
        else:
            verdict = "одно" if s["S"] <= th["stab"] else ("пограничное (S между серединой и лучшим «разное»)" if s["S"] < th["stab_hi"] else "разное")
        path = "М" if s["dev"] <= th["mult_tol"] else "К"
        usable = (scope == "между биржами" and verdict == "одно" and a["leg_c1d2"] and b["leg_c1d2"])
        why = ""
        if scope == "между биржами" and verdict == "одно" and not usable:
            if a["role"] == "dup" or b["role"] == "dup":
                why = "нога - дубль: " + "; ".join(x for x in (
                    "%s:%s (%s)" % (a["exchange"], a["symbol"], a["reason"]) if a["role"] == "dup" else "",
                    "%s:%s (%s)" % (b["exchange"], b["symbol"], b["reason"]) if b["role"] == "dup" else "") if x)
        src = "имя" if not any(t.startswith(("alias_", "fx_pair", "cross_quote", "suffix_", "kraken_", "lighter_", "quote_BTC"))
                               for t in a["tags"] + b["tags"]) else "имя+словарь: " + ",".join(sorted(set(
                                   t for t in a["tags"] + b["tags"] if t.startswith(("alias_", "fx_pair", "cross_quote", "suffix_", "kraken_", "lighter_", "quote_BTC")))))
        in_gap = (cal["S"]["worst_same"] < s["S"] < cal["S"]["best_diff"])
        pair_rows.append(dict(
            key=s["key"], scope=scope, big1=a["big"], big2=b["big"], class_match=int(class_match),
            ex1=a["exchange"], sym1=a["symbol"], role1=a["role"], ex2=b["exchange"], sym2=b["symbol"], role2=b["role"],
            quote1=a["quote"], quote2=b["quote"],
            turnover1=a["turnover"], turnover2=b["turnover"], zero1=a["zero_share"], zero2=b["zero_share"],
            n_common=s["n_common"], zero_common=1.0 - s["n_common"] / L.N_MIN, n_ret=s["n_ret"], corr=s["corr"],
            med_ratio=s["med_ratio"], k=s["k"], dev=s["dev"], path=path, S=s["S"], sd_lr=s["sd_lr"], sd_l1=s["sd_l1"],
            verdict=verdict, in_calib_gap=int(in_gap), c1d2=int(usable), why=why, source=src))
        cnt[(scope, verdict)] += 1

    # ---------------- запись
    os.makedirs(L.DATA, exist_ok=True)
    mp = os.path.join(L.DATA, "asset_map.csv")
    mcols = ["exchange", "symbol", "base", "quote", "class", "class_src", "big", "key", "tags", "status", "listed_ms", "delisted_ms",
             "n_bars", "n_valid", "zero_share", "turnover", "turnover_cv", "price_med", "asset_id", "role", "reason", "main_symbol",
             "dup_corr", "dup_S", "dup_ratio", "dup_n_ret", "dup_unverified", "leg_c1d2", "a1a2b1"]
    with open(mp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(mcols)
        for d in rows:
            ms = by_i[d["main_i"]]["symbol"] if d["main_i"] >= 0 else ""
            w.writerow([d["exchange"], d["symbol"], d["base"], d["quote"], d["class"], d["class_src"], d["big"], d["key"],
                        "|".join(d["tags"]), d["status"], d["listed_ms"], d["delisted_ms"], d["n_bars"], d["n_valid"],
                        "%.4f" % d["zero_share"], "%.6g" % d["turnover"] if d["turnover"] == d["turnover"] else "",
                        "%.6g" % d["turnover_cv"], "%.6g" % d["price_med"] if d["price_med"] == d["price_med"] else "",
                        d["asset_id"], d["role"], d["reason"], ms,
                        "%.4f" % d["dup_corr"] if d["dup_corr"] == d["dup_corr"] else "",
                        "%.4f" % d["dup_S"] if d["dup_S"] == d["dup_S"] else "",
                        "%.6g" % d["dup_ratio"] if d["dup_ratio"] == d["dup_ratio"] else "", d["dup_n_ret"], d["dup_unverified"],
                        d["leg_c1d2"], d["a1a2b1"]])
    pp = os.path.join(L.DATA, "asset_pairs.csv")
    pcols = list(pair_rows[0].keys())
    pair_rows.sort(key=lambda r: (r["key"], r["scope"], r["ex1"], r["ex2"], r["sym1"], r["sym2"]))
    with open(pp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=pcols)
        w.writeheader()
        for r in pair_rows:
            rr = dict(r)
            for k in ("zero1", "zero2", "zero_common", "corr", "dev", "S", "sd_lr", "sd_l1"):
                rr[k] = "%.5f" % rr[k] if rr[k] == rr[k] else ""
            for k in ("turnover1", "turnover2", "med_ratio"):
                rr[k] = "%.6g" % rr[k] if rr[k] == rr[k] else ""
            w.writerow(rr)
    json.dump(dict(th=th, counts={"%s|%s" % k: v for k, v in cnt.items()}, n_clusters_multi=n_clusters_multi),
              open(os.path.join(L.DATA, "table_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("contracts", len(rows), "roles", collections.Counter(d["role"] for d in rows))
    print("pairs", len(pair_rows))
    for k, v in sorted(cnt.items()):
        print(k, v)
    print("usable C1/D2 pairs:", sum(r["c1d2"] for r in pair_rows))


if __name__ == "__main__":
    main()
