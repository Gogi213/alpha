"""П-42 TK-602: графики и таблицы отчёта «форма эквити» из shape.json (tk602_shape.py) и trades-<сторона>-<окно>.csv.

Использование: <venv>/bin/python tk602_report.py <каталог выхода tk602_shape.py> <каталог для png и tables.md>
Пишет: eq-<сторона>-<окно>.png (эквити по сделкам, рывки и спады отмечены полосами; под ней — P&L по суткам, столбцы), tables.md (таблицы для отчёта).
Цвета: рывок (5 лучших суток) — синий, спад (5 худших) — оранжевый, остальные сутки — серый; те же цвета — на странице.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BLUE, ORANGE, GREY, INK, LINE = "#2a78d6", "#eb6834", "#c4c4cc", "#27272a", "#52525b"
SIDE = {"short": "Шорт", "long": "Лонг"}
WIN = {"ja": "июль + август 2026 (62 суток)", "fit": "окно подбора 15.09–13.11 (60 суток)"}
LINE_NAME = {"base": "база (все сделки)", "T1": "T1", "T2": "T2", "T4": "T4", "T5": "T5", "T6": "T6"}


def dstr(ms):
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return d.strftime("%d.%m")


def fmt(v, d=2, sign=False):
    if v is None:
        return "—"
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",").replace("-", "−")
    return ("+" + s) if sign and v > 0 else s


def pct(v, d=0):
    return "—" if v is None else fmt(v * 100, d) + " %"


def chart(doc, side, wid, tr, out):
    s = doc["sides"][side][wid]
    n, st = s["n_days"], s["start_ms"]
    d = np.array(s["daily"])
    up, dn = set(s["jump"]), set(s["drop"])
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True, gridspec_kw={"height_ratios": [2.1, 1], "hspace": 0.08})
    t = (tr["t"].to_numpy() - st) / 86_400_000
    eq = np.cumsum(tr["net_nf"].to_numpy())
    xs = np.concatenate([[0], np.repeat(t, 2)])
    ys = np.concatenate([[0], np.ravel(np.column_stack([np.concatenate([[0], eq[:-1]]), eq]))])
    for di in range(n):
        c = BLUE if di in up else ORANGE if di in dn else None
        if c:
            for a in (a1, a2):
                a.axvspan(di, di + 1, color=c, alpha=0.16, lw=0)
    a1.plot(xs, ys, color=INK, lw=1.6, solid_joinstyle="round")
    a1.axhline(0, color="#a1a1aa", lw=0.8, ls=(0, (3, 3)))
    a1.set_ylabel("накопленная прибыль, $")
    a1.set_title(f"{SIDE[side]} · база U1 · {WIN[wid]}: эквити по закрытию сделок", loc="left", fontsize=11, color=INK)
    a2.bar(np.arange(n) + 0.5, d, width=0.78, color=[BLUE if i in up else ORANGE if i in dn else GREY for i in range(n)])
    a2.axhline(0, color="#a1a1aa", lw=0.8)
    a2.set_ylabel("P&L суток, $")
    a2.set_ylim(min(d.min() * 1.32, -3), max(d.max() * 1.2, 3))
    step = 7
    a2.set_xticks(np.arange(0, n + 1, step))
    a2.set_xticklabels([dstr(st + k * 86_400_000) for k in range(0, n + 1, step)], fontsize=9)
    a2.set_xlim(0, n)
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
        a.grid(axis="y", color="#e4e4e7", lw=0.6)
        a.tick_params(labelsize=9, colors=LINE)
    for di in sorted(up | dn):
        c = BLUE if di in up else ORANGE
        a2.annotate(dstr(st + di * 86_400_000), (di + 0.5, d[di]), xytext=(0, 3 if d[di] >= 0 else -10), textcoords="offset points", ha="center", fontsize=7.5, color=LINE)
    fig.text(0.01, 0.005, "синяя полоса — 5 лучших суток (рывок), оранжевая — 5 худших (спад); сутки UTC по закрытию сделки; прибыль без фандинга", fontsize=8, color=LINE)
    fig.savefig(out, dpi=115, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def tables(doc):
    out = []
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            s = doc["sides"][side][wid]
            out.append(f"\n### {SIDE[side]} · {WIN[wid]}\n")
            out.append("| Линия | Сделок | Сумма, $ | На сделку, $ | Топ-1 суток | Топ-3 | Топ-5 | Топ-5 сделок | Топ-10 сделок | k80 суток (% окна) |")
            out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
            for k, m in s["measures"].items():
                def sh(key):
                    return pct(m[key + "_share"]) if m[key + "_share"] is not None else f"({pct(m[key + '_pos_share'])} пол.)"
                out.append(f"| {LINE_NAME[k]} | {m['n']} | {fmt(m['total'], 2, True)} | {fmt(m['mean_trade'], 3, True)} | {sh('top1d')} | {sh('top3d')} | {sh('top5d')} | {sh('top5t')} | {sh('top10t')} | {m['k80']} ({pct(m['k80_pct'])}) |")
            out.append("\n| Линия | R² прямой (перцентиль среди перестановок суток) | Суток без нового максимума | Самое длинное плато, сут. (перцентиль) | Макс. просадка, $ (сут.) | Суток +/−/пустых | Асимметрия суток | Эксцесс суток | Серии знаков: число, z, макс. + / − |")
            out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
            for k, m in s["measures"].items():
                nu = m["null_perm"]
                r = m["runs"]
                out.append(f"| {LINE_NAME[k]} | {fmt(m['r2'], 2)} ({fmt(nu['r2_pctile'], 0)}) | {m['nonew_days']} ({pct(m['nonew_pct'])}) | {m['longest_plateau']} ({fmt(nu['plateau_pctile'], 0)}) | {fmt(m['maxdd'], 1)} ({m['maxdd_days']}) | {m['days_pos']}/{m['days_neg']}/{m['days_no_trades']} | {fmt(m['day_skew'], 2)} | {fmt(m['day_kurt'], 1)} | {r['n_runs']}, {fmt(r['z'], 2)}, {r['longest_pos']} / {r['longest_neg']} |")
            m = s["measures"]["base"]
            out.append(f"\nБаза: среднее суток {fmt(m['day_mean'], 2, True)} $, σ {fmt(m['day_sd'], 2)} $, лучшие/худшие сутки {fmt(m['best_day'], 1, True)} / {fmt(m['worst_day'], 1, True)} $; "
                       f"доля 5 лучших суток в сумме положительных {pct(m['top5d_pos_share'])} (iid-нормальные сутки с тем же средним и σ: 5/50/95 % — "
                       f"{' / '.join(pct(x) for x in m['null_normal']['top5_pos_share_q'])}); перестановки: R² 5/50/95 % {' / '.join(fmt(x, 2) for x in m['null_perm']['r2_q'])}, "
                       f"плато {' / '.join(fmt(x, 0) for x in m['null_perm']['plateau_q'])} сут.")
            out.append("\nВиды выхода (сделок / сумма $): " + "; ".join(f"{k} {v['n']} / {fmt(v['sum'], 1, True)}" for k, v in s["kinds"].items()))
            out.append(f"\nP&L по 4-часовым окнам закрытия UTC (00–04, 04–08, …), $: " + ", ".join(fmt(v, 1, True) for v in s["by_4h"]["pnl"]) + " · сделок: " + ", ".join(str(v) for v in s["by_4h"]["n"]))
            rb = s["rho_btc_trade"]
            out.append(f"\nρ Спирмена (P&L сделки в бп) × ход BTC за сделку (+ = в пользу стороны): {fmt(rb['rho'], 3) if rb else '—'} (n = {rb['n'] if rb else '—'}, p = {fmt(rb['p'], 3) if rb else '—'})")
            # сутки
            for title, key in (("Рывки (5 лучших суток)", "jump"), ("Спады (5 худших суток)", "drop")):
                out.append(f"\n**{title}**\n")
                out.append("| Дата | P&L, $ | Сделок (в плюс) | Монет | Главная монета: $ (доля суток) | Выходы | Удерж. мед./макс., мин | Часы закрытия UTC | Макс. монет за 60 мин: n, $ | BTC сутки, % (размах, макс. час) | BTC за сделку, ср. % (доля в пользу) |")
                out.append("|---|--:|--:|--:|--|--|--:|--|--|--|--|")
                for di in s[key]:
                    x = s["marked"][str(di)]
                    ks = ", ".join(f"{k} {v}" for k, v in x["kinds"].items())
                    hs = ",".join(str(h) for h in x["close_h"])
                    out.append(f"| {dstr(x['date_ms'])} | {fmt(x['pnl'], 2, True)} | {x['n']} ({x['n_win']}) | {x['coins']} | {x['top_coin']}: {fmt(x['top_coin_pnl'], 1, True)} ({pct(x['top_coin_pnl'] / x['pnl']) if x['pnl'] else '—'}) | {ks} | {fmt(x['hold_med'], 1)} / {fmt(x['hold_max'], 0)} | {hs} | {x['cluster60_n']}, {fmt(x['cluster60_pnl'], 1, True)} | {fmt(x['btc_day'], 2, True)} ({fmt(x['btc_day_range'], 1)}; {fmt(x['btc_hour_max'], 2, True)} в {x['btc_hour_max_h']:02d}ч) | {fmt(x['btc_aligned_mean'], 3, True)} ({pct(x['btc_aligned_pos_share'])}) |")
            for title, key in (("10 лучших сделок", "top_trades"), ("10 худших сделок", "bottom_trades")):
                out.append(f"\n**{title}**\n")
                out.append("| Монета | Вход → закрытие UTC | Удерж., мин | Выход | Тейков | $ | бп | BTC за сделку, % |")
                out.append("|---|--|--:|--|--:|--:|--:|--:|")
                for r in s[key]:
                    f = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%d.%m %H:%M")
                    out.append(f"| {r['sym']} | {f(r['en'])} → {f(r['t'])} | {fmt(r['hold_min'], 1)} | {r['kind']} | {r['takes']} | {fmt(r['net_nf'], 2, True)} | {fmt(r['bps'], 0, True)} | {fmt(r['btc_aligned_pct'], 2, True)} |")
            # признаки
            out.append("\n**Признаки суток (до торговли): AUC для «рывок против прочих активных суток» и «спад против прочих», ρ Спирмена с P&L суток и с |P&L| (активные сутки)**\n")
            out.append("| Признак | медиана рывок / прочие | AUC рывок (p) | медиана спад / прочие | AUC спад (p) | ρ с P&L (p) | ρ с \\|P&L\\| (p) |")
            out.append("|---|--|--:|--|--:|--:|--:|")
            for k, name in doc["features_list"]:
                f = s["feat_tests"][k]
                j, d_ = f["jump"], f["drop"]
                out.append(f"| {name} | {fmt(j['med_grp'], 2) + ' / ' + fmt(j['med_rest'], 2) if j else '—'} | {fmt(j['auc'], 2) + ' (' + fmt(j['p'], 2) + ')' if j else '—'} | "
                           f"{fmt(d_['med_grp'], 2) + ' / ' + fmt(d_['med_rest'], 2) if d_ else '—'} | {fmt(d_['auc'], 2) + ' (' + fmt(d_['p'], 2) + ')' if d_ else '—'} | "
                           f"{fmt(f['rho_pnl']['rho'], 2) + ' (' + fmt(f['rho_pnl']['p'], 2) + ')' if f['rho_pnl'] else '—'} | {fmt(f['rho_abs']['rho'], 2) + ' (' + fmt(f['rho_abs']['p'], 2) + ')' if f['rho_abs'] else '—'} |")
    return "\n".join(out)


def extras(doc, src):
    """Сводка по четырём окнам: ход BTC на рывках и спадах, соседство отмеченных суток, окно 13–15 UTC, что даёт рывок и спад, экстремумы эквити, счёт сравнений признаков."""
    rng = np.random.default_rng(602)
    out = ["## Сводка по четырём окнам (база U1)\n"]
    # (а) ход BTC за сутки
    out.append("**Ход BTC за сутки на отмеченных сутках (|ход|, % и размах, %; среднее)**\n")
    out.append("| Сторона · окно | \\|ход\\| рывки | \\|ход\\| спады | \\|ход\\| прочие | размах рывки | размах спады | размах прочие | рывков / спадов с \\|ходом\\| выше медианы суток |")
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            s = doc["sides"][side][wid]
            n = s["n_days"]
            ab, rg = np.abs(np.array(s["btc_day"])), np.array(s["btc_range"])
            up, dn = s["jump"], s["drop"]
            ot = [i for i in range(n) if i not in up and i not in dn]
            med = np.median(ab)
            out.append(f"| {SIDE[side]} · {wid} | {fmt(ab[up].mean(), 2)} | {fmt(ab[dn].mean(), 2)} | {fmt(ab[ot].mean(), 2)} | {fmt(rg[up].mean(), 2)} | {fmt(rg[dn].mean(), 2)} | {fmt(rg[ot].mean(), 2)} | {int((ab[up] > med).sum())}/5 и {int((ab[dn] > med).sum())}/5 |")
    # (б) соседство отмеченных суток
    out.append("\n**Соседство: у скольких из 10 отмеченных суток (5 рывков + 5 спадов) есть другие отмеченные сутки в пределах ±3 суток** (случайное размещение 10 суток в окне: среднее и 5–95 %)\n")
    out.append("| Сторона · окно | наблюдено | случайно: среднее (5–95 %) | рывков с падением рядом (≤ 3 сут.) |")
    out.append("|---|--:|--:|--:|")
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            s = doc["sides"][side][wid]
            n = s["n_days"]
            up, dn = s["jump"], s["drop"]
            mk = np.array(sorted(up + dn))

            def cnt(a):
                return int(sum(1 for x in a if np.sum(np.abs(a - x) <= 3) > 1))
            sims = np.array([cnt(np.sort(rng.choice(n, 10, replace=False))) for _ in range(5000)])
            ud = sum(1 for u in up if any(abs(u - d) <= 3 for d in dn))
            out.append(f"| {SIDE[side]} · {wid} | {cnt(mk)} | {fmt(sims.mean(), 1)} ({int(np.percentile(sims, 5))}–{int(np.percentile(sims, 95))}) | {ud} из 5 |")
    # (в) окно 13–15 UTC
    out.append("\n**Закрытия 13:00–15:00 UTC (открытие американской сессии — 13:30): доля сделок и P&L окна, и P&L отмеченных суток из этого окна**\n")
    out.append("| Сторона · окно | сделок в 13–15 UTC | доля сделок | P&L в 13–15 UTC, $ | вне 13–15 UTC, $ | рывки: из 13–15 UTC / всего за 5 суток, $ | спады: из 13–15 UTC / всего за 5 суток, $ |")
    out.append("|---|--:|--:|--:|--:|--|--|")
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            s = doc["sides"][side][wid]
            tr = pd.read_csv(os.path.join(src, f"trades-{side}-{wid}.csv"))
            hh = ((tr["t"] - s["start_ms"]) // 3_600_000 % 24)
            w = (hh >= 13) & (hh < 15)

            def part(days):
                m = tr["dc"].isin(days)
                return f"{fmt(tr['net_nf'][m & w].sum(), 1, True)} / {fmt(tr['net_nf'][m].sum(), 1, True)}"
            out.append(f"| {SIDE[side]} · {wid} | {int(w.sum())} из {len(tr)} | {pct(w.mean())} | {fmt(tr['net_nf'][w].sum(), 1, True)} | {fmt(tr['net_nf'][~w].sum(), 1, True)} | {part(s['jump'])} | {part(s['drop'])} |")
    # (г) что даёт рывок и спад
    out.append("\n**Что даёт рывок и спад (по 5 суток; доля главной сделки — лучшей у рывка и худшей у спада — в P&L суток; кластер — не менее 5 монет, закрытых в пределах 60 мин)**\n")
    out.append("| Сторона · окно | рывки: суток с трейлом | рывки: монет в среднем | рывки: доля лучшей сделки | рывки: кластер | спады: суток со стопом | спады: монет в среднем | спады: доля худшей сделки | спады: кластер |")
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            s = doc["sides"][side][wid]
            U = [s["marked"][str(i)] for i in s["jump"]]
            D = [s["marked"][str(i)] for i in s["drop"]]
            out.append(f"| {SIDE[side]} · {wid} | {sum(1 for x in U if x['kinds'].get('trail'))}/5 | {fmt(np.mean([x['coins'] for x in U]), 1)} | {pct(np.mean([x['best_trade'] / x['pnl'] for x in U]))} | {sum(1 for x in U if x['cluster60_n'] >= 5)}/5 | "
                       f"{sum(1 for x in D if x['kinds'].get('stop'))}/5 | {fmt(np.mean([x['coins'] for x in D]), 1)} | {pct(np.mean([x['worst_trade'] / x['pnl'] for x in D]))} | {sum(1 for x in D if x['cluster60_n'] >= 5)}/5 |")
    # (д) экстремумы эквити
    out.append("\n**Эквити по концу суток: пик, дно, финал, $ (сутки пика)**\n")
    out.append("| Сторона · окно | пик | сутки пика | дно | финал |")
    out.append("|---|--:|--:|--:|--:|")
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            s = doc["sides"][side][wid]
            e = np.array(s["eq_day"])
            out.append(f"| {SIDE[side]} · {wid} | {fmt(e.max(), 1, True)} | {dstr(s['start_ms'] + int(e.argmax()) * 86_400_000)} | {fmt(e.min(), 1, True)} | {fmt(e[-1], 1, True)} |")
    # (д2) виды выхода: сколько и на сколько, удержание
    out.append("\n**Виды выхода: сделок, сумма $, среднее $, медиана удержания, мин (rt — возврат за уровень, trail — трейл после тейков, be — безубыток, stop — стоп)**\n")
    out.append("| Сторона · окно | вид | сделок | сумма, $ | среднее, $ | медиана удержания, мин |")
    out.append("|---|--|--:|--:|--:|--:|")
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            tr = pd.read_csv(os.path.join(src, f"trades-{side}-{wid}.csv"))
            for k in ("trail", "rt", "be", "stop"):
                g = tr[tr["kind"] == k]
                if len(g):
                    out.append(f"| {SIDE[side]} · {wid} | {k} | {len(g)} | {fmt(g['net_nf'].sum(), 1, True)} | {fmt(g['net_nf'].mean(), 2, True)} | {fmt(g['hold_min'].median(), 1)} |")
    # (е) признаки: счёт сравнений
    tot = hit = 0
    hits = []
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            for k, name in doc["features_list"]:
                f = doc["sides"][side][wid]["feat_tests"][k]
                for key, lab in (("jump", "AUC рывок"), ("drop", "AUC спад"), ("rho_pnl", "ρ с P&L"), ("rho_abs", "ρ с |P&L|")):
                    t = f[key]
                    if t:
                        tot += 1
                        if t["p"] < 0.05:
                            hit += 1
                            v = t["auc"] if "auc" in t else t["rho"]
                            hits.append(f"{SIDE[side]} · {wid}: {name} — {lab} {fmt(v, 2)} (p = {fmt(t['p'], 2)})")
    out.append(f"\n**Признаки: сравнений {tot}, с p < 0,05 — {hit}; по случайности при {tot} независимых сравнениях ожидается ≈ {fmt(tot * 0.05, 1)}**\n")
    out.extend("- " + h for h in hits)
    return "\n".join(out)


def main():
    src, dst = sys.argv[1:3]
    os.makedirs(dst, exist_ok=True)
    doc = json.load(open(os.path.join(src, "shape.json"), encoding="utf-8"))
    for side in ("short", "long"):
        for wid in ("ja", "fit"):
            tr = pd.read_csv(os.path.join(src, f"trades-{side}-{wid}.csv"))
            chart(doc, side, wid, tr, os.path.join(dst, f"eq-{side}-{wid}.png"))
    open(os.path.join(dst, "tables.md"), "w", encoding="utf-8").write(extras(doc, src) + "\n\n" + tables(doc))
    print("готово:", sorted(os.listdir(dst)))


if __name__ == "__main__":
    main()
