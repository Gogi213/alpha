#!/usr/bin/env python3
"""Данные KPI «до перехая» (В-120) для дашборда: `kpi-newhigh.py --out` → `--merge kpi=<файл>`.

    by_variant  ключ варианта страницы → период (aug/sep/augsep) → распределение (часы) + все периоды (для гистограммы)
    rating      топ-10 рейтинга и главный — строки таблицы «Ровность роста»
    curves      топ-3 и главный → период → [[мс, $ накопительно], …] по закрытиям

    python tools/compute/kpi-dash-data.py --kpi data/kpi/kpi-2026-09-27.json --closes-dir data/kpi \\
        --out data/titration-dashboard/kpi-dash.json
"""
import argparse
import glob
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = {"BTC 4 ч: трейл 1/1": "btc4h_trail", "BTC 4 ч: тейк 1,75 %": "btc4h_take", "Кандидат: трейл 1/1": "cand",
        "Без фильтра просадки": "nofilter", "Г-105 стоп перед стеной": "g105", "Г-85 вход от фронтрана": "g85",
        "Г-86 рыночный вход": "g86", "f-g08": "g08"}
PK = {"aug": "август", "sep": "сентябрь", "augsep": "август+сентябрь"}
EDGES = [0, 0.25, 1, 2, 3, 5, 7, 10, 1e9]  # корзины гистограммы, дни
LEV_MONTH = {"aug": "август", "sep": "сентябрь"}
LEV_EXACT = (1, 2, 3, 4, 5)  # прогоны portfolio-sim --size-mult (data/p05/protection-p05-lev-x{m}.json); ×4 — tmp-t29/p05-lev-x4.sh (CEO 27.09: без среднего ×3/×5)


def load_lev(dirpath, variant="главный"):
    """Плечо ×1..×5 при депозите $2500 (владелец 27.09: «не размер позиции а в плечах»), только читает готовые
    прогоны `data/p05/protection-p05-lev-x{1..5}.json` (portfolio-sim --size-mult одним счётом `tmp-t29/p05-lev.sh`,
    ×4 — `tmp-t29/p05-lev-x4.sh`): все клетки — один и тот же простой масштаб позиции, без оценок."""
    rows = {}
    for m in LEV_EXACT:
        p = os.path.join(dirpath, f"protection-p05-lev-x{m}.json")
        if not os.path.exists(p):
            return None
        d = json.load(open(p, encoding="utf-8"))
        dep = d.get("deposit_usd") or 2500.0
        for g in d["grid"]:
            if g["variant"] != variant or g["epoch"] not in LEV_MONTH.values() or g["max_pos"] or g["kill"] or g["exclude"] != "нет" or g["day_stop"]:
                continue
            pk = next(k for k, v in LEV_MONTH.items() if v == g["epoch"])
            rows.setdefault(pk, {})[m] = {"usd": round(g["total_usd"], 2), "pct": round(g["total_pct"], 2),
                                          "dd_pct": round(g["dd_pct"], 2), "worst_day_pct": round(g["worst_day_pct"], 2),
                                          "peak_usd": round(g["peak_usd"], 1), "lev": round(g["peak_usd"] / dep, 2),
                                          "stress_pct": round(g["stress_pct"], 1) if g.get("stress_pct") is not None else None}
    return {"deposit_usd": 2500.0, "sizes": {m: 500 * m for m in (1, 2, 3, 4, 5)}, "rows": rows,
            "note_scale_invariant": "доля часов > 5 сут от масштаба не меняется — плечо ускоряет деньги, не перехай",
            "notes": ["простым масштабом позиции — точных прогонов --order-usd нет, крупная заявка иначе исполняется в очереди",
                      "на проверке у Судьи"]}


FAMILY_KEYS = [("age", "1_age"), ("size", "2_size"), ("gap", "3_gap"), ("outcome", "3_outcome"),
               ("before45", "4_before45"), ("traded_serial", "4_traded_serial"),
               ("btc", "5_btc"), ("episode_minute", "5_episode_minute"), ("family", "6_family")]
FAMILY_TITLE = {"traded_serial": "N x номер среди торгуемых (traded_serial, вместо номера от рождения)"}
# §7.x п.1: "часов под водой >$50" — busy-stress.json хранит только суточную `daily`-кривую, часовой ряд не
# сохранён; числа транскрибированы из docs/findings/retries-2026-09-27.md §7.x (approach-slices-summary.md
# §7.x п.1), гейт T-31 зелёный, на проверке у Судьи — не пересчитано здесь
UNDERWATER_GT50 = {"все": {"aug": 0.20, "sep": 0.0}, "первые 3": {"aug": 0.22, "sep": 0.0},
                    "первые 4": {"aug": 0.21, "sep": 0.0}, "с 4-го": {"aug": 0.0, "sep": 0.0}}
STRESS_DD_ROWS = [("все (номер известен, busy-replay)", "все"), ("первые 3 (busy-replay)", "первые 3"),
                   ("первые 4 (busy-replay)", "первые 4"), ("с 4-го и дальше (busy-replay)", "с 4-го")]
STRESS_LEV_ROWS = [("все (номер известен, busy-replay)", "все"), ("первые 3 (busy-replay)", "первые 3")]
# §7.x п.2: вклад сделок подхода 4+ в топ-3 эпизода просадки БАЗЫ — тоже не в json (approach-slices-summary.md
# §7.x п.2), порядок совпадает с busy-stress.json["top3_episodes"] (по убыванию глубины)
N4_CONTRIB = [{"n4": 10, "n_total": 87, "usd4": -13.7}, {"n4": 15, "n_total": 148, "usd4": -3.4},
              {"n4": 7, "n_total": 66, "usd4": -3.8}]


def slim_cell(c):
    n = c.get("n", 0)
    if not n:
        return {"n": 0, "note": c.get("note", "нет сделок")}
    return {"n": n, "usd_pt": round(c["usd"] / n, 3), "win": c.get("win"), "note": c.get("note", "")}


def slim_family(raw, key):
    """T-32 срезы по номеру подхода (владелец 27.09 через CEO): только читает `data/t32/approach-slices.json`
    (t32-approach-slices.py — не менять); клетки ужаты до n/$-за-сделку/доля-в-плюс для дашборда."""
    table = raw.get("table")
    if table is None:  # traded_serial — плоский, без корзин
        table = {"aug": {"": raw["aug"]}, "sep": {"": raw["sep"]}}
    return {"title": FAMILY_TITLE.get(key, raw.get("title", "")),
            "table": {pk: {bucket: {n: slim_cell(cell) for n, cell in cells.items()} for bucket, cells in table[pk].items()}
                      for pk in ("aug", "sep")}}


def load_slices(path):
    if not os.path.exists(path):
        return {}
    sl = json.load(open(path, encoding="utf-8"))
    return {"n_trades": sl["n_trades"], "n_cells": sl["n_cells_viewed"], "btc_tertile_cuts_aug": sl["btc_tertile_cuts_aug"],
            "families": {key: slim_family(sl[jk], key) for key, jk in FAMILY_KEYS}, "age_pooled": sl["1_pooled_1_vs_2plus_by_age"]}


def load_busy(path):
    """T-31 busy-replay (владелец 27.09: «после 4 подхода масштабируется просадка?»), только читает
    `data/t32/busy-stress.json` (t32-busy-stress.py — не менять)."""
    if not os.path.exists(path):
        return {}
    bs = json.load(open(path, encoding="utf-8"))
    pertrade = lambda v: round(v["usd"] / v["n"], 3) if v.get("n") else None
    rules_kpi = [{"rule": k, **{m: {**v[m], "usd_pt": pertrade(v[m])} for m in ("aug", "sep")}} for k, v in bs["rules_kpi"].items()]
    dd_rows = [{"rule": label, **{pk: {"dd_usd": round(bs["stress"][jk][pk]["dd_usd"], 1),
                                        "worst_day_usd": round(bs["stress"][jk][pk]["worst_day_usd"], 1),
                                        "underwater_gt50": UNDERWATER_GT50[label][pk]} for pk in ("aug", "sep")}}
               for jk, label in STRESS_DD_ROWS]
    lev_rows = [{"rule": label, **{pk: {k: round(bs["stress"][jk][pk][k], 2) for k in ("worst_day_pct", "dd_pct")}
                                    for pk in ("aug", "sep")} | {pk + "_stress": round(bs["stress"][jk][pk]["stress_pct"], 1) for pk in ("aug", "sep")}}
                for jk, label in STRESS_LEV_ROWS]
    top3 = [{"depth_usd": round(e["depth_usd"], 1), **N4_CONTRIB[i]} for i, e in enumerate(bs["top3_episodes"])]
    return {"rules_kpi": rules_kpi, "dd_rows": dd_rows, "lev_rows": lev_rows, "top3": top3,
            "stop_days_n4": {"together": bs["stop_days_n4_together"], "alone": bs["stop_days_n4_alone"]}}


def page_key(name):
    if name in PAGE:
        return PAGE[name]
    if name.startswith("П-05 ") and "потолок" not in name:
        return "p05:p05-" + name[5:]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kpi", required=True)
    ap.add_argument("--closes-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t32-dir", default="", help="data/t32: grid.json (хедж × ограничение) и exits.json (Г-102/103/108/110)")
    ap.add_argument("--retries-all", default="data/t32/retries-all.json",
                     help="владелец 27.09: «добавим количество подходов в дашборд» — t32-retries-all.py, не менять, только читать")
    ap.add_argument("--p05-lev-dir", default="data/p05", help="плечо ×1..×5 (владелец 27.09) — protection-p05-lev-x*.json, только читать")
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("kn", os.path.join(HERE, "kpi-newhigh.py"))
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)
    K = json.load(open(a.kpi, encoding="utf-8"))
    R, rank, main_name = K["results"], K["rank"], K["main"]
    S = kn.load(a.closes_dir)

    def closes_of(name):
        if name in S:
            return S[name][1]
        if name.startswith("смесь: "):
            parts = name[len("смесь: "):].split(" + ")
            if all(p in S for p in parts):
                return {pk: [(t, p / len(parts)) for n in parts for t, p in S[n][1][pk]] for pk in PK}
        return None

    by_variant = {}
    for name in S:
        key = page_key(name)
        if not key:
            continue
        by_variant[key] = {}
        for pk, (_n, d0, d1) in kn.PERIODS.items():
            ev = sorted(S[name][1][pk])
            hrs, tail, _ = kn.periods_to_high([(t / kn.MS_H, p) for t, p in ev], kn.ms(d0) / kn.MS_H, kn.ms(d1) / kn.MS_H)
            d = kn.dist(hrs, tail)
            by_variant[key][pk] = {**{k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()},
                                   "periods": [round(x, 1) for x in hrs]}
        # основное определение с 27.09 (CEO): скользящий старт по непрерывному счёту — статистика и гистограмма по часам t
        dd = kn.drawdown_stats(S[name][1]["augsep"])
        rl = kn.rolling_kpi(S[name][1]["augsep"], raw=True)
        for pk in ("aug", "sep"):
            raw = rl[pk].pop("raw_max")
            # rl[pk] с 27.09 (Судья 1d2a3f4) — не только вложенные словари ("max"/"start"), но и скальные
            # frac_gt_h/n_main/h_hours (главный показатель по H) — округлять только float, остальное копировать как есть
            rnd = lambda v: (round(v, 1) if isinstance(v, float) else v)
            by_variant[key][pk]["roll"] = {k: ({kk: rnd(vv) for kk, vv in v.items()} if isinstance(v, dict) else rnd(v))
                                           for k, v in rl[pk].items()}
            by_variant[key][pk]["dd"] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in dd[pk].items()}
            by_variant[key][pk]["roll_hist"] = [sum(EDGES[i] <= x / 24 < EDGES[i + 1] for x in raw) for i in range(len(EDGES) - 1)]
        # П-07 (Судья 3d3a5f6): вердикт «доля часов с ожиданием > 5 сут ≤ 0,10» + устойчивость — уже посчитан один раз
        # в kpi-newhigh.py (дорого — не для всех 285 рядов); здесь только переносится, если считался для этого ряда
        if name in R and "kpi07" in R[name]:
            by_variant[key]["kpi07"] = R[name]["kpi07"]
    cols = ("worst", "tail", "tw_p90", "median", "p90", "n")
    row = lambda n: {"name": n, "place": rank.index(n) + 1 if n in rank else None, "group": R[n]["group"], "key": page_key(n),
                     **{pk: {"usd": R[n][pk]["usd"], "n_trades": R[n][pk]["n"], "r2": R[n][pk]["r2"],
                             "plus_weeks": R[n][pk]["plus_weeks"], "episodes": R[n][pk]["episodes"],
                             **{c: R[n][pk]["hours"][c] for c in cols},
                             **({"roll_p90": R[n]["roll"][pk]["max"]["p90"], "roll_max": R[n]["roll"][pk]["max"]["max"],
                                 "roll_cens": R[n]["roll"][pk]["max"]["cens"], "fall_p90": R[n]["dd"][pk]["fall_p90"],
                                 "relows": R[n]["dd"][pk]["relows"]} if pk in R[n].get("roll", {}) else {})} for pk in PK}}
    rating = [row(n) for n in rank[:10]] + ([row(main_name)] if main_name not in rank[:10] else [])
    curves = {}
    for n in rank[:3] + [main_name]:
        cl = closes_of(n)
        if cl is None:
            continue
        curves[n] = {}
        for pk in PK:
            c, pts = 0.0, []
            for t, p in sorted(cl[pk]):
                c += p
                pts.append([t, round(c, 2)])
            curves[n][pk] = pts
    t32 = {}
    if a.t32_dir:
        g = json.load(open(os.path.join(a.t32_dir, "grid.json"), encoding="utf-8"))
        t32["grid"] = [{"hedge": r["hedge"], "limit": r["limit"], **{pk: {k: r[pk].get(k) for k in ("roll_p90_d", "roll_max_d", "worst_d", "usd", "n")}
                                                                      for pk in ("aug", "sep")}} for r in g.values()]
        ex = json.load(open(os.path.join(a.t32_dir, "exits.json"), encoding="utf-8"))
        rows = ex.get("results") or ex
        t32["exits"] = [{"name": k, **{pk: {"roll_p90_d": round(v["roll"][pk]["max"]["p90"] / 24, 1), "roll_max_d": round(v["roll"][pk]["max"]["max"] / 24, 1),
                                           "worst_d": round(v[pk]["hours"]["worst"] / 24, 1), "usd": v[pk]["usd"], "n": v[pk]["n"]} for pk in ("aug", "sep")}}
                        for k, v in rows.items() if isinstance(v, dict) and "roll" in v]
    approach = {}
    if a.retries_all and os.path.exists(a.retries_all):
        ra = json.load(open(a.retries_all, encoding="utf-8"))
        pertrade = lambda v: round(v["usd"] / v["n"], 3) if v["n"] else None
        by_number = [{"key": k, **{m: {**v[m], "usd_per_trade": pertrade(v[m])} for m in ("aug", "sep")}}
                     for k, v in ra["by_approach_number"].items()]
        by_number.append({"key": "без номера", **{m: {**ra["unknown_number"][m], "usd_per_trade": pertrade(ra["unknown_number"][m])} for m in ("aug", "sep")}})
        approach = {"n_trades": ra["n_trades"], "max_approach_number": ra["max_approach_number"], "tail_from": ra["tail_from"],
                    "by_number": by_number, "rules": ra["rules"], "n_rules": ra["n_rules"], "h_days": ra["h_days"],
                    "notes": ["на проверке у Судьи",
                              "сделки выпадают без пересчёта занятости монеты и очереди входа",
                              f"{ra['n_rules']} правил на тех же днях главного варианта — не проверка, не подбор",
                              "Г-85 — считается (размеченных сделок по номеру подхода для Г-85 пока нет, только для главного варианта)"]}
    if a.t32_dir:
        approach["slices"] = load_slices(os.path.join(a.t32_dir, "approach-slices.json"))
        approach["busy"] = load_busy(os.path.join(a.t32_dir, "busy-stress.json"))
    lev = load_lev(a.p05_lev_dir) if a.p05_lev_dir else None
    if lev is not None and main_name in R:
        lev["frac_gt_h"] = {pk: R[main_name]["roll"][pk]["frac_gt_h"] for pk in ("aug", "sep")}
    out = {"n_rows": len(R), "n_pass": len(rank), "by_variant": by_variant, "rating": rating, "curves": curves, "t32": t32,
           "approach": approach, "lev": lev, "edges_days": EDGES[:-1],
           "status": "находки на тех же днях подбора; клетки П-05 — на проверке у Судьи"}
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: вариантов {len(by_variant)}, строк рейтинга {len(rating)}, кривых {len(curves)}, "
          f"{os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
