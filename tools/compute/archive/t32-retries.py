#!/usr/bin/env python3
"""T-32 задача «retries» — вся стата по повторам (владелец, 27.09: «а много ваще у меня сетапов с
ретраями?»). Данные data/t32/main-trades.csv (главный вариант, без TRX, без потолка), связь
сделка -> стена из data/t32/retries-link.csv (Steam Deck, шаг 1), доля отскока по touch_index —
data/t32/retries-touchidx.json. Эпизоды просадки BTC — как в tools/compute/t32-epcap.py
(build_episodes/assign_episode, тот же порог btc_ret_4h_bps <= -44.55).

Группы (1): первый вход монеты в эпизоде / повтор после прибыльной сделки монеты В ЭТОМ ЖЕ эпизоде
(pnl>0) / повтор после стопа (reason=stop) / повтор после прочего убытка. Флаги (не завязаны на
эпизод): repeat_same_wall — предыдущая по времени сделка этой монеты (любой эпизод) была связана
с той же стеной (price_tick, birth_ms); repeat_same_wall_diff_episode — то же, но предыдущая сделка
была в другом эпизоде (или без эпизода).

KPI — только tools/compute/kpi-newhigh.py: month_metrics(closes,pk).hours.worst (старое
определение, дней с хвостом) и rolling_kpi(closes_augsep) (скользящий старт, p90/max «от
максимума», дней).

    python tools/compute/t32-retries.py --out data/t32/retries.json --summary data/t32/retries-summary.md
"""
import argparse
import datetime as dt
import importlib.util
import json
import os

TRADES = "data/t32/main-trades.csv"
LINK = "data/t32/retries-link.csv"
TOUCHIDX = "data/t32/retries-touchidx.json"
MS_H = 3_600_000
MS_MIN = 60_000

_lib_spec = importlib.util.spec_from_file_location(
    "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "__init__.py"))
_lib = importlib.util.module_from_spec(_lib_spec)
_lib_spec.loader.exec_module(_lib)


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_trades(path):
    # T-21 batch 2: было своим `open` + `csv.DictReader` — тот же дословный `_lib.read_csv` (шапка на
    # `#` в main-trades.csv не встречается, поведение не меняется).
    rows = []
    for r in _lib.read_csv(path)[1]:
        rows.append({"month": r["month"], "sym": r["sym"], "t0": int(r["t0_ms"]), "t1": int(r["t1_ms"]),
                     "pnl": float(r["pnl_usd"]), "reason": r["reason"]})
    return rows


def load_link(path):
    out = {}
    if not os.path.exists(path):
        return out
    for r in _lib.read_csv(path)[1]:
        key = (r["month"], r["sym"], int(r["t0_ms"]))
        out[key] = r
    return out


def day_str(t_ms):
    return dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def closes_of(trades, month=None):
    xs = trades if month is None else [t for t in trades if t["month"] == month]
    return [(t["t1"], t["pnl"]) for t in xs]


def kpi_both(kn, trades):
    """hours.worst (аug;sep, дней) и rolling p90/max от максимума (аug;sep, дней)."""
    m_aug = kn.month_metrics(closes_of(trades, "aug"), "aug")
    m_sep = kn.month_metrics(closes_of(trades, "sep"), "sep")
    roll = kn.rolling_kpi(closes_of(trades, None))
    return {
        "worst_days": {"aug": round(m_aug["hours"]["worst"] / 24, 1) if m_aug["hours"]["worst"] is not None else None,
                        "sep": round(m_sep["hours"]["worst"] / 24, 1) if m_sep["hours"]["worst"] is not None else None},
        "roll_p90_days": {"aug": round(roll["aug"]["max"]["p90"] / 24, 1), "sep": round(roll["sep"]["max"]["p90"] / 24, 1)},
        "roll_max_days": {"aug": round(roll["aug"]["max"]["max"] / 24, 1), "sep": round(roll["sep"]["max"]["max"] / 24, 1)},
        "usd": {"aug": m_aug["usd"], "sep": m_sep["usd"]}, "n": {"aug": m_aug["n"], "sep": m_sep["n"]},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default=TRADES)
    ap.add_argument("--link", default=LINK)
    ap.add_argument("--touchidx", default=TOUCHIDX)
    ap.add_argument("--out", default="data/t32/retries.json")
    ap.add_argument("--summary", default="data/t32/retries-summary.md")
    a = ap.parse_args()

    kn = load_mod("tools/compute/kpi-newhigh.py", "kn")
    epcap = load_mod("tools/compute/t32-epcap.py", "epcap")

    trades = load_trades(a.trades)
    link = load_link(a.link)
    touchidx = json.load(open(a.touchidx, encoding="utf-8")) if os.path.exists(a.touchidx) else None

    for t in trades:
        t["link"] = link.get((t["month"], t["sym"], t["t0"]))

    regime = epcap.load_regime(epcap.REGIME_DIRS)
    episodes = epcap.build_episodes(regime)
    for t in trades:
        t["ep"] = epcap.assign_episode(t["t0"], episodes)

    trades_sorted_all = sorted(trades, key=lambda t: t["t0"])
    by_sym = {}
    for t in trades_sorted_all:
        by_sym.setdefault(t["sym"], []).append(t)

    # previous trade of same symbol overall (any episode), and previous trade of same symbol in SAME episode
    for sym, ts in by_sym.items():
        prev_overall = None
        prev_in_ep = {}  # ep -> last trade seen with that ep
        for t in ts:
            t["prev_overall"] = prev_overall
            t["prev_in_episode"] = prev_in_ep.get(t["ep"])
            prev_overall = t
            prev_in_ep[t["ep"]] = t

    def same_wall(t1, t2):
        if t1 is None or t2 is None:
            return False
        l1, l2 = t1.get("link"), t2.get("link")
        if not l1 or not l2 or l1["status"] != "linked" or l2["status"] != "linked":
            return False
        return l1["price_tick"] == l2["price_tick"] and l1["birth_ms"] == l2["birth_ms"]

    for t in trades:
        p = t["prev_in_episode"]
        if p is None:
            t["group"] = "first_in_episode"
        elif p["pnl"] > 0:
            t["group"] = "repeat_after_profit"
        elif p["reason"] == "stop":
            t["group"] = "repeat_after_stop"
        else:
            t["group"] = "repeat_after_other_loss"
        po = t["prev_overall"]
        t["repeat_same_wall"] = same_wall(t, po)
        t["repeat_same_wall_diff_episode"] = t["repeat_same_wall"] and po is not None and po["ep"] != t["ep"]

    n_viewed = 0  # счётчик просмотренных вариантов (для раздела «испытания»)

    # ---------- 1. группы ----------
    DD_WINDOWS = [("10-19.08", ms("2026-08-10"), ms("2026-08-20")), ("22-31.08", ms("2026-08-22"), ms("2026-09-01"))]
    groups = ["first_in_episode", "repeat_after_profit", "repeat_after_stop", "repeat_after_other_loss"]
    g_stats = {}
    for g in groups:
        gt = [t for t in trades if t["group"] == g]
        rest = [t for t in trades if t["group"] != g]
        n_viewed += 1
        dd = {}
        for name, s, e in DD_WINDOWS:
            dd[name] = round(sum(t["pnl"] for t in gt if s <= t["t1"] < e), 2)
        g_stats[g] = {
            "n": len(gt), "usd": round(sum(t["pnl"] for t in gt), 2),
            "avg_usd": round(sum(t["pnl"] for t in gt) / len(gt), 4) if gt else None,
            "win_share": round(sum(1 for t in gt if t["pnl"] > 0) / len(gt), 3) if gt else None,
            "stop_share": round(sum(1 for t in gt if t["reason"] == "stop") / len(gt), 3) if gt else None,
            "dd_usd": dd, "kpi_without_group": kpi_both(kn, rest),
        }
    # доп: доли по флагам стены
    n_same_wall = sum(1 for t in trades if t["repeat_same_wall"])
    n_same_wall_diff_ep = sum(1 for t in trades if t["repeat_same_wall_diff_episode"])

    # ---------- 2. по номеру подхода / касания на входе ----------
    linked = [t for t in trades if t["link"] and t["link"]["status"] == "linked"]
    n_linked, n_ambig, n_unlinked = (sum(1 for t in trades if t["link"] and t["link"]["status"] == s)
                                      for s in ("linked", "ambiguous", "unlinked"))
    n_no_link_row = sum(1 for t in trades if not t["link"])

    def bucket_appr(n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        return n if n < 4 else "4+"

    def bucket_touch(n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        return n if n < 3 else "3+"

    appr_tab = {}
    for t in linked:
        b = bucket_appr(t["link"]["approach_number_at_entry"])
        if b is None:
            continue
        appr_tab.setdefault(b, []).append(t)
    appr_tab = {str(k): {"n": len(v), "usd": round(sum(x["pnl"] for x in v), 2),
                          "win_share": round(sum(1 for x in v if x["pnl"] > 0) / len(v), 3)}
                for k, v in appr_tab.items()}
    n_viewed += 1

    touch_tab = {}
    for t in linked:
        b = bucket_touch(t["link"]["touch_number_at_entry"])
        if b is None:
            continue
        touch_tab.setdefault(b, []).append(t)
    touch_tab_out = {}
    for k, v in touch_tab.items():
        key = str(k)
        bs = None
        if touchidx:
            b_aug = touchidx["counts"]["aug"].get(key)
            b_sep = touchidx["counts"]["sep"].get(key)
            bs = {"aug": b_aug["bounce_share"] if b_aug else None, "sep": b_sep["bounce_share"] if b_sep else None}
        touch_tab_out[key] = {"n": len(v), "usd": round(sum(x["pnl"] for x in v), 2),
                               "win_share": round(sum(1 for x in v if x["pnl"] > 0) / len(v), 3),
                               "bounce_share_from_touchidx": bs}
    n_viewed += 1

    # ---------- 3. время от прошлой сделки той же монеты ----------
    def gap_bucket(gap_ms):
        if gap_ms is None:
            return "first"
        m = gap_ms / MS_MIN
        if m < 15:
            return "<15min"
        if m < 60:
            return "15-60min"
        if m < 240:
            return "1-4h"
        return ">4h"

    gap_tab = {}
    for t in trades:
        po = t["prev_overall"]
        gap = (t["t0"] - po["t1"]) if po else None
        b = gap_bucket(gap)
        gap_tab.setdefault(b, []).append(t)
    gap_tab_out = {k: {"n": len(v), "usd": round(sum(x["pnl"] for x in v), 2),
                        "win_share": round(sum(1 for x in v if x["pnl"] > 0) / len(v), 3)}
                   for k, v in gap_tab.items()}
    n_viewed += 1

    # ---------- 4. серии стопов подряд по монете ----------
    streaks = []
    for sym, ts in by_sym.items():
        cur = []
        for t in ts:
            if t["reason"] == "stop":
                cur.append(t)
            else:
                if len(cur) >= 2:
                    streaks.append((sym, cur[:]))
                cur = []
        if len(cur) >= 2:
            streaks.append((sym, cur[:]))
    streaks.sort(key=lambda s: -len(s[1]))
    top_streaks = [{"sym": sym, "len": len(ts), "start": day_str(ts[0]["t0"]), "end": day_str(ts[-1]["t1"]),
                    "usd": round(sum(x["pnl"] for x in ts), 2)} for sym, ts in streaks[:10]]

    # ---------- 5. правила ----------
    rules = {}

    def rule_kpi(name, keep_fn):
        nonlocal n_viewed
        kept = [t for t in trades if keep_fn(t)]
        rules[name] = {"n_total": len(kept), **kpi_both(kn, kept)}
        n_viewed += 1

    for N in (30, 60, 120, 240):
        def keep(t, N=N):
            po = t["prev_overall"]
            if po is None or po["reason"] != "stop" or po["sym"] != t["sym"]:
                return True
            return (t["t0"] - po["t1"]) >= N * MS_MIN
        rule_kpi(f"no_entry_{N}min_after_stop_same_coin", keep)

    for K in (1, 2, 3):
        def keep(t, K=K):
            if not t["link"] or t["link"]["status"] != "linked":
                return True  # неизвестный номер -- не режем (оговорка)
            try:
                return int(t["link"]["approach_number_at_entry"]) <= K
            except (TypeError, ValueError):
                return True
        rule_kpi(f"only_first_{K}_approaches", keep)

    rule_kpi("no_repeat_after_stop_in_episode", lambda t: t["group"] != "repeat_after_stop")
    rule_kpi("no_repeat_same_wall", lambda t: not t["repeat_same_wall"])

    out = {
        "n_trades": len(trades), "n_variants_viewed": n_viewed,
        "link_coverage": {"linked": n_linked, "ambiguous": n_ambig, "unlinked": n_unlinked,
                           "no_link_row": n_no_link_row, "share_linked": round(n_linked / len(trades), 3)},
        "groups": g_stats, "n_repeat_same_wall": n_same_wall, "n_repeat_same_wall_diff_episode": n_same_wall_diff_ep,
        "by_approach_number": appr_tab, "by_touch_number": touch_tab_out, "by_gap_since_last_trade": gap_tab_out,
        "top_stop_streaks": top_streaks, "rules": rules,
        "caveats": ["занятость монеты и очередь входа при исключении сделок не пересчитаны (сделки просто выпадают "
                    "из счёта)", "доля связанных сделок (approach-линк) — см. link_coverage.share_linked",
                    "касание-линк (touch_number_at_entry) ещё реже: вход идёт на взводе подхода, а не на касании "
                    "(см. tools/compute/p02-variant-filter.py) -- touch_number_at_entry часто 0",
                    "правило K (только первые K подходов) не режет сделки без известного номера подхода",
                    "подбор порогов N/K не делался -- печатаются все просмотренные значения"],
    }
    json.dump(out, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    write_summary(a.summary, out)
    print(open(a.summary, encoding="utf-8").read())


def ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def write_summary(path, out):
    L = []
    lc = out["link_coverage"]
    L.append(f"# T-32 retries — стата по повторам (найдено на данных подбора, авг/сен; "
              f"просмотрено вариантов: {out['n_variants_viewed']})\n")
    L.append(f"Связь сделка->стена (approach): linked {lc['linked']}/{out['n_trades']} "
              f"({lc['share_linked']*100:.0f}%), ambiguous {lc['ambiguous']}, unlinked {lc['unlinked']}, "
              f"без строки {lc['no_link_row']}. Повтор от той же стены: {out['n_repeat_same_wall']} сделок "
              f"(из них в другом эпизоде: {out['n_repeat_same_wall_diff_episode']}).\n")
    L.append("## 1. Группы (в рамках эпизода просадки BTC)")
    L.append("| группа | n | $ | ср.сделка $ | win% | stop% | $ 10-19.08 | $ 22-31.08 | KPI без группы worst дн авг;сен | roll p90 дн авг;сен |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for g, s in out["groups"].items():
        k = s["kpi_without_group"]
        L.append(f"| {g} | {s['n']} | {s['usd']:+.0f} | {s['avg_usd']:+.2f} | {s['win_share']*100:.0f}% | "
                  f"{s['stop_share']*100:.0f}% | {s['dd_usd']['10-19.08']:+.0f} | {s['dd_usd']['22-31.08']:+.0f} | "
                  f"{k['worst_days']['aug']};{k['worst_days']['sep']} | {k['roll_p90_days']['aug']};{k['roll_p90_days']['sep']} |")
    L.append("")
    L.append("## 2. По номеру подхода/касания стены на входе (только связанные сделки)")
    L.append("подход (approach_number_at_entry): | " + " | ".join(f"{k}: n={v['n']} ${v['usd']:+.0f} win{v['win_share']*100:.0f}%"
                                                                     for k, v in sorted(out["by_approach_number"].items())))
    L.append("")
    def pct(x):
        return f"{x*100:.0f}%" if x is not None else "-"
    L.append("касание (touch_number_at_entry, редко >0 -- вход на взводе): | " +
              " | ".join(f"{k}: n={v['n']} ${v['usd']:+.0f} win{v['win_share']*100:.0f}% "
                          f"(отскок авг {pct(v['bounce_share_from_touchidx']['aug'])}, сен {pct(v['bounce_share_from_touchidx']['sep'])})"
                          if v["bounce_share_from_touchidx"] else f"{k}: n={v['n']} ${v['usd']:+.0f} win{v['win_share']*100:.0f}%"
                          for k, v in sorted(out["by_touch_number"].items())))
    L.append("")
    L.append("## 3. Время от прошлой сделки той же монеты")
    for k, v in out["by_gap_since_last_trade"].items():
        L.append(f"- {k}: n={v['n']} ${v['usd']:+.0f} win={v['win_share']*100:.0f}%")
    L.append("")
    L.append("## 4. Топ-10 серий стопов подряд по монете")
    L.append("| монета | длина | с | по | $ |")
    L.append("|---|---|---|---|---|")
    for s in out["top_stop_streaks"]:
        L.append(f"| {s['sym']} | {s['len']} | {s['start']} | {s['end']} | {s['usd']:+.0f} |")
    L.append("")
    L.append("## 5. Правила (без подбора, печатаются все просмотренные)")
    L.append("| правило | n (авг;сен) | $ авг | $ сен | worst дн авг;сен | roll p90 дн авг;сен | roll max дн авг;сен |")
    L.append("|---|---|---|---|---|---|---|")
    for name, r in out["rules"].items():
        L.append(f"| {name} | {r['n']['aug']};{r['n']['sep']} | {r['usd']['aug']:+.0f} | {r['usd']['sep']:+.0f} | "
                  f"{r['worst_days']['aug']};{r['worst_days']['sep']} | {r['roll_p90_days']['aug']};{r['roll_p90_days']['sep']} | "
                  f"{r['roll_max_days']['aug']};{r['roll_max_days']['sep']} |")
    L.append("")
    L.append("Оговорки: " + "; ".join(out["caveats"]) + ".")
    open(path, "w", encoding="utf-8", newline="").write("\n".join(L))


if __name__ == "__main__":
    main()
