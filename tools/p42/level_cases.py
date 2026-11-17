"""П-42 TK-587: разбор кейсов владельца (PUMP, MANA — «уровни или зоны», SOPH — «вышли под уровень, почему остались») по сырым минуткам
и ленте сделок. Движок не меняется. Запуск на srv:

  python3 level_cases.py <detail.jsonl> <trace_dir> <out_dir>

Читает: `*-detail.jsonl` клетки `ow_e` (long; MINIMAX short — путь рядом `short-detail.jsonl` не нужен, всё берётся из trace_dir);
`level_trace.py` уже выполнен (trace_dir: <СИМВОЛ>-<sig_min>-{trace.json,tape.npz}).
Пишет в out_dir: facts-<ключ>.json, <ключ>-1h.png, <ключ>-zone.png, <ключ>-ticks.png, SOPH-ticks.csv, MINIMAX-takes.png, MINIMAX-ticks.csv."""
import csv
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import charts as C  # noqa: E402
import levport as L  # noqa: E402
from level_trace import Pos, frac  # noqa: E402

CASES = {
    "PUMP": dict(sym="PUMPUSDT", sig_min=42789),
    "MANA": dict(sym="MANAUSDT", sig_min=48329),
    "SOPH": dict(sym="SOPHUSDT", sig_min=79233),
}
TAU, E_ZONE = 0.002, 0.003
INK, INK2 = C.INK, C.INK2
AMBER = "#8a6d00"
BLUE = "#2a5db0"


def tm(m, secs=False):
    """Минута с 01.09 → «дд.мм ЧЧ:ММ» GMT+4."""
    return datetime.fromtimestamp((L.T0 + m * 60_000 + L.GMT4) / 1000, tz=timezone.utc).strftime("%d.%m %H:%M" + (":%S" if secs else ""))


def tms(ms_abs):
    d = datetime.fromtimestamp((ms_abs + L.GMT4) / 1000, tz=timezone.utc)
    return d.strftime("%d.%m %H:%M:%S.") + "%03d" % (ms_abs % 1000)


def load_case(key, detail_path):
    cs = CASES[key]
    r = None
    for line in open(detail_path, encoding="utf-8"):
        d = json.loads(line)
        if d["sym"] == cs["sym"] and d["sig_min"] == cs["sig_min"]:
            r = d
    m = L.load_min(cs["sym"])
    bars = L.make_bars(m)
    ev = []
    levels = L.build_levels(bars, 1, ev=ev)
    lv = [x for x in levels if x["born"] == r["born"] and x["first_bar"] == r["first_bar"]][0]
    diag = []
    L.find_pivots(L.Frame(bars, 1), diag=diag)
    return dict(key=key, sym=cs["sym"], sm=cs["sig_min"], r=r, m=m, bars=bars, levels=levels, ev=ev, lv=lv, diag=diag)


def piv_info(cs, bar):
    bars, m = cs["bars"], cs["m"]
    ph = int(bars["h"][bar])
    mins = [t for t in range(bar * 60, bar * 60 + 60) if m["o"][t] and int(m["h"][t]) == ph]
    thr = ph - L.td(ph * 500, 100_000)
    jb = next(j for j in range(bar + 1, len(bars["ok"])) if bars["ok"][j] and int(bars["l"][j]) <= thr)
    return dict(bar=bar, hour=tm(bar * 60), price_e8=ph, high_minute=tm(mins[0], True), high_min=mins[0],
                nb_highs_e8={str(bar + j): int(bars["h"][bar + j]) for j in (-2, -1, 1, 2)},
                bounce_bar=jb, bounce_bar_hour=tm(jb * 60), bounce_low_e8=int(bars["l"][jb]), bounce_pct=round((ph - int(bars["l"][jb])) / ph * 100, 3))


def facts(cs, tr):
    r, m, bars, lv, sm = cs["r"], cs["m"], cs["bars"], cs["lv"], cs["sm"]
    lvl = r["level_px_e8"]
    mem = L.members_at(lv, sm)
    mean = L.td(sum(p for _, p in mem), len(mem))
    f = dict(sym=cs["sym"], key=cs["key"], level_e8=lvl, mean_e8=mean, members=[], born=lv["born"], born_t=tm(lv["born"]),
             end=lv["end"], end_t=tm(lv["end"]) if lv["end"] < L.N_MIN else None, segs=[(a, tm(a), b) for a, b in lv["segs"]], first_bar=lv["first_bar"])
    for bar, p in sorted(mem):
        pi = piv_info(cs, bar)
        pi["dev_bp"] = round((p - mean) / mean * 1e4, 2)
        f["members"].append(pi)
    ps = [p for _, p in mem]
    f["spread_bp"] = round((max(ps) - min(ps)) / mean * 1e4, 2)
    f["spread_over_2tau_pct"] = round(f["spread_bp"] / 40 * 100, 1)
    f["first_pivot_min"] = min(x["high_min"] for x in f["members"])
    f["first_pivot_t"] = tm(f["first_pivot_min"], True)
    f["drawn_before_birth_min"] = lv["born"] - f["first_pivot_min"]
    f["engine_born_vs_chart_start_h"] = round((lv["born"] - f["first_pivot_min"]) / 60, 2)
    # сигнал и вход
    em = int((r["entry_ms"] + C.T0 - L.T0) // 60_000)
    f["sig_t"] = tms(r["sig_ms"] + C.T0)
    f["entry_t"] = tms(r["entry_ms"] + C.T0)
    f["entry_px_e8"] = r["entry_px_e8"]
    f["entry_dist_to_level_bp"] = round((lvl - r["entry_px_e8"]) / lvl * 1e4, 2)
    f["ezone_lo_e8"] = lvl - L.td(lvl * 300, 100_000)
    f["entry_after_birth_min"] = em - lv["born"]
    f["entry_minute_high_e8"] = int(m["h"][sm])
    f["entry_minute_high_vs_level_bp"] = round((int(m["h"][sm]) - lvl) / lvl * 1e4, 2)
    f["forbid"] = L.forbid_scan(m, lv, 1, upto=sm)
    if f["forbid"]:
        f["forbid"] = (tm(f["forbid"][0], True), f["forbid"][1])
    # пул
    nat = L.natr_series(m, 30)
    f["natr30_prev_pct"] = round(nat[sm - 1] / 1e6, 4)
    f["turnover_1h_usd"] = round(sum(int(x) for x in m["qv"][sm - 60 : sm]) / 1e5, 0)  # 60 закрытых минут ≤ t−1, как пул движка
    # свежесть: последнее касание полосы (high >= L − τ·L) в [max(first_bar·60, t−240), t)
    band = lvl - L.td(lvl * 200, 100_000)
    lo = max(lv["first_bar"] * 60, sm - 240)
    touches = [t for t in range(lo, sm) if m["o"][t] and int(m["h"][t]) >= band]
    f["fresh_last_touch_min_ago"] = (sm - touches[-1]) if touches else None
    f["fresh_touch_minutes_in_4h"] = len(touches)
    # живые уровни потолка в минуту сигнала
    alive = []
    for x in cs["levels"]:
        if x["born"] <= sm < x["end"]:
            fb = L.forbid_scan(m, x, 1, upto=sm)
            alive.append(dict(id=x["id"], price_e8=L.price_at(x, sm), born=tm(x["born"]), members=[(b, p) for b, p in L.members_at(x, sm)],
                              forbidden=(tm(fb[0], True), fb[1]) if fb else None, traded=x is lv))
    f["alive_ceilings_at_signal"] = alive
    # лента после входа
    t, p, bm = tr["_tape"]
    T = tr["T"]
    mk = t > T
    tt, pp = t[mk], p[mk]
    f["max_after_entry_e8"] = int(pp.max())
    f["max_after_entry_t"] = tms(int(tt[np.argmax(pp)]))
    f["max_vs_level_bp"] = round((int(pp.max()) - lvl) / lvl * 1e4, 2)
    f["max_vs_entry_bp"] = round((int(pp.max()) - r["entry_px_e8"]) / r["entry_px_e8"] * 1e4, 2)
    f["natr_pct"] = round(r["natr_e8"] / 1e6, 4)
    f["take1_e8"] = tr["tps"][0]
    f["take1_vs_entry_bp"] = round((tr["tps"][0] - r["entry_px_e8"]) / r["entry_px_e8"] * 1e4, 2)
    f["trail_arm_e8"] = r["entry_px_e8"] + tr["a_dist"]
    f["stop_e8"] = tr["stop_level"]
    iu = np.where(pp >= lvl)[0]
    f["first_tick_ge_level_t"] = tms(int(tt[iu[0]])) if len(iu) else None
    f["ticks_ge_level"] = int(len(iu))
    above = pp >= lvl
    ch = np.where(np.diff(above.astype(int)) != 0)[0]
    f["level_crossings"] = int(len(ch))
    if len(ch):
        lc = ch[-1] + 1
        f["last_cross_t"] = tms(int(tt[lc]))
        f["last_cross_dir"] = "вверх" if above[lc] else "вниз"
        f["last_cross_px_e8"] = int(pp[lc])
        f["last_tick_ge_level_t"] = tms(int(tt[iu[-1]])) if len(iu) else None
    f["exit_t"] = tms(r["exit_ms"] + C.T0)
    f["hold_s"] = round((r["exit_ms"] - r["entry_ms"]) / 1000, 1)
    f["exit_kind"] = r["kind"]
    f["net_usd"] = round(r["net"], 2)
    f["ticks_after_entry"] = int(len(pp))
    # часовые максимумы у уровня за 7 сут. до сигнала и почему они не пивоты
    sb = sm // 60
    diag = {d["bar"]: d for d in cs["diag"]}
    near = []
    for b in range(sb - 7 * 24, sb + 1):
        if not bars["ok"][b]:
            continue
        h = int(bars["h"][b])
        if h < lvl * 0.98 or h > lvl * 1.03:
            continue
        if b in diag:
            why = diag[b]["status"]
        else:
            nb = max(((int(bars["h"][b + j]), b + j) for j in (-2, -1, 1, 2) if 0 <= b + j < len(bars["h"])), default=(0, 0))
            if nb[0] >= h:
                why = f"не пивот k=2: у соседа (бар {nb[1]}, {tm(nb[1] * 60)}) high {nb[0] / 1e8:.8g} ≥"
            else:
                why = "не пивот k=2: соседний бар без данных"
        near.append(dict(bar=b, hour=tm(b * 60), high_e8=h, vs_level_bp=round((h - lvl) / lvl * 1e4, 1), status=why,
                         in_level=any(b == x["bar"] for x in f["members"])))
    f["near_level_hours"] = near
    # засчитанные пивоты ±4 % от уровня за окно L: ближайший сосед по цене
    cnt = [(d["bar"], d["p"]) for d in cs["diag"] if d["status"].startswith("засчитан") and sb - 7 * 24 <= d["bar"] <= sb]
    pool = []
    for b, p in cnt:
        if abs(p - lvl) > lvl * 0.04:
            continue
        others = [(abs(p - q) / ((p + q) / 2) * 1e4, bb, q) for bb, q in cnt if bb != b]
        near_o = min(others) if others else None
        pool.append(dict(bar=b, hour=tm(b * 60), price_e8=p, vs_level_bp=round((p - lvl) / lvl * 1e4, 1),
                         nearest_other_bar=near_o[1] if near_o else None, nearest_other_spread_bp=round(near_o[0], 1) if near_o else None,
                         in_level=any(b == x["bar"] for x in f["members"])))
    f["counted_pivots_pm4pct"] = pool
    f["n_counted_pivots_7d"] = len(cnt)
    # события уровней за 7 сут. до сигнала
    f["events_7d"] = [(tm(t_), e) for t_, e in cs["ev"] if sm - 7 * 1440 <= t_ <= sm + 1440]
    return f


# ───────────────────────────── графики ─────────────────────────────

def setup():
    return C.setup_mpl()


def hh(m_):
    return m_ / 60.0  # ось X графика 1ч: часы с 01.09 00:00 UTC


def ticks_axis(ax, x0, x1, step_h=12, unit="h"):
    from matplotlib.ticker import FixedLocator, FuncFormatter
    base = (L.GMT4 // 3_600_000) % step_h  # метки на круглых часах GMT+4
    first = int(np.ceil((x0 + 4) / step_h) * step_h - 4)
    ts = list(range(first, int(x1) + 1, step_h))
    ax.xaxis.set_major_locator(FixedLocator(ts))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: tm(int(round(v * 60)))))
    ax.tick_params(axis="x", labelsize=8)


def chart_1h(cs, f, out):
    plt = setup()
    from matplotlib.lines import Line2D
    r, bars, lv, sm = cs["r"], cs["bars"], cs["lv"], cs["sm"]
    lvl = r["level_px_e8"] / 1e8
    sb = sm // 60
    xm_bar = (int((r["exit_ms"] + C.T0 - L.T0) // 60_000)) // 60
    b0, b1 = sb - 8 * 24, min(max(sb + 4, xm_bar + 2), sb + 14)
    idx = np.arange(b0, b1 + 1)
    ok = bars["ok"][b0 : b1 + 1]
    o, h, l, c = (bars[k][b0 : b1 + 1] / 1e8 for k in ("o", "h", "l", "c"))
    fig, ax = plt.subplots(figsize=(17, 7.2), dpi=80)
    x = idx + 0.5
    up = (c >= o) & ok
    ax.vlines(x[ok], l[ok], h[ok], color=np.where(up[ok], C.UP, C.DOWN), lw=0.9, zorder=3)
    ax.bar(x[ok], np.abs(c - o)[ok] + (h.max() - l.min()) * 0.0006, bottom=np.minimum(o, c)[ok], width=0.7, color=np.where(up[ok], C.UP, C.DOWN), zorder=3)
    emin = (r["entry_ms"] + C.T0 - L.T0) / 60_000
    # другие потолочные уровни, жившие в окне
    for x_ in cs["levels"]:
        if x_ is lv or x_["end"] <= b0 * 60 or x_["born"] >= b1 * 60:
            continue
        s0, s1 = max(x_["born"], b0 * 60), min(x_["end"], (b1 + 1) * 60)
        p = L.price_at(x_, max(s0, x_["born"])) / 1e8
        ax.hlines(p, hh(s0), hh(s1), color="#8f8d86", lw=1.1, ls=(0, (3, 2)), zorder=4)
        ax.text(hh(s0), p, f" ур.{x_['id']} {p:.8g}", fontsize=7, color=INK2, va="bottom", zorder=6)
    # проторгованный уровень: как знает движок (от рождения) и как рисует график (от 1-го пивота)
    born, fm = lv["born"], f["first_pivot_min"]
    ax.axhspan(lvl * (1 - TAU), lvl * (1 + TAU), xmin=0, xmax=1, color="#9a998f", alpha=0.0)
    ax.fill_between([hh(born), hh(emin)], lvl * (1 - TAU), lvl * (1 + TAU), color="#9a998f", alpha=0.22, lw=0, zorder=1)
    ax.hlines(lvl * (1 - E_ZONE), hh(born), hh(emin), color=INK2, lw=0.9, ls=(0, (6, 3)), zorder=4)
    ax.hlines(lvl, hh(born), hh(emin), color=INK, lw=2.6, zorder=6)
    ax.hlines(lvl, hh(fm), hh(born), color=AMBER, lw=2.6, ls=(0, (1.2, 1.6)), zorder=6)
    ax.axvline(hh(born), color=AMBER, lw=0.9, ls="-", alpha=0.7, zorder=2)
    # пивоты: засчитанные (кольцо), члены уровня (залитые), незасчитанные локальные максимумы (крестик)
    for d in cs["diag"]:
        if not (b0 <= d["bar"] <= b1):
            continue
        p = d["p"] / 1e8
        if d["status"].startswith("засчитан"):
            ax.plot([d["bar"] + 0.5], [p], marker="o", ms=5.5, mfc="white", mec=INK2, mew=0.9, ls="none", zorder=8)
        else:
            ax.plot([d["bar"] + 0.5], [p], marker="x", ms=5, mec="#b04a2a", mew=1.0, ls="none", zorder=8)
    for i, mb in enumerate(f["members"], 1):
        ax.plot([hh(mb["high_min"])], [mb["price_e8"] / 1e8], marker="o", ms=9, mfc=INK, mec="white", mew=1.0, ls="none", zorder=10)
        ax.annotate(f"П{i} {mb['price_e8'] / 1e8:.8g}\n{mb['high_minute'][:11]}", (hh(mb["high_min"]), mb["price_e8"] / 1e8), textcoords="offset points",
                    xytext=(-4, 9 if mb["dev_bp"] > 0 else -26), fontsize=8, color=INK, ha="right", zorder=11)
    ax.plot([emin / 60], [r["entry_px_e8"] / 1e8], marker="^", ms=11, mfc="#0a8f3c", mec="white", mew=1.0, ls="none", zorder=12)
    ax.annotate(f"вход {tms(r['entry_ms'] + C.T0)[:-4]}\n{r['entry_px_e8'] / 1e8:.8g}", (emin / 60, r["entry_px_e8"] / 1e8), textcoords="offset points",
                xytext=(-40, -80), fontsize=8, color=INK, zorder=11)
    xm = (r["exit_ms"] + C.T0 - L.T0) / 3_600_000
    ax.plot([xm], [r["legs"][-1][1] / 1e8], marker="s", ms=7, mfc=C.C_STOP, mec="white", mew=0.8, ls="none", zorder=12)
    ax.hlines(r["entry_px_e8"] / 1e8 * 0.95, emin / 60, xm, color=C.C_STOP, lw=1.0, ls=(0, (5, 3)), zorder=5)
    ax.set_xlim(b0, b1 + 1)
    ys = [h[ok].max(), l[ok].min(), lvl]
    pad = (max(ys) - min(ys)) * 0.06
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ticks_axis(ax, b0, b1 + 1, step_h=12)
    C.price_axis(ax)
    ax.set_xlabel("часовые свечи Binance USD-M, время GMT+4", loc="left", fontsize=9)
    ttl = (f"{cs['sym']} лонг · сигнал {f['sig_t'][:11]} · итог {r['net']:+.2f} $ · часовые бары: уровень по движку (порт levels.rs сверен), "
           f"пивоты {f['members'][0]['price_e8'] / 1e8:.8g} / {f['members'][1]['price_e8'] / 1e8:.8g}, разброс {f['spread_bp']:.1f} бп")
    fig.suptitle(ttl, x=0.01, ha="left", fontsize=11, fontweight="bold", color=INK)
    handles = [
        Line2D([0], [0], color=INK, lw=2.6, label=f"уровень (среднее пивотов) {lvl:.8g}, жив с рождения {tm(born)}"),
        Line2D([0], [0], color=AMBER, lw=2.6, ls=(0, (1.2, 1.6)), label="так рисует график сделки: от минуты 1-го пивота (до рождения)"),
        Line2D([0], [0], color="#9a998f", lw=8, alpha=0.4, label="допуск ±τ = ±0,2 % от цены уровня"),
        Line2D([0], [0], color=INK2, lw=0.9, ls=(0, (6, 3)), label="нижняя граница зоны входа E = 0,3 %"),
        Line2D([0], [0], color="#8f8d86", lw=1.1, ls=(0, (3, 2)), label="другие живые потолочные уровни окна"),
        Line2D([0], [0], marker="o", color="none", mfc=INK, mec="white", ms=9, label="пивоты этого уровня (минута максимума)"),
        Line2D([0], [0], marker="o", color="none", mfc="white", mec=INK2, ms=6, label="остальные засчитанные пивоты (k=2, отскок ≥ 0,5 %)"),
        Line2D([0], [0], marker="x", color="none", mec="#b04a2a", ms=6, label="локальный максимум k=2, пивотом не стал"),
        Line2D([0], [0], color=C.C_STOP, lw=1.0, ls=(0, (5, 3)), label="стоп −5 % от входа (единственное правило выхода, активное в сделке)"),
    ]
    fig.legend(handles=handles, loc="lower center", fontsize=8, frameon=False, ncol=3)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.24)
    fig.savefig(out, dpi=80)
    plt.close(fig)


def chart_zone(cs, f, out):
    plt = setup()
    from matplotlib.lines import Line2D
    r, m, lv, sm = cs["r"], cs["m"], cs["lv"], cs["sm"]
    lvl = r["level_px_e8"] / 1e8
    emin = int((r["entry_ms"] + C.T0 - L.T0) // 60_000)
    fm = f["first_pivot_min"]
    m0, m1 = fm - 150, emin + 45
    t = np.arange(m0, m1 + 1)
    ok = m["o"][m0 : m1 + 1] != 0
    o, h, l, c = (m[k][m0 : m1 + 1] / 1e8 for k in ("o", "h", "l", "c"))
    fig, ax = plt.subplots(figsize=(17, 7.2), dpi=80)
    x = t + 0.5
    up = (c >= o) & ok
    col = np.where(up, C.UP, C.DOWN)
    ax.vlines(x[ok], l[ok], h[ok], color=col[ok], lw=0.8, zorder=3)
    ax.bar(x[ok], np.abs(c - o)[ok], bottom=np.minimum(o, c)[ok], width=0.8, color=col[ok], zorder=3)
    born = lv["born"]
    lo_y, hi_y = lvl * (1 - 0.014), lvl * (1 + 0.007)
    ax.fill_between([born, emin], lvl * (1 - TAU), lvl * (1 + TAU), color="#9a998f", alpha=0.22, lw=0, zorder=1)
    ax.fill_between([fm, born], lvl * (1 - TAU), lvl * (1 + TAU), color="#9a998f", alpha=0.10, lw=0, zorder=1)
    ax.hlines(lvl * (1 - E_ZONE), born, emin, color=INK2, lw=0.9, ls=(0, (6, 3)), zorder=4)
    ax.hlines(lvl, born, emin, color=INK, lw=2.6, zorder=6)
    ax.hlines(lvl, fm, born, color=AMBER, lw=2.6, ls=(0, (1.2, 1.6)), zorder=6)
    ax.axvline(born, color=AMBER, lw=1.0, alpha=0.8, zorder=2)
    ax.text(born + 3, hi_y - (hi_y - lo_y) * 0.03, f"уровень рождён {tm(born)}\n(движок узнал о нём здесь)", fontsize=8.5, color=AMBER, va="top", zorder=11)
    ax.text(fm + 3, hi_y - (hi_y - lo_y) * 0.03, f"1-й пивот {f['first_pivot_t'][:11]}\n(отсюда рисует график сделки)", fontsize=8.5, color=INK2, va="top", zorder=11)
    for i, mb in enumerate(f["members"], 1):
        ax.plot([mb["high_min"] + 0.5], [mb["price_e8"] / 1e8], marker="o", ms=10, mfc=INK, mec="white", mew=1.0, ls="none", zorder=10)
        ax.annotate(f"П{i} {mb['price_e8'] / 1e8:.8g} ({mb['dev_bp']:+.1f} бп от уровня)\n{mb['high_minute'][:11]}, часовой бар {mb['hour'][6:]}", (mb["high_min"] + 0.5, mb["price_e8"] / 1e8),
                    textcoords="offset points", xytext=(10, 8 if mb["dev_bp"] > 0 else -30), fontsize=8.5, color=INK, zorder=11)
    ax.plot([r["entry_ms"] / 60_000 + C.T0 / 60_000 - L.T0 / 60_000], [r["entry_px_e8"] / 1e8], marker="^", ms=12, mfc="#0a8f3c", mec="white", mew=1.0, ls="none", zorder=12)
    ax.annotate(f"вход {tms(r['entry_ms'] + C.T0)[:-4]}\n{r['entry_px_e8'] / 1e8:.8g} ({f['entry_dist_to_level_bp']:.1f} бп под уровнем)",
                (emin + 0.5, r["entry_px_e8"] / 1e8), textcoords="offset points", xytext=(-150, -36), fontsize=8.5, color=INK, zorder=11)
    ax.annotate("", xy=(fm + (born - fm) * 0.5, lvl * (1 + TAU)), xytext=(fm + (born - fm) * 0.5, lvl * (1 - TAU)),
                arrowprops=dict(arrowstyle="<->", color=INK2, lw=0.9), zorder=9)
    ax.text(fm + (born - fm) * 0.5 + 4, lvl * (1 - TAU) - (hi_y - lo_y) * 0.006, f"полоса ±τ: {2 * TAU * 100:.1f} % ширины, пивоты на её краях", fontsize=8, color=INK2, va="top", zorder=11)
    ax.set_xlim(m0, m1 + 1)
    ax.set_ylim(lo_y, hi_y)
    ticks_axis(ax, m0 / 60, (m1 + 1) / 60, step_h=2)
    C.price_axis(ax)
    ax.set_xlabel("минутные свечи, время GMT+4", loc="left", fontsize=9)
    fig.suptitle(f"{cs['sym']} · зона уровня: пивоты {f['members'][0]['price_e8'] / 1e8:.8g} и {f['members'][1]['price_e8'] / 1e8:.8g} (разброс {f['spread_bp']:.1f} бп), уровень = среднее {lvl:.8g}; "
                 f"график рисует линию на {f['drawn_before_birth_min'] // 60} ч {f['drawn_before_birth_min'] % 60:02d} мин раньше рождения", x=0.01, ha="left", fontsize=11, fontweight="bold", color=INK)
    handles = [Line2D([0], [0], color=INK, lw=2.6, label="уровень от рождения (движок)"),
               Line2D([0], [0], color=AMBER, lw=2.6, ls=(0, (1.2, 1.6)), label="линия графика до рождения"),
               Line2D([0], [0], color="#9a998f", lw=8, alpha=0.4, label="допуск ±0,2 %"),
               Line2D([0], [0], color=INK2, lw=0.9, ls=(0, (6, 3)), label="граница зоны входа E = 0,3 % под уровнем")]
    fig.legend(handles=handles, loc="lower center", fontsize=8.5, frameon=False, ncol=4)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.15)
    fig.savefig(out, dpi=80)
    plt.close(fig)


def chart_ticks(cs, f, tr, out):
    plt = setup()
    from matplotlib.lines import Line2D
    r = cs["r"]
    lvl = r["level_px_e8"] / 1e8
    t, p, bm = tr["_tape"]
    T = tr["T"]
    e = r["entry_px_e8"] / 1e8
    stop = tr["stop_level"] / 1e8
    take1 = tr["tps"][0] / 1e8
    xm = r["exit_ms"] + C.T0
    pr = p / 1e8
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(17, 9.2), dpi=80, gridspec_kw=dict(height_ratios=[1, 1]))
    zoom_end = T + (8 if cs["key"] == "SOPH" else 10) * 60_000
    for ax, (x0, x1, ttl) in ((a1, (T - 60_000, zoom_end, "первые минуты после входа, каждая сделка ленты")),
                              (a2, (T - 600_000, xm + 600_000, "вся сделка, каждая сделка ленты"))):
        mk = (t >= x0) & (t <= x1)
        xs = (t[mk] - T) / 1000.0
        ax.step(xs, pr[mk], where="post", color="#3b4350", lw=0.7 if ax is a1 else 0.5, zorder=4)
        ax.fill_between([(x0 - T) / 1000.0, (x1 - T) / 1000.0], lvl * (1 - TAU), lvl * (1 + TAU), color="#9a998f", alpha=0.22, lw=0, zorder=1)
        ax.hlines(lvl, (x0 - T) / 1000.0, (x1 - T) / 1000.0, color=INK, lw=2.0, zorder=5)
        ax.hlines(e, 0, (x1 - T) / 1000.0, color="#0a8f3c", lw=1.2, zorder=5)
        ax.hlines(take1, 0, (x1 - T) / 1000.0, color=C.C_TAKE, lw=1.0, ls=(0, (6, 3)), zorder=5)
        ax.hlines(stop, 0, (x1 - T) / 1000.0, color=C.C_STOP, lw=1.0, ls=(0, (5, 3)), zorder=5)
        ax.plot([0], [e], marker="^", ms=10, mfc="#0a8f3c", mec="white", mew=1, ls="none", zorder=9)
        ax.set_xlim((x0 - T) / 1000.0, (x1 - T) / 1000.0)
        ax.set_title(ttl, loc="left", fontsize=10, color=INK, fontweight="bold")
        C.price_axis(ax)
        ax.set_xlabel("секунд от входа", loc="left", fontsize=9)
    im = int(np.argmax(np.where(t > T, p, 0)))
    for ax in (a1, a2):
        ax.plot([(t[im] - T) / 1000.0], [pr[im]], marker="v", ms=9, mfc="#6250d6", mec="white", mew=1, ls="none", zorder=9)
    a1.annotate(f"макс. {pr[im]:.8g}\n{tms(int(t[im]))[6:-4]}", ((t[im] - T) / 1000.0, pr[im]), textcoords="offset points", xytext=(8, 8), fontsize=8.5, color=INK)
    a2.plot([(xm - T) / 1000.0], [r["legs"][-1][1] / 1e8], marker="s", ms=8, mfc=C.C_STOP, mec="white", mew=1, ls="none", zorder=9)
    a2.annotate(f"выход по стопу {tms(xm)[:-4]}\n{r['legs'][-1][1] / 1e8:.8g}", ((xm - T) / 1000.0, r["legs"][-1][1] / 1e8), textcoords="offset points", xytext=(-210, 14), fontsize=8.5, color=INK)
    if cs["key"] == "SOPH" and f.get("last_cross_t"):
        lc_abs = None
        above = (p[t > T] >= tr["level"])
        ch = np.where(np.diff(above.astype(int)) != 0)[0]
        tt = t[t > T]
        lc_abs = int(tt[ch[-1] + 1])
        for ax in (a1, a2):
            ax.axvline((lc_abs - T) / 1000.0, color=BLUE, lw=1.0, ls=(0, (2, 2)), zorder=6)
        a1.annotate(f"последний переход под уровень\n{tms(lc_abs)[6:-4]} — после него цена\nуровня уже не достигала", ((lc_abs - T) / 1000.0, lvl), textcoords="offset points", xytext=(8, -52), fontsize=8.5, color=INK)
    handles = [Line2D([0], [0], color="#3b4350", lw=1.0, label="цена сделок (aggTrades)"),
               Line2D([0], [0], color=INK, lw=2.0, label=f"уровень {lvl:.8g}"),
               Line2D([0], [0], color="#9a998f", lw=8, alpha=0.4, label="допуск ±0,2 %"),
               Line2D([0], [0], color="#0a8f3c", lw=1.2, label=f"цена входа {e:.8g}"),
               Line2D([0], [0], color=C.C_TAKE, lw=1.0, ls=(0, (6, 3)), label=f"тейк 1 (+1 NATR = {f['take1_vs_entry_bp']:.1f} бп) {take1:.8g}"),
               Line2D([0], [0], color=C.C_STOP, lw=1.0, ls=(0, (5, 3)), label=f"стоп −5 % {stop:.8g}")]
    fig.legend(handles=handles, loc="lower center", fontsize=8.5, frameon=False, ncol=3)
    fig.suptitle(f"{cs['sym']} лонг · вход {f['entry_t'][:-4]} · выход {f['exit_t'][:-4]} ({r['kind']}) · итог {r['net']:+.2f} $; {f['ticks_after_entry']} сделок ленты после входа", x=0.01, ha="left", fontsize=11, fontweight="bold", color=INK)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.925, bottom=0.13, hspace=0.3)
    fig.savefig(out, dpi=80)
    plt.close(fig)


def soph_csv(cs, f, tr, out):
    """Каждая сделка ленты после входа: цена, расстояние до уровня / стопа, максимум, состояние правил выхода (повтор Pos)."""
    r = cs["r"]
    t, p, bm = tr["_tape"]
    T = tr["T"]
    lvl = r["level_px_e8"]
    e = tr["entry_px_model"]
    pos = Pos(e, tr["p0"], r["natr_e8"])
    rows = []
    pending = None
    for i in np.where(t > T)[0]:
        ms, px = int(t[i]), int(p[i])
        note = ""
        st = pos.stop_level()
        if pending is not None:
            if ms >= pending[0]:
                note = "выход: исполнение по этой сделке"
                rows.append((ms, px, st, pos.maxp, pos, note))
                break
            pos.fill_takes(ms, px)
            rows.append((ms, px, st, pos.maxp, pos, "ожидание исполнения стопа (задержка 4 мс)"))
            continue
        kind = pos.on_trade(ms, px)
        if kind:
            pending = (ms + 4, kind)
            note = f"СТОП СРАБОТАЛ ({kind}): цена ≤ уровня стопа"
        rows.append((ms, px, st, pos.maxp, pos, note))
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["время_GMT+4", "цена", "от_уровня_бп", "от_стопа_бп", "максимум_с_входа", "тейк1_исполнен", "безубыток", "трейл_взведён", "уровень_стопа", "примечание"])
        for ms, px, st, mx, pp, note in rows:
            w.writerow([tms(ms), f"{px / 1e8:.8f}", f"{(px - lvl) / lvl * 1e4:+.1f}", f"{(px - st) / st * 1e4:+.1f}", f"{mx / 1e8:.8f}",
                        int(pp.next_tp > 0), int(pp.be_on), int(pp.armed), f"{st / 1e8:.8f}", note])
    # по минутам: сводка тиков (для документа)
    mn = {}
    for ms, px, st, mx, pp, note in rows:
        k = ms // 60_000
        d = mn.setdefault(k, [px, px, px, px, 0, 0])
        d[1], d[2], d[3] = max(d[1], px), min(d[2], px), px
        d[4] += 1
        d[5] += px >= lvl
    with open(out.replace("-ticks.csv", "-minutes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["минута_GMT+4", "сделок", "из них не ниже уровня", "open", "high", "low", "close", "high_от_уровня_бп", "low_от_стопа_бп"])
        st0 = tr["stop_level"]
        for k in sorted(mn):
            o_, h_, l_, c_, n_, nu = mn[k]
            w.writerow([tms(k * 60_000)[:-4], n_, nu, f"{o_ / 1e8:.8f}", f"{h_ / 1e8:.8f}", f"{l_ / 1e8:.8f}", f"{c_ / 1e8:.8f}",
                        f"{(h_ - lvl) / lvl * 1e4:+.1f}", f"{(l_ - st0) / st0 * 1e4:+.1f}"])
    return len(rows)


def chart_takes(tr, tape, out, csv_out):
    """MINIMAX шорт: лестница из 8 тейков против хода цены; что исполнено строго насквозь, что закрыло остаток (безубыток), что было после выхода."""
    plt = setup()
    from matplotlib.lines import Line2D
    t, p, bm = tape
    T = tr["T"]
    e = tr["entry_px_model"] / 1e8
    tps = [x / 1e8 for x in tr["tps"]]
    xm = tr["exit_ms"] + C.T0
    pr = p / 1e8
    nat = tr["natr_e8"] / 1e8
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(17, 9.6), dpi=80, gridspec_kw=dict(height_ratios=[1, 1]))
    legs = tr["take_legs"]
    tk1 = legs[0][0] if legs else None
    trig = tr["trigger"]["ms"]
    for ax, (x0, x1, ttl) in ((a1, (T - 10_000, xm + 20_000, "от входа до выхода: каждая сделка ленты")),
                              (a2, (T - 10_000, xm + 660_000, "то же + 11 минут после выхода (пунктир — тейки, которые остаток уже не получил)"))):
        mk = (t >= x0) & (t <= x1)
        xs = (t[mk] - T) / 1000.0
        ax.step(xs, pr[mk], where="post", color="#3b4350", lw=0.9 if ax is a1 else 0.6, zorder=4)
        xr = (x1 - T) / 1000.0
        ax.hlines(e, 0, (xm - T) / 1000.0, color="#0a8f3c", lw=1.4, zorder=5)
        for k, tp in enumerate(tps, 1):
            done = k <= tr["takes"]
            ax.hlines(tp, 0, (xm - T) / 1000.0 if done or ax is a1 else xr, color=C.C_TAKE, lw=1.1 if done else 0.9, ls="-" if done else (0, (5, 3)), zorder=5)
            ax.text(xr, tp, f" тейк {k}: {tp:.5f} ({(e - tp) / e / nat:.2f} NATR)" + (" — исполнен" if done else ""), fontsize=8, color=INK, va="center", clip_on=False)
            if ax is a2 and not done:
                after = np.where((t > xm) & (pr < tp))[0]
                if len(after):
                    ax.plot([(t[after[0]] - T) / 1000.0], [pr[after[0]]], marker="o", ms=5, mfc="white", mec=C.C_TAKE, mew=1.0, ls="none", zorder=8)
        ax.hlines(e, (xm - T) / 1000.0, xr, color="#0a8f3c", lw=0.8, ls=(0, (2, 3)), zorder=3)
        ax.plot([0], [e], marker="v", ms=10, mfc="#0a8f3c", mec="white", mew=1, ls="none", zorder=9)
        ax.axvline((xm - T) / 1000.0, color=C.C_STOP, lw=1.0, ls=(0, (2, 2)), zorder=6)
        ax.set_xlim(-10, xr)
        ax.set_title(ttl, loc="left", fontsize=10, color=INK, fontweight="bold")
        C.price_axis(ax)
        ax.set_xlabel("секунд от входа", loc="left", fontsize=9)
    if legs:
        for ax in (a1, a2):
            ax.plot([(tk1 - T) / 1000.0], [legs[0][1] / 1e8], marker="o", ms=9, mfc=C.C_TAKE, mec="white", mew=1, ls="none", zorder=9)
    a1.hlines(e, (tk1 - T) / 1000.0, (xm - T) / 1000.0, color=C.C_BE, lw=2.0, zorder=6)
    a1.plot([(trig - T) / 1000.0], [tr["trigger"]["px"] / 1e8], marker="X", ms=10, mfc=C.C_STOP, mec="white", mew=1, ls="none", zorder=9)
    a1.annotate(f"тейк 1 исполнен {tms(tk1)[6:]}\nсделка ниже {tps[0]:.5f}; после него стоп → в цену входа", ((tk1 - T) / 1000.0, legs[0][1] / 1e8), textcoords="offset points", xytext=(10, -48), fontsize=8.5, color=INK)
    a1.annotate(f"безубыток сработал {tms(trig)[6:]}: сделка {tr['trigger']['px'] / 1e8:.2f} ≥ стопа {tr['trigger']['stop'] / 1e8:.6f}\nвыход {tms(xm)[6:]} по {tr['exit_px_model'] / 1e8:.6f} (первая сделка ≥ +4 мс)",
                ((trig - T) / 1000.0, tr["trigger"]["px"] / 1e8), textcoords="offset points", xytext=(-330, 22), fontsize=8.5, color=INK)
    handles = [Line2D([0], [0], color="#3b4350", lw=1.0, label="цена сделок (aggTrades)"),
               Line2D([0], [0], color="#0a8f3c", lw=1.4, label=f"цена входа {e:.6f} (шорт)"),
               Line2D([0], [0], color=C.C_TAKE, lw=1.1, label="тейки: 8 ступеней от 1 до 5 NATR (NATR 0,476 %)"),
               Line2D([0], [0], color=C.C_BE, lw=2.0, label="стоп после тейка 1 = цена входа (безубыток)"),
               Line2D([0], [0], color=C.C_STOP, lw=1.0, ls=(0, (2, 2)), label="выход остатка")]
    fig.legend(handles=handles, loc="lower center", fontsize=8.5, frameon=False, ncol=3)
    fig.suptitle(f"MINIMAXUSDT шорт · вход {tms(tr['entry_abs'])[:-4]} · безубыток {tms(xm)[:-4]} · исполнен 1 тейк из 8", x=0.01, ha="left", fontsize=11, fontweight="bold", color=INK)
    fig.subplots_adjust(left=0.06, right=0.84, top=0.925, bottom=0.13, hspace=0.3)
    fig.savefig(out, dpi=80)
    plt.close(fig)
    with open(csv_out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["время_GMT+4", "цена", "от_входа_бп(+ против шорта)", "примечание"])
        for ms, px in zip(t, p):
            if T < ms <= xm:
                note = ""
                if legs and ms == legs[0][0] and px / 1e8 < tps[0]:
                    note = "тейк 1: сделка строго ниже цены тейка"
                if ms == trig:
                    note = "безубыток: сделка ≥ стопа (цена входа)"
                if ms == xm:
                    note = "выход остатка по этой сделке"
                w.writerow([tms(int(ms)), f"{px / 1e8:.5f}", f"{(px / 1e8 - e) / e * 1e4:+.1f}", note])


def main_minimax(trace_dir, out_dir):
    tr = json.load(open(os.path.join(trace_dir, "MINIMAXUSDT-83615-trace.json")))
    z = np.load(os.path.join(trace_dir, "MINIMAXUSDT-83615-tape.npz"))
    chart_takes(tr, (z["t"], z["p"], z["bm"]), os.path.join(out_dir, "MINIMAX-takes.png"), os.path.join(out_dir, "MINIMAX-ticks.csv"))
    print("MINIMAX ok")


def main():
    detail, trace_dir, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    for key in CASES:
        cs = load_case(key, detail)
        tr = json.load(open(os.path.join(trace_dir, f"{cs['sym']}-{cs['sm']}-trace.json")))
        z = np.load(os.path.join(trace_dir, f"{cs['sym']}-{cs['sm']}-tape.npz"))
        tr["_tape"] = (z["t"], z["p"], z["bm"])
        f = facts(cs, tr)
        json.dump(f, open(os.path.join(out_dir, f"facts-{key}.json"), "w"), ensure_ascii=False, indent=1, default=str)
        chart_1h(cs, f, os.path.join(out_dir, f"{key}-1h.png"))
        chart_zone(cs, f, os.path.join(out_dir, f"{key}-zone.png"))
        chart_ticks(cs, f, tr, os.path.join(out_dir, f"{key}-ticks.png"))
        if key == "SOPH":
            n = soph_csv(cs, f, tr, os.path.join(out_dir, "SOPH-ticks.csv"))
            print("SOPH csv rows", n)
        print(key, "ok", f["spread_bp"], f["born_t"], f["first_pivot_t"])
    main_minimax(trace_dir, out_dir)


if __name__ == "__main__":
    main()
