#!/usr/bin/env python3
"""Отчёт в деньгах по сетке `lob bounce-grid` (F8/F10 плана 2026-09-20).

Вход — каталоги сетки (`rounds.csv` + `forms.csv`) или прежний `rounds.json`.
Деньги круга считаются по **фактически исполненному** размеру (В-78, F4):

    usd = net_bps / 10000 × --order-usd × fill_frac

`net_bps` уже посчитан сеткой по `entry_vwap` и по ногам комиссий (В-63) — отчёт
сам никаких коэффициентов не придумывает. Рядом печатается итог «как раньше» —
по плановому размеру `--order-usd`, и отдельной строкой **Δ частичности**
(факт − план): у убыточной формы частичный вход уменьшает убыток, у прибыльной —
уменьшает прибыль, знак берётся из данных, а не задаётся.

Обратная совместимость: старый `rounds.csv` (до F4, без колонки `fill_frac`)
читается как полное исполнение (`fill_frac = 1.0`); это отмечается в шапке
отчёта и в логе. Легаси-`rounds.json` тоже читается (без статистики форм).

Запуск (на счётной машине, из /opt/alpha-compute):
    python3 bin/equity-report.py --grid-dir b5/f3-gate-new2
    python3 bin/equity-report.py --grid-dir long=b5/nightly-2026-09-19-base/a45-bid \
            --grid-dir short=b5/nightly-2026-09-19-base/a45-ask \
            --grid-dir long_dip=b5/nightly-2026-09-19-base/a45-bid-p4h-neg
Без аргументов — прежний вход `$TEMP/rounds.json` (легаси).
"""
import argparse
import collections
import datetime as dt
import importlib.util
import json
import math
import os
import statistics
import sys

# Лог печатается по-русски; на консолях с cp1251 (Windows) не падаем на «Δ» и «×».
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

_lib_spec = importlib.util.spec_from_file_location(
    "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib.py"))
_lib = importlib.util.module_from_spec(_lib_spec)
assert _lib_spec and _lib_spec.loader
_lib_spec.loader.exec_module(_lib)

# Плановый размер позиции круга, $ (README/CLAUDE: позиция $1000; ключ сетки
# `--order-usd`). Именно он домножается на фактическую долю исполнения.
DEFAULT_ORDER_USD = 1000.0
# Форма прежнего отчёта (equity-2026-09-20.html): база В-65, семья a45 (стоп 2 %,
# тейк 1:1, дедлайн 1 ч). Другие формы — из набора/плана, умолчания не выдумываем.
DEFAULT_FORM = "pct2-1to1-3600"

# Колонки F4/F5 (`src/commands/lob/bounce_grid.rs`, ROUNDS_HEADER/FORMS_HEADER).
ROUNDS_F4 = ("fill_frac", "entry_vwap", "legs_filled", "legs_rejected")
FORMS_F4 = (
    "n_partial",
    "n_fill_by_cross",
    "n_rejected_postonly",
    "n_entry_cancelled_ttl",
    "n_entry_cancelled_wall_dead",
    "n_entry_cancelled_price_left",
)

# Имена серий владельца: подпись и цвета как в отчёте 20.09.
KNOWN = {
    "long": ("Лонг от старой бид-стены", "#2a78d6", "#3987e5"),
    "short": ("Шорт от старой аск-стены", "#eb6834", "#d95926"),
    "long_dip": ("Лонг после просадки пула за 4 ч", "#1baf7a", "#199e70"),
    "long_btcdip": ("Лонг после просадки BTC за 4 ч", "#9257d6", "#a06ae2"),
}
PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#9257d6", "#c9a227", "#3f8f8f", "#b5479b")


def read_csv(path):
    """Заголовок + строки CSV; строки шапки на `#` (метаданные сетки) пропускаются (`_lib.read_csv`)."""
    return _lib.read_csv(path)


def load_rounds(path):
    """`rounds.csv` → (заголовок, строки-круги, есть ли колонка `fill_frac`)."""
    head, raw = read_csv(path)
    has = {c: c in head for c in ROUNDS_F4}
    if "net_bps" not in head or "symbol" not in head or "day_utc" not in head:
        sys.exit(f"{path}: не похоже на rounds.csv сетки (нет symbol/day_utc/net_bps)")
    rows = []
    for r in raw:
        def f(c):
            v = r.get(c)
            return float(v) if v not in (None, "") else None

        def i(c):
            v = r.get(c)
            return int(v) if v not in (None, "") else None

        # Обратная совместимость: нет колонки — полное исполнение (до F4).
        fill = f("fill_frac") if has["fill_frac"] else 1.0
        if fill is None:
            fill = 1.0
        t0 = i("t0_ns")
        ex = i("exit_ns")
        rows.append(dict(
            symbol=r["symbol"], day=r["day_utc"], form=r.get("form") or "",
            t0_ms=(t0 // 1_000_000) if t0 is not None else 0,
            exit_ms=(ex // 1_000_000) if ex is not None else 0,
            dir=i("dir") or 0, net_bps=float(r["net_bps"]), reason=r.get("reason") or "",
            fill_frac=fill, qty=f("qty"),
            entry_vwap=f("entry_vwap") if has["entry_vwap"] else None,
            legs_filled=i("legs_filled") if has["legs_filled"] else None,
            legs_rejected=i("legs_rejected") if has["legs_rejected"] else None,
        ))
    return head, rows, has["fill_frac"]


def load_json(path):
    """Легаси-вход: {имя: [[symbol, day, t0_ms, exit_ms, dir, net_bps, reason], …]}.

    Восьмой элемент строки (если есть) — `fill_frac`; иначе полное исполнение."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    out = []
    for name, recs in d.items():
        rows = []
        for r in recs:
            fill = float(r[7]) if len(r) > 7 else 1.0
            rows.append(dict(symbol=r[0], day=r[1], form="", t0_ms=int(r[2]), exit_ms=int(r[3]),
                             dir=int(r[4]), net_bps=float(r[5]), reason=r[6], fill_frac=fill,
                             qty=None, entry_vwap=None, legs_filled=None, legs_rejected=None))
        out.append(dict(name=name, path=path, rows=rows, forms=None, head=[], has_fill=False))
    return out


def parse_args(argv):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--grid-dir", action="append", default=[], metavar="[ИМЯ=]КАТАЛОГ",
                   help="каталог сетки с rounds.csv/forms.csv; повторяемый. ИМЯ задаётся "
                        "`ИМЯ=КАТАЛОГ` и даёт прежние подписи для long/short/long_dip/"
                        "long_btcdip, иначе подпись — имя каталога")
    p.add_argument("--form", default=DEFAULT_FORM,
                   help="форма сетки (умолчание %(default)s — форма отчёта 20.09); "
                        "`all` — все формы в одном отчёте")
    p.add_argument("--order-usd", type=float, default=DEFAULT_ORDER_USD,
                   help="плановый размер позиции круга, $ (умолчание %(default)s)")
    p.add_argument("--out", default=None, help="путь HTML (умолчание equity-<дата>.html рядом со скриптом)")
    p.add_argument("--json", default=None, help="легаси-вход rounds.json (только без --grid-dir)")
    return p.parse_args(argv)


def build_series(args):
    series = []
    for spec in args.grid_dir:
        if "=" in spec:
            name, path = spec.split("=", 1)
        else:
            name, path = os.path.basename(spec.rstrip("/\\")) or spec, spec
        rp = os.path.join(path, "rounds.csv")
        if not os.path.exists(rp):
            sys.exit(f"{rp}: нет файла (--grid-dir {spec})")
        head, rows, has_fill = load_rounds(rp)
        if args.form != "all":
            forms = sorted({r["form"] for r in rows})
            rows = [r for r in rows if r["form"] == args.form]
            if not rows:
                sys.exit(f"{rp}: формы {args.form} нет; в файле: {', '.join(forms[:12])}"
                         + (" …" if len(forms) > 12 else ""))
        fp = os.path.join(path, "forms.csv")
        forms = read_csv(fp)[1] if os.path.exists(fp) else None
        series.append(dict(name=name, path=path, rows=rows, forms=forms, head=head, has_fill=has_fill))
    if not series:
        jp = args.json or os.path.join(os.environ.get("TEMP") or "/tmp", "rounds.json")
        if not os.path.exists(jp):
            sys.exit("нет --grid-dir и нет легаси-входа " + jp + " (см. --help)")
        series = load_json(jp)
        print(f"легаси-вход {jp}: форм/колонок F4 нет, fill_frac = 1.0", file=sys.stderr)
    for i, s in enumerate(series):
        title, light, dark = KNOWN.get(s["name"], (s["name"], PALETTE[i % len(PALETTE)], None))
        s["title"] = title
        s["color"] = light
        s["color_dark"] = dark or light
        s["key"] = f"s{i}"
    return series


def forms_sums(forms, form):
    """Суммы счётчиков F4/F5 из forms.csv по выбранной форме (или по всем)."""
    if not forms:
        return None
    head = forms[0].keys()
    out = {c: 0 for c in FORMS_F4 if c in head}
    for r in forms:
        if form != "all" and r.get("form") != form:
            continue
        for c in out:
            v = r.get(c)
            if v not in (None, ""):
                out[c] += int(v)
    return out or None


def usd(row, pos, actual=True):
    v = row["net_bps"] / 10000.0 * pos
    return v * row["fill_frac"] if actual else v


def exec_notional(row):
    """Точный исполненный нотионал круга `qty × entry_vwap` (F3/F4), если он есть."""
    if row["qty"] is None or not row["entry_vwap"]:
        return None
    return row["qty"] * row["entry_vwap"]


def metrics(rows, pos):
    n = len(rows)
    if n == 0:
        return None
    actual = [usd(r, pos) for r in rows]
    planned = [usd(r, pos, actual=False) for r in rows]
    wins = [x for x in actual if x > 0]
    losses = [x for x in actual if x <= 0]
    eq = 0.0
    peak = 0.0
    dd = 0.0
    curve = []
    for x in actual:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        curve.append(eq)
    byday = collections.OrderedDict()
    for r, x in zip(rows, actual):
        byday.setdefault(r["day"], []).append(x)
    daily = [sum(v) for v in byday.values()]
    sd_d = statistics.pstdev(daily)
    sharpe_d = (statistics.mean(daily) / sd_d * math.sqrt(365)) if len(daily) > 1 and sd_d > 0 else None
    sd_t = statistics.pstdev(actual)
    sharpe_t = (statistics.mean(actual) / sd_t) if n > 1 and sd_t > 0 else None
    gp = sum(wins)
    gl = -sum(losses)
    sd = sd_t if n > 1 else 0.0
    ci_trade = (1.96 * sd / math.sqrt(n)) if n > 1 else None
    ci_day = (1.96 * statistics.pstdev(daily) / math.sqrt(len(daily))) if len(daily) > 1 else None
    fills = [r["fill_frac"] for r in rows]
    part = [r for r in rows if r["fill_frac"] < 1.0 - 1e-9]
    legs_f = sum(r["legs_filled"] or 0 for r in rows)
    legs_r = sum(r["legs_rejected"] or 0 for r in rows)
    exact = [exec_notional(r) for r in rows]
    exact_ok = all(e is not None for e in exact)
    exact_total = (sum(r["net_bps"] / 10000.0 * e for r, e in zip(rows, exact))
                   if exact_ok else None)
    exact_notional = sum(exact) if exact_ok else None
    return dict(
        n=n, wins=len(wins), winrate=len(wins) / n,
        avg_win=(gp / len(wins)) if wins else 0.0,
        avg_loss=(-gl / len(losses)) if losses else 0.0,
        pf=(gp / gl) if gl > 0 else float("inf"),
        total=sum(actual), total_planned=sum(planned), partiality=sum(actual) - sum(planned),
        per_trade=sum(actual) / n, dd=dd, ci_trade=ci_trade, ci_day=ci_day,
        daily=daily,
        sharpe_d=sharpe_d, sharpe_t=sharpe_t, byday=byday, curve=curve,
        reasons=collections.Counter(r["reason"] for r in rows),
        mean_fill=statistics.mean(fills), min_fill=min(fills),
        n_partial_rounds=len(part),
        partial_share=(len(part) / n),
        partiality_per_round=(sum(actual) - sum(planned)) / len(part) if part else 0.0,
        legs_filled=legs_f, legs_rejected=legs_r,
        legs_rejected_share=(legs_r / (legs_f + legs_r)) if (legs_f + legs_r) else None,
        exact_total=exact_total, exact_notional=exact_notional,
    )


def f_money(v, sign=True):
    return (f"{v:+,.0f}" if sign else f"{v:,.0f}").replace(",", " ") + " $"


def f_pct(x):
    return f"{100 * x:.0f} %"


def f_sh(x):
    return "—" if x is None else f"{x:.1f}"


def f_fill(x):
    return "—" if x is None else f"{x:.3f}"


def cell_delta(v):
    cls = "pos" if v > 0 else ("neg" if v < 0 else "mut")
    return f"<td class='{cls}'>{f_money(v)}</td>"


# ---------- таблицы ----------
def total_table(S, M):
    h = ("<tr><th>серия</th><th>сделок</th><th>винрейт</th><th>ср. плюс / минус</th><th>PF</th>"
         "<th>итог по факту (<code>fill_frac</code>)</th><th>итог при плановом размере</th>"
         "<th>Δ частичности</th><th>на сделку ± 95 %</th><th>в день ± 95 %</th>"
         "<th>макс. просадка</th><th>Шарп по дням*</th><th>Шарп по сделкам</th><th>выходы</th></tr>")
    rows = []
    for s in S:
        m = M[s["key"]]
        rows.append(
            f"<tr><td><span class='sw' style='--c:{s['color']};--cd:{s['color_dark']}'></span>{s['title']}</td>"
            f"<td>{m['n']}</td><td>{f_pct(m['winrate'])}</td>"
            f"<td>{f_money(m['avg_win'])} / {f_money(m['avg_loss'])}</td><td>{m['pf']:.2f}</td>"
            f"<td class='{'pos' if m['total']>0 else 'neg'}'><b>{f_money(m['total'])}</b></td>"
            f"<td class='mut'>{f_money(m['total_planned'])}</td>"
            + cell_delta(m["partiality"])
            + f"<td>{f_money(m['per_trade'])} ± {f_money(m['ci_trade'], False) if m['ci_trade'] is not None else '—'}"
              f"{' <span class=mut>(задевает ноль)</span>' if m['ci_trade'] is not None and abs(m['per_trade']) <= m['ci_trade'] else ''}</td>"
              f"<td>{f_money(statistics.mean(m['daily']))} ± {f_money(m['ci_day'], False) if m['ci_day'] is not None else '—'}"
              f"{' <span class=mut>(задевает ноль)</span>' if m['ci_day'] is not None and abs(statistics.mean(m['daily'])) <= m['ci_day'] else ''}</td>"
              f"<td>{f_money(m['dd'])} ({(m['dd'] / POS * 100):+.1f} % позиции)</td>"
              f"<td>{f_sh(m['sharpe_d'])}</td><td>{f_sh(m['sharpe_t'])}</td>"
              f"<td>{', '.join(f'{k} {v}' for k, v in m['reasons'].most_common())}</td></tr>")
    return "<table>" + h + "".join(rows) + "</table>"


def fill_table(S, M):
    """Доля исполнения и отвергнутые ноги (F4) + счётчики форм (F4/F5)."""
    h = ("<tr><th>серия</th><th>колонка <code>fill_frac</code></th><th>кругов</th>"
         "<th>ср. <code>fill_frac</code></th><th>мин.</th><th>кругов с <code>fill_frac &lt; 1</code></th>"
         "<th>ног исполнено</th><th>ног отвергнуто</th><th>доля отвергнутых ног</th>"
         "<th><code>n_partial</code></th><th><code>n_rejected_postonly</code></th>"
         "<th><code>n_fill_by_cross</code></th><th>отмены входа: ttl / стена снята / цена ушла</th></tr>")
    rows = []
    for s in S:
        m = M[s["key"]]
        if not s["has_fill"]:
            col = ("<td class='mut'>нет (до F4) → полное исполнение <code>1.0</code>"
                   " <span class=mut>(помечено)</span></td>")
        else:
            col = "<td>есть</td>"
        fo = forms_sums(s["forms"], FORM)
        if fo is not None:
            f4 = [f"<td>{fo.get(c, 0)}</td>" for c in ("n_partial", "n_rejected_postonly", "n_fill_by_cross")]
            cancels = " / ".join(str(fo.get(c, 0)) for c in
                                 ("n_entry_cancelled_ttl", "n_entry_cancelled_wall_dead",
                                  "n_entry_cancelled_price_left"))
            f4.append(f"<td>{cancels if any(c in fo for c in ('n_entry_cancelled_ttl', 'n_entry_cancelled_wall_dead', 'n_entry_cancelled_price_left')) else '—'}</td>")
        else:
            f4 = ["<td class='mut'>—</td>"] * 4
        legs = "—" if (m["legs_filled"] + m["legs_rejected"]) == 0 and not s["has_fill"] else str(m["legs_filled"])
        legsr = "—" if (m["legs_filled"] + m["legs_rejected"]) == 0 and not s["has_fill"] else str(m["legs_rejected"])
        lshare = f_pct(m["legs_rejected_share"]) if m["legs_rejected_share"] is not None else ("—" if not s["has_fill"] else "0 %")
        rows.append(f"<tr><td>{s['title']}</td>" + col
                    + f"<td>{m['n']}</td>"
                    + (f"<td>{f_fill(m['mean_fill'])}</td><td>{f_fill(m['min_fill'])}</td>"
                       f"<td>{m['n_partial_rounds']} ({f_pct(m['partial_share'])})</td>"
                       if s["has_fill"] else
                       "<td class='mut'>1.000</td><td class='mut'>1.000</td><td class='mut'>0</td>")
                    + f"<td>{legs}</td><td>{legsr}</td><td>{lshare}</td>" + "".join(f4) + "</tr>")
    return "<table>" + h + "".join(rows) + "</table>"


def partiality_table(S, M):
    """Δ частичности отдельной строкой: факт против планового размера."""
    h = ("<tr><th>серия</th><th>итог по факту</th><th>итог при плановом размере</th>"
         "<th>Δ частичности</th><th>Δ к |плану|</th><th>кругов с fill_frac &lt; 1</th>"
         "<th>Δ на такой круг</th><th>при нотионале файла (<code>Σ qty × entry_vwap</code>)</th></tr>")
    rows = []
    for s in S:
        m = M[s["key"]]
        denom = abs(m["total_planned"])
        share = f"{100 * m['partiality'] / denom:+.1f} %" if denom > 0 else "—"
        if m["exact_total"] is None:
            exact = "<td class='mut'>нет колонок <code>qty</code>/<code>entry_vwap</code></td>"
        else:
            exact = (f"<td>{f_money(m['exact_total'])} <span class=mut>(Σ нотионал "
                     f"{f_money(m['exact_notional'], False)})</span></td>")
        per = f_money(m["partiality_per_round"]) if m["n_partial_rounds"] else "—"
        rows.append(f"<tr><td>{s['title']}</td>"
                    f"<td class='{'pos' if m['total']>0 else 'neg'}'><b>{f_money(m['total'])}</b></td>"
                    f"<td class='mut'>{f_money(m['total_planned'])}</td>"
                    + cell_delta(m["partiality"])
                    + f"<td>{share}</td><td>{m['n_partial_rounds']}</td><td>{per}</td>" + exact + "</tr>")
    return "<table>" + h + "".join(rows) + "</table>"


def period_table(S, M):
    days = sorted({d for s in S for d in M[s["key"]]["byday"]})
    h = "<tr><th>период</th>" + "".join(f"<th>{s['title']}</th>" for s in S) + "</tr>"
    rows = []
    for d in days:
        cells = []
        for s in S:
            v = M[s["key"]]["byday"].get(d)
            cells.append(f"<td class='{'pos' if v and sum(v)>0 else 'neg'}'>{f_money(sum(v))} "
                         f"<span class='mut'>({len(v)} сд, {f_pct(sum(1 for x in v if x>0)/len(v))})</span></td>"
                         if v else "<td class='mut'>—</td>")
        rows.append(f"<tr><td>{d}</td>" + "".join(cells) + "</tr>")
    cells = []
    for s in S:
        m = M[s["key"]]
        cells.append(f"<td class='{'pos' if m['total']>0 else 'neg'}'><b>{f_money(m['total'])}</b> "
                     f"<span class='mut'>({m['n']} сд, {f_pct(m['winrate'])})</span></td>")
    rows.append("<tr class='tot'><td>весь период</td>" + "".join(cells) + "</tr>")
    return "<table>" + h + "".join(rows) + "</table>"


def side_table(S):
    """По стороне круга (`dir` в rounds.csv): +1 лонг, −1 шорт."""
    h = "<tr><th>серия</th><th>сторона</th><th>сделок</th><th>винрейт</th><th>итог</th><th>на сделку</th></tr>"
    rows = []
    for s in S:
        for d, name in ((1, "лонг (+1)"), (-1, "шорт (−1)")):
            rr = [r for r in s["rows"] if r["dir"] == d]
            if not rr:
                continue
            m = metrics(rr, POS)
            rows.append(f"<tr><td>{s['title']}</td><td>{name}</td><td>{m['n']}</td>"
                        f"<td>{f_pct(m['winrate'])}</td>"
                        f"<td class='{'pos' if m['total']>0 else 'neg'}'>{f_money(m['total'])}</td>"
                        f"<td>{f_money(m['per_trade'])}</td></tr>")
    return "<table>" + h + "".join(rows) + "</table>"


def coin_table(rows, limit=None):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["symbol"]].append(usd(r, POS))
    items = sorted(by.items(), key=lambda kv: sum(kv[1]), reverse=True)
    if limit and len(items) > limit * 2:
        items = items[:limit] + [("…", [])] + items[-limit:]
    h = "<tr><th>монета</th><th>сделок</th><th>винрейт</th><th>итог</th><th>на сделку</th></tr>"
    out = []
    for sym, v in items:
        if not v:
            out.append("<tr><td colspan=5 class='mut'>…</td></tr>")
            continue
        w = sum(1 for x in v if x > 0)
        out.append(f"<tr><td>{sym.replace('USDT','')}</td><td>{len(v)}</td><td>{f_pct(w/len(v))}</td>"
                   f"<td class='{'pos' if sum(v)>0 else 'neg'}'>{f_money(sum(v))}</td>"
                   f"<td>{f_money(sum(v)/len(v))}</td></tr>")
    return "<table>" + h + "".join(out) + "</table>"


# ---------- запуск ----------
ARGS = parse_args(sys.argv[1:])
POS = ARGS.order_usd
FORM = ARGS.form
S = build_series(ARGS)
M = {s["key"]: metrics(s["rows"], POS) for s in S}
for s in S:
    if M[s["key"]] is None:
        sys.exit(f"{s['path']}: под --form {FORM} нет ни одного круга")

DAYS = sorted({d for s in S for d in M[s["key"]]["byday"]})
SYMBOLS = sorted({r["symbol"] for s in S for r in s["rows"]})

# Легаси-вход: POS задавался только внутренней константой; --order-usd остаётся $1000.
warns = [s["title"] for s in S if not s["has_fill"]]
if ARGS.grid_dir and warns:
    fill_note = ("<b>⚠ Внимание:</b> в <code>rounds.csv</code> нет колонки <code>fill_frac</code> (дампы до F4) — "
                 "такие серии считаются <b>полностью исполненными</b> (<code>fill_frac = 1.0</code>): " + ", ".join(warns)
                 + ". Итог по факту и при плановом размере у них совпадают.")
elif ARGS.grid_dir:
    fill_note = ("Деньги круга — по фактически исполненному размеру: "
                 "<code>fill_frac × --order-usd</code> (В-78, F4); <code>net_bps</code> посчитан сеткой по "
                 "<code>entry_vwap</code> и ногам комиссий (В-63).")
else:
    fill_note = ("Легаси-вход <code>rounds.json</code>: колонок F4/F5 нет, все круги считаются полностью "
                 "исполненными (<code>fill_frac = 1.0</code>).")

src = "<br>".join(
    f"<code>{s['path']}</code> ({'rounds+forms' if s['forms'] is not None else 'rounds'}; "
    f"{'fill_frac есть' if s['has_fill'] else 'fill_frac НЕТ → 1.0'}; "
    f"{len(s['rows'])} кругов)" for s in S)
def fmt_pos(v):
    return f"{v:,.0f}".replace(",", " ")


subtitle = (f"Бэктест <code>lob bounce-grid</code>: {DAYS[0]} … {DAYS[-1]} UTC ({len(DAYS)} сут), "
            f"{len(SYMBOLS)} монет, форма <b>{FORM}</b>, позиция <b>{fmt_pos(POS)} $</b>, "
            "одна позиция за раз, вход лимиткой, задержка измеренная (В-68), комиссии по ногам (В-63).")

# ---------- данные графика ----------
chart = {}
for s in S:
    eq = 0.0
    pts = []
    for r in s["rows"]:
        v = usd(r, POS)
        eq += v
        pts.append([r["t0_ms"], round(eq, 2), r["symbol"].replace("USDT", ""), round(v, 2),
                    r["reason"], round(r["fill_frac"], 4)])
    chart[s["key"]] = dict(name=s["title"], color=s["color"], colorDark=s["color_dark"], points=pts)
daily_js = {s["key"]: {d: round(sum(v), 1) for d, v in M[s["key"]]["byday"].items()} for s in S}
first = S[0]["key"]
coins_js = sorted(((sym, round(sum(v), 1), len(v)) for sym, v in
                   ((sym, [usd(r, POS) for r in S[0]["rows"] if r["symbol"] == sym])
                    for sym in {r["symbol"] for r in S[0]["rows"]})), key=lambda x: x[1])

PAGE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Эквити отскока</title>
<style>
:root{--surface:#fcfcfb;--panel:#ffffff;--text:#0b0b0b;--text2:#52514e;--mut:#7a7975;--grid:#e6e5e1;--pos:#006300;--neg:#d03b3b;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--surface:#1a1a19;--panel:#232322;--text:#fff;--text2:#c3c2b7;--mut:#8a8984;--grid:#33332f;--pos:#0ca30c;--neg:#ec835a;--s1:#3987e5;--s2:#d95926;--s3:#199e70}}
:root[data-theme=dark]{--surface:#1a1a19;--panel:#232322;--text:#fff;--text2:#c3c2b7;--mut:#8a8984;--grid:#33332f;--pos:#0ca30c;--neg:#ec835a;--s1:#3987e5;--s2:#d95926;--s3:#199e70}
body{margin:0;background:var(--surface);color:var(--text);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;padding:16px}
main{max-width:1300px;margin:0 auto} h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 8px} h3{font-size:14px;margin:18px 0 6px} .sub{color:var(--text2);margin:0 0 16px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0} th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top} th{color:var(--text2);font-weight:600}
td.pos{color:var(--pos)} td.neg{color:var(--neg)} .mut{color:var(--mut)} tr.tot td{border-top:2px solid var(--grid);font-weight:600}
.warn{background:var(--panel);border:1px solid var(--grid);border-left:3px solid #d03b3b;border-radius:6px;padding:8px 12px;margin:10px 0;color:var(--text2)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--c);margin-right:6px;vertical-align:middle}
@media (prefers-color-scheme:dark){.sw{background:var(--cd)}}
.card{background:var(--panel);border:1px solid var(--grid);border-radius:8px;padding:12px;margin:8px 0}
svg{width:100%;height:auto;display:block} .legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--text2);margin:4px 0 8px}
.tip{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--grid);border-radius:6px;padding:6px 8px;font-size:12px;display:none;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.note{color:var(--text2);font-size:13px} .wrap{overflow-x:auto}
</style></head><body><main>
<h1>Отскок от старых стен — эквити и P&amp;L</h1>
<p class="sub">__SUBTITLE__</p>
<p class="__FILLCLS__" id="fillnote">__FILLNOTE__</p>

<h2>Тотал</h2><div class="wrap">__TOTAL__</div>
<p class="note">* Шарп по дням — годовой по дневным P&amp;L: при малом числе суток это не оценка, а знак. Шарп по сделкам — mean/std одной сделки. Просадка — от пика накопленного P&amp;L, в $ на позицию.</p>

<h2>Исполнение входа и частичность (F4/F5)</h2><div class="wrap">__FILL__</div>
<h3>Δ частичности отдельной строкой</h3><div class="wrap">__PARTIAL__</div>
<p class="note">Δ частичности = итог по факту − итог при плановом размере <code>--order-usd</code> (без <code>fill_frac</code>). У убыточной формы частичный вход уменьшает убыток (Δ &gt; 0), у прибыльной — уменьшает прибыль (Δ &lt; 0); знак берётся из данных. Счётчики форм (<code>n_partial</code>, <code>n_rejected_postonly</code>, <code>n_fill_by_cross</code>, отмены входа) — суммы по выбранной форме из <code>forms.csv</code>; круги с <code>fill_frac &lt; 1</code>, ноги — по <code>rounds.csv</code>. Столбец «при нотионале файла» — те же круги в деньгах по исполненному нотионалу <code>qty × entry_vwap</code> без масштабирования к <code>--order-usd</code>: он совпадает с «итогом по факту», только когда лот сетки равен <code>--order-usd</code> (у прогонов с <code>--order-qty-from-pool</code>/<code>--order-qty-e9</code> лот свой — расхождение ожидаемо, его величину видно в «Σ нотионал»).</p>

<h2>Эквити (накопленный P&amp;L, $ на позицию)</h2>
<div class="card"><div class="legend" id="lg"></div><svg id="eq" viewBox="0 0 1000 360" role="img" aria-label="Кривые эквити"></svg></div>

<h2>По периодам</h2><div class="wrap">__PERIOD__</div>
<div class="card"><div class="legend">P&amp;L по дням, $ на позицию</div><svg id="dl" viewBox="0 0 1000 220" role="img" aria-label="Дневной P&L"></svg></div>

<h2>По стороне круга</h2><div class="wrap">__SIDE__</div>

<h2>По монетам — __FIRST__ (все)</h2><div class="wrap">__COINS__</div>
<div class="card"><div class="legend">Итог по монетам, $ на позицию</div><svg id="cn" viewBox="0 0 1000 __CNH__" role="img" aria-label="P&L по монетам"></svg></div>
__COINS_REST__

<p class="note">Источник: __SRC__.<br>Сгенерировано __DATE__.</p>
<div class="tip" id="tip"></div>
</main>
<script>
const CH=__CH__;
const DAILY=__DAILY__;
const COINS=__COINSJS__;
const DAYS=__DAYS__;
const dark=matchMedia('(prefers-color-scheme:dark)').matches && document.documentElement.dataset.theme!=='light';
const col=k=>dark?CH[k].colorDark:CH[k].color;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const tip=document.getElementById('tip');
function showTip(e,html){tip.innerHTML=html;tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';}
function hideTip(){tip.style.display='none';}
const fmt=v=>(v>0?'+':'')+v.toFixed(0)+' $';
const pctf=f=>f>=0.9995?'':(' · fill '+(100*f).toFixed(0)+' %');
const dstr=ms=>{const d=new Date(ms);return d.toISOString().slice(5,16).replace('T',' ');};
// ---- equity
(function(){
  const svg=document.getElementById('eq'),W=1000,H=360,L=56,R=16,T=16,B=36;
  const keys=Object.keys(CH); let xs=[],ys=[0];
  keys.forEach(k=>CH[k].points.forEach(p=>{xs.push(p[0]);ys.push(p[1]);}));
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const X=t=>L+(t-x0)/(x1-x0)*(W-L-R), Y=v=>T+(y1-v)/(y1-y0)*(H-T-B);
  let s='';
  const step=Math.pow(10,Math.floor(Math.log10((y1-y0)/4)))*(((y1-y0)/4)/Math.pow(10,Math.floor(Math.log10((y1-y0)/4)))>5?5:((y1-y0)/4)/Math.pow(10,Math.floor(Math.log10((y1-y0)/4)))>2?2:1);
  for(let v=Math.ceil(y0/step)*step;v<=y1;v+=step){s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${css('--grid')}" stroke-width="1"/><text x="${L-6}" y="${Y(v)+4}" text-anchor="end" font-size="11" fill="${css('--text2')}">${fmt(v)}</text>`;}
  s+=`<line x1="${L}" x2="${W-R}" y1="${Y(0)}" y2="${Y(0)}" stroke="${css('--text2')}" stroke-width="1"/>`;
  for(const d of DAYS){const t=Date.parse(d+'T00:00:00Z'); if(t>=x0&&t<=x1) s+=`<line x1="${X(t)}" x2="${X(t)}" y1="${T}" y2="${H-B}" stroke="${css('--grid')}"/><text x="${X(t)+4}" y="${H-B+16}" font-size="11" fill="${css('--text2')}">${d.slice(5)}</text>`;}
  keys.forEach(k=>{const p=CH[k].points; let d='M'+X(x0)+','+Y(0); p.forEach(q=>{d+=' L'+X(q[0])+','+Y(q[1]);}); s+=`<path d="${d}" fill="none" stroke="${col(k)}" stroke-width="2" stroke-linejoin="round"/>`;
    const last=p[p.length-1]; s+=`<text x="${Math.min(X(last[0])+6,W-R-90)}" y="${Y(last[1])+4}" font-size="12" fill="${css('--text')}">${fmt(last[1])}</text>`;
    p.forEach(q=>{s+=`<circle cx="${X(q[0])}" cy="${Y(q[1])}" r="7" fill="transparent" data-k="${k}" data-t="${q[0]}" data-e="${q[1]}" data-s="${q[2]}" data-n="${q[3]}" data-r="${q[4]}" data-f="${q[5]}"/>`;});
  });
  svg.innerHTML=s;
  svg.addEventListener('mousemove',e=>{const c=e.target.closest('circle');if(!c){hideTip();return;}showTip(e,`<b>${CH[c.dataset.k].name}</b><br>${dstr(+c.dataset.t)} UTC · ${c.dataset.s}<br>сделка ${fmt(+c.dataset.n)} (${c.dataset.r})${pctf(+c.dataset.f)} · накопл. ${fmt(+c.dataset.e)}`);});
  svg.addEventListener('mouseleave',hideTip);
  document.getElementById('lg').innerHTML=keys.map(k=>`<span><span class="sw" style="background:${col(k)}"></span>${CH[k].name}</span>`).join('');
})();
// ---- daily bars
(function(){
  const svg=document.getElementById('dl'),W=1000,H=220,L=56,R=16,T=12,B=28;
  const keys=Object.keys(DAILY), days=[...new Set(keys.flatMap(k=>Object.keys(DAILY[k])))].sort();
  const vals=keys.flatMap(k=>Object.values(DAILY[k])); const y1=Math.max(0,...vals),y0=Math.min(0,...vals);
  const Y=v=>T+(y1-v)/(y1-y0)*(H-T-B); const gw=(W-L-R)/days.length, bw=(gw-24)/keys.length-2;
  let s=`<line x1="${L}" x2="${W-R}" y1="${Y(0)}" y2="${Y(0)}" stroke="${css('--text2')}"/>`;
  days.forEach((d,i)=>{s+=`<text x="${L+i*gw+gw/2}" y="${H-B+16}" text-anchor="middle" font-size="11" fill="${css('--text2')}">${d.slice(5)}</text>`;
    keys.forEach((k,j)=>{const v=DAILY[k][d]||0; const x=L+i*gw+12+j*(bw+2); const y=Math.min(Y(0),Y(v)); const h=Math.abs(Y(v)-Y(0));
      s+=`<rect x="${x}" y="${y}" width="${bw}" height="${Math.max(h,1)}" rx="3" fill="${col(k)}" data-k="${k}" data-d="${d}" data-v="${v}"/><text x="${x+bw/2}" y="${v>=0?y-4:(y+h+12<H-B-2?y+h+12:y+12)}" text-anchor="middle" font-size="11" fill="${v>=0||y+h+12<H-B-2?css('--text'):'#fff'}">${fmt(v)}</text>`;});
  });
  svg.innerHTML=s;
  svg.addEventListener('mousemove',e=>{const r=e.target.closest('rect');if(!r){hideTip();return;}showTip(e,`<b>${CH[r.dataset.k].name}</b><br>${r.dataset.d}: ${fmt(+r.dataset.v)}`);});
  svg.addEventListener('mouseleave',hideTip);
})();
// ---- coins
(function(){
  const svg=document.getElementById('cn'),W=1000,L=90,R=60,T=8,rowh=18; const H=T+rowh*COINS.length+24;
  const vals=COINS.map(c=>c[1]); const v0=Math.min(0,...vals),v1=Math.max(0,...vals); const X=v=>L+(v-v0)/(v1-v0)*(W-L-R);
  let s=`<line x1="${X(0)}" x2="${X(0)}" y1="${T}" y2="${H-16}" stroke="${css('--text2')}"/>`;
  [...COINS].reverse().forEach((c,i)=>{const y=T+i*rowh; const x=Math.min(X(0),X(c[1])); const w=Math.abs(X(c[1])-X(0));
    s+=`<text x="${L-6}" y="${y+13}" text-anchor="end" font-size="11" fill="${css('--text2')}">${c[0].replace('USDT','')}</text><rect x="${x}" y="${y+3}" width="${Math.max(w,1)}" height="${rowh-6}" rx="3" fill="${c[1]>=0?css('--s1'):css('--s2')}"/><text x="${c[1]>=0?X(c[1])+4:X(c[1])-4}" y="${y+13}" text-anchor="${c[1]>=0?'start':'end'}" font-size="11" fill="${css('--text')}">${fmt(c[1])} · ${c[2]} сд</text>`;});
  svg.innerHTML=s;
})();
</script></body></html>"""

coins_rest = []
for s in S[1:]:
    coins_rest.append(f"<h2>По монетам — {s['title']} (лучшие и худшие)</h2>"
                      f"<div class='wrap'>{coin_table(s['rows'], 6)}</div>")

html = (PAGE
        .replace("__SUBTITLE__", subtitle)
        .replace("__FILLNOTE__", fill_note)
        .replace("__FILLCLS__", "warn" if (warns and ARGS.grid_dir) else "note")
        .replace("__TOTAL__", total_table(S, M))
        .replace("__FILL__", fill_table(S, M))
        .replace("__PARTIAL__", partiality_table(S, M))
        .replace("__PERIOD__", period_table(S, M))
        .replace("__SIDE__", side_table(S))
        .replace("__FIRST__", S[0]["title"])
        .replace("__COINS__", coin_table(S[0]["rows"]))
        .replace("__COINS_REST__", "".join(coins_rest))
        .replace("__CNH__", str(40 + 18 * len(coins_js)))
        .replace("__SRC__", src)
        .replace("__DATE__", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        .replace("__CH__", json.dumps(chart, ensure_ascii=False))
        .replace("__DAILY__", json.dumps(daily_js, ensure_ascii=False))
        .replace("__COINSJS__", json.dumps(coins_js, ensure_ascii=False))
        .replace("__DAYS__", json.dumps(DAYS)))

out = ARGS.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               f"equity-{dt.date.today().isoformat()}.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)

# ---------- лог для капитана ----------
if ARGS.grid_dir:
    mode = ("fill_frac × order_usd" if all(s["has_fill"] for s in S)
            else "fill_frac = 1.0 (нет колонки — дампы до F4, помечено в шапке)")
else:
    mode = "легаси rounds.json: fill_frac = 1.0 (колонок F4/F5 нет)"
print(f"форма: {FORM}; позиция (--order-usd): {POS:.0f} $; режим денег: {mode}")
for s in S:
    m = M[s["key"]]
    print(f"  {s['name']}: n={m['n']} win={m['winrate']:.0%} "
          f"итог[факт fill_frac]={m['total']:+.0f}$ итог[план {POS:.0f}$]={m['total_planned']:+.0f}$ "
          f"Δчастичности={m['partiality']:+.0f}$ dd={m['dd']:+.0f}$ "
          f"ср.fill_frac={m['mean_fill']:.3f} кругов<1={m['n_partial_rounds']} "
          f"ног исп/отверг={m['legs_filled']}/{m['legs_rejected']}"
          + (f" | итог по нотионалу файла Σ(qty×vwap)={m['exact_notional']:.0f}$"
             f"={m['exact_total']:+.0f}$" if m["exact_total"] is not None else "")
          + (f" | forms: " + " ".join(f"{k}={v}" for k, v in (forms_sums(s["forms"], FORM) or {}).items())
             if s["forms"] is not None else ""))
    if not s["has_fill"]:
        src_name = "rounds.csv" if ARGS.grid_dir else "легаси rounds.json"
        print(f"    ВНИМАНИЕ: {s['path']}: в {src_name} нет колонки fill_frac (дампы до F4) — "
              f"полное исполнение (fill_frac = 1.0), итог[факт] == итог[план]; помечено в шапке отчёта")
print(f"артефакт: {out} ({len(html)} байт)")
