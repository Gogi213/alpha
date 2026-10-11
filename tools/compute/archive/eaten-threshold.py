#!/usr/bin/env python3
"""Порог «съели» (X) для отмены сценария в позиции — замер по CSV ёмкости и касаний (F9).

    python3 tools/compute/eaten-threshold.py --dir study/capacity/v72 --touches-from study/touches \\
            [--sets a45-bid,a15-bid,a45-ask,a45-bid-b4h-neg] [--usd 200,500,1000] [--band 2,10]

Что считает (все числа — из данных, ничего не пишется на диск):

1. **Доля съеденного у касаний, где нога 0,02–0,1 % над стеной исполнилась бы** (буквально F9).
   Полоса — `--band` (bps от стены; 2..10 = 0,02–0,1 %). Правило исполнения ноги — ровно то же, что в
   `fill-capacity.py` / `leg-distance.py` (модуль `lob::capacity`): нога на тике исполнена на
   `clamp(наторговано_против_нас_на_тике_за_касание − очередь_впереди_на_тике, 0, нога)`; нога по нашу
   сторону от лучшей цены другой стороны на момент касания (`tick >= opp_t0` у бида, `tick <= opp_t0`
   у аска) — **не поставлена** (пост-онли отверг бы); размер ноги в лотах — `floor($ / (тик × tick_px ×
   lot_qty))`, размеры `--usd` — как в `fill-capacity.py` (200/500/1000).

   Числитель «сколько стены съели», знаменатель один — `size_at_touch` (размер стены на момент касания):
   - **A** = сумма `sold_touch` по **тикам полосы** / `size_at_touch` — наторговано по ценам, где лежали
     бы ноги лестницы (тики выше стены), за окно касания `[t0, end]`;
   - **B** = `sold_touch` **на тике стены** / `size_at_touch` — прямое чтение В-75;
   - **B(h)** = `sold_post<h>` на тике стены / `size_at_touch` — за `h` секунд от `t0`
     (10/60/300/1800 с). Вход по В-73 живёт минуты, поэтому **B(h)** — рабочая форма порога X:
     `sold_post*` пишется только для тиков, попавших в полосу (тик стены в полосе при off=0),
     и не знает о смерти стены — верхняя оценка.

   Подвыборки печатаются отдельно и не смешиваются:
   - **все касания** набора (и счётчики выбытия: нет тика стены, нет тиков полосы, все ноги через
     спред, размер ноги меньше лота);
   - **«нога ставится»** — в полосе есть тик, нога не через спред, размер ноги в лотах > 0;
   - **«нога исполнилась бы»** — подвыборка, требуемая F9 буквально; в ней A — по исполнившимся ногам
     (максимум: «насколько стену съели»);
   - **«в стену продавали»** — где `sold_touch` на тике стены > 0. Рабочая подвыборка для порога:
     пока сделок нет, «съеденное» равно нулю у подавляющего большинства касаний, и безусловные
     квантили вырождаются в ноль (печатается как есть).

2. **Квантили** p10/p20/p50/p75/p90 — по набору, по стороне (bid/ask) и по возрасту (`a45-*` / `a15-*`).
   Жирным p20 и p50 — кандидаты `X₁`, `X₂` для `eat<X>`.

3. **Markout по корзинам доли съеденного** (0 / 0–10 / 10–20 / 20–50 / 50+ %). Markout склеивается с
   `touches-<SYMBOL>.csv` по ключу `(side, price_tick, start_ms)` внутри тех же суток (`day` в capacity
   = `day_utc` в touches). Берутся `m_1h` и `m_2h` («в сторону отскока»). Пустой `m_*` (нет среза) —
   **отдельный счётчик**, в средние не подмешивается. Средние печатаются «по модулю» (знак отскока)
   и в знаке сделки **лонг** (аск-стена × −1) — видно асимметрию сторон.

4. **«Сняли» (В-75б) — что выводимо.** На тике стены наторгованное против нас с `t0` за
   `post10/60/300/1800` (`sold_post*`) сравнивается с очередью `q_t0`: если сделок меньше очереди —
   остаток очереди ушёл **снятием**. Считается по выборке «нога ставится».

Замер — срез, не вердикт: интервалов не строит, ничего не пишет на диск.
"""
import argparse
import collections
import csv
import glob
import math
import os
import statistics
import sys

BUCKETS = ["ровно 0", "0–10 %", "10–20 %", "20–50 %", "50+ %"]
HORIZONS = (10, 60, 300, 1800)


def pct(x):
    return f"{100 * x:.1f} %"


def quantile(sorted_xs, q):
    """Квантиль линейной интерполяцией по возрастающему списку."""
    n = len(sorted_xs)
    if n == 0:
        return None
    if n == 1:
        return sorted_xs[0]
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] * (hi - pos) + sorted_xs[hi] * (pos - lo)


def fmt_q(v):
    """Доля — в процентах с двумя знаками (доли стены малы)."""
    return "—" if v is None else f"{100 * v:.2f} %"


def fmt1(v, digits=1):
    return "—" if v is None else f"{v:+.{digits}f}"


def sd(xs):
    return statistics.stdev(xs) if len(xs) > 1 else None


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_markout(touches_from):
    """{(symbol, day_utc, side, price_tick, start_ms): (m_1h|None, m_2h|None)} по кэшу касаний.

    Символ берётся из имени файла `touches-<SYMBOL>.csv` — в самом CSV колонки символа нет,
    а без него ключ склеил бы разные монеты с теми же тиком и временем.
    """
    mk = {}
    if not touches_from:
        return mk
    for day_dir in sorted(glob.glob(os.path.join(touches_from, "20*"))):
        if not os.path.isdir(day_dir):
            continue
        for p in sorted(glob.glob(os.path.join(day_dir, "touches-*.csv"))):
            sym = os.path.basename(p)[len("touches-"):-len(".csv")]
            for r in read_csv(p):
                key = (sym, r["day_utc"], r["side"], int(r["price_tick"]), int(r["start_ms"]))
                vals = []
                for col in ("m_1h", "m_2h"):
                    v = r.get(col, "")
                    vals.append(None if v in ("", None) else float(v))
                mk[key] = tuple(vals)
    return mk


def in_band(dist, band):
    return band[0] <= dist <= band[1]


def bucket(r):
    if r <= 0:
        return BUCKETS[0]
    if r < 0.10:
        return BUCKETS[1]
    if r < 0.20:
        return BUCKETS[2]
    if r < 0.50:
        return BUCKETS[3]
    return BUCKETS[4]


def read_touches(setdir):
    """Касания набора → {key: запись}, ключ (symbol, day, side, price_tick, start_ms).

    Символ обязателен в ключе: (day, side, price_tick, start_ms) у разных монет совпадает
    (один ценовой уровень, та же миллисекунда) — без символа касания склеиваются.
    """
    touches = collections.OrderedDict()
    no_wall = 0
    for path in sorted(glob.glob(os.path.join(setdir, "capacity-*.csv"))):
        rows = read_csv(path)
        if not rows:
            continue
        per = collections.OrderedDict()
        for r in rows:
            key = (r["symbol"], r["day"], r["side"], int(r["price_tick"]), int(r["start_ms"]))
            per.setdefault(key, {})[int(r["off"])] = r
        for key, ticks in per.items():
            if 0 not in ticks:
                # Тик самой стены не записан (полоса начинается выше) — долю съеденного не счесть.
                no_wall += 1
                continue
            any_row = next(iter(ticks.values()))
            touches[key] = {
                "size": int(any_row["size_at_touch"]),
                "side": any_row["side"],
                "symbol": any_row["symbol"],
                "ticks": ticks,
            }
    return touches, no_wall


def band_legs(ticks, side, band):
    """Тики полосы (offs) и из них не через спред и с известной очередью (placeable)."""
    offs = [o for o in sorted(ticks) if in_band(float(ticks[o]["dist_bps"]), band)]
    placeable = []
    for o in offs:
        r = ticks[o]
        tick = int(r["tick"])
        opp = int(r["opp_t0"])
        if opp >= 0 and ((side == "bid" and tick >= opp) or (side == "ask" and tick <= opp)):
            continue
        if int(r["q_t0"]) < 0:
            continue
        placeable.append(o)
    return offs, placeable


def fill_stats(ticks, placeable, usd):
    """Ноги полосы на касании: исполнилось бы и чем это подтверждено.

    → (n_fill, n_placed, fill_usd, a_filled, a_band) или None (размер стены 0).
    a_band — сумма `sold_touch` по ногам полосы / размер стены (ставится или нет);
    a_filled — то же по исполнившимся ногам.
    """
    size = None
    n_fill = 0
    n_placed = 0
    fill_usd = 0.0
    sold_filled = 0
    sold_band = 0
    for o in placeable:
        r = ticks[o]
        size = int(r["size_at_touch"])
        tick = int(r["tick"])
        px = tick * float(r["tick_px"])
        lot = float(r["lot_qty"])
        sold = int(r["sold_touch"])
        sold_band += sold
        want_lots = math.floor(usd / (px * lot))
        if want_lots <= 0:
            continue
        n_placed += 1
        got = max(0, min(want_lots, sold - int(r["q_t0"])))
        if got > 0:
            n_fill += 1
            fill_usd += got * px * lot
            sold_filled += sold
    if size is None or size <= 0:
        return None
    return n_fill, n_placed, fill_usd, sold_filled / size, sold_band / size


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dir", required=True, help="каталог с наборами: <набор>/capacity-*.csv")
    ap.add_argument("--touches-from", default=None,
                    help="корень кэша касаний: <корень>/<сутки>/touches-*.csv (без него markout не считается)")
    ap.add_argument("--usd", default="200,500,1000", help="размеры ноги, $ через запятую")
    ap.add_argument("--band", default="2,10", help="полоса расстояния от стены, bps (по умолчанию 2,10 = 0,02–0,1 %%)")
    ap.add_argument("--sets", default=None, help="наборы через запятую (по умолчанию — все подкаталоги с capacity-*.csv)")
    args = ap.parse_args()

    usds = [float(x) for x in args.usd.split(",") if x]
    main_usd = usds[-1]
    band = tuple(float(x) for x in args.band.split(","))
    if len(band) != 2:
        raise SystemExit("--band: два числа bps, например 2,10")

    if args.sets:
        names = [s for s in args.sets.split(",") if s]
    else:
        names = [
            os.path.basename(os.path.dirname(p))
            for p in glob.glob(os.path.join(args.dir, "*", "capacity-*.csv"))
        ]
    # Один набор — один раз: подкаталогов может быть столько же, сколько файлов внутри.
    seen = set()
    sets = [s for s in names if not (s in seen or seen.add(s))]
    if not sets:
        raise SystemExit(f"{args.dir}: наборов с capacity-*.csv нет")

    mk = load_markout(args.touches_from) if args.touches_from else {}
    out = []

    def add(s=""):
        out.append(s)

    add(f"# Порог «съели» (X) — замер F9 по `{args.dir}`")
    add()
    add(f"Полоса ноги: **{band[0]:g}–{band[1]:g} bps** ({band[0] / 100:.2f}–{band[1] / 100:.2f} % над стеной). "
        f"Размеры ноги: {', '.join(f'{u:.0f}' for u in usds)} $. Наборы: {', '.join(sets)}.")
    add()
    add("Правило исполнения ноги — как в `fill-capacity.py` (модуль `lob::capacity`): "
        "`clamp(наторговано на тике против нас за касание − очередь впереди, 0, нога)`; нога через спред "
        "(`tick ≥ opp_t0` у бида, `tick ≤ opp_t0` у аска) — не поставлена. Доли съеденного "
        "(знаменатель — `size_at_touch`): **A** — сумма `sold_touch` по тикам полосы, "
        "**B** — `sold_touch` на тике стены, **B(h)** — `sold_post<h>` на тике стены.")
    add()

    # --- Сбор ----------------------------------------------------------------
    data = {}
    snyali = {h: collections.Counter() for h in HORIZONS}
    for name in sets:
        touches, no_wall = read_touches(os.path.join(args.dir, name))
        rows = []
        stat = collections.Counter()
        stat["всего касаний"] = len(touches) + no_wall
        stat["без тика стены в выгрузке"] = no_wall
        for key, t in touches.items():
            size = t["size"]
            side = t["side"]
            if size <= 0:
                stat["размер стены 0"] += 1
                continue
            row0 = t["ticks"].get(0)
            offs, placeable = band_legs(t["ticks"], side, band)
            if not offs:
                stat["тиков полосы нет"] += 1
                continue
            n_off = len(offs)
            stat["тиков полосы: 1"] += 1 if n_off == 1 else 0
            stat["тиков полосы: 2–3"] += 1 if 2 <= n_off <= 3 else 0
            stat["тиков полосы: 4+"] += 1 if n_off >= 4 else 0
            if not placeable:
                stat["все ноги через спред"] += 1
                continue
            st = fill_stats(t["ticks"], placeable, main_usd)
            if st is None:
                continue
            n_fill, n_placed, _fill_usd, a_f, a_band = st
            if n_placed == 0:
                stat["размер ноги меньше лота"] += 1
                continue
            stat["нога ставится"] += 1
            if n_fill > 0:
                stat["нога исполнилась бы"] += 1
            b_wall = None
            b_h = {}
            if row0 is not None:
                q0 = int(row0["q_t0"])
                b_wall = int(row0["sold_touch"]) / size
                for h in HORIZONS:
                    col = f"sold_post{h}"
                    if col in row0 and q0 >= 0:
                        b_h[h] = int(row0[col]) / size
                if b_wall > 0:
                    stat["в стену продавали"] += 1
                for h in HORIZONS:
                    col = f"sold_post{h}"
                    if col not in row0:
                        snyali[h]["нет колонки"] += 1
                        continue
                    if q0 < 0:
                        snyali[h]["очередь t0 неизвестна"] += 1
                        continue
                    sold = int(row0[col])
                    snyali[h]["касаний с очередью"] += 1
                    if sold >= q0:
                        snyali[h]["очередь выбрана сделками"] += 1
                    else:
                        snyali[h]["очередь НЕ выбрана (снятие)"] += 1
                    if sold == 0:
                        snyali[h]["сделок не было вовсе"] += 1
            rows.append({
                "key": key, "side": side, "size": size, "ticks": t["ticks"],
                "placeable": placeable, "n_fill": n_fill, "a_f": a_f, "a_band": a_band,
                "b_wall": b_wall, "b_h": b_h,
            })
        data[name] = {"rows": rows, "stat": stat, "no_wall": no_wall}

    groups = [
        ("bid, возраст 45 мин (a45-bid + a45-bid-b4h-neg)", lambda n: n in ("a45-bid", "a45-bid-b4h-neg")),
        ("ask, возраст 45 мин (a45-ask)", lambda n: n == "a45-ask"),
        ("bid, возраст 15 мин (a15-bid)", lambda n: n == "a15-bid"),
        ("bid, все наборы", lambda n: "bid" in n),
        ("ask, все наборы", lambda n: "ask" in n),
        ("возраст 45 мин, все", lambda n: n.startswith("a45-")),
        ("возраст 15 мин, все", lambda n: n.startswith("a15-")),
        ("все наборы", lambda n: True),
    ]

    def sel(name, only_filled=False, only_traded=False):
        rows = data[name]["rows"] if name in data else []
        if only_filled:
            rows = [r for r in rows if r["n_fill"] > 0]
        if only_traded:
            rows = [r for r in rows if r["b_wall"] is not None and r["b_wall"] > 0]
        return rows

    def rows_of(pred, **kw):
        return [r for n in sets if n in data and pred(n) for r in sel(n, **kw)]

    def a_of(r, usd):
        if usd == main_usd and r["n_fill"] > 0:
            return r["a_f"]
        if usd == main_usd:
            return r["a_band"]
        st = fill_stats(r["ticks"], r["placeable"], usd)
        if st is None:
            return None
        return st[3] if r["n_fill"] > 0 else st[4]

    HEAD = "| доля | подвыборка | $ | n | p10 | **p20** | **p50** | p75 | p90 | доля с нулём |"
    SEP = "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"

    def qrow(unit, sample, xs, usd=None):
        u = "" if usd is None else f"{usd:.0f}"
        xs = sorted(xs)
        if not xs:
            return f"| {unit} | {sample} | {u} | 0 | — | — | — | — | — | — |"
        zero = sum(1 for x in xs if x == 0) / len(xs)
        return (f"| {unit} | {sample} | {u} | {len(xs)} | {fmt_q(quantile(xs, 0.10))} | "
                f"**{fmt_q(quantile(xs, 0.20))}** | **{fmt_q(quantile(xs, 0.50))}** | "
                f"{fmt_q(quantile(xs, 0.75))} | {fmt_q(quantile(xs, 0.90))} | {pct(zero)} |")

    # --- 1. По наборам --------------------------------------------------------
    add("## 1. Квантили по наборам")
    add()
    add("Подвыборки: **все** — «нога ставится»; **исп.** — «нога исполнилась бы»; "
        "**сделки** — «в стену продавали» (B > 0).")
    add()
    for name in sets:
        d = data.get(name)
        add(f"### Набор `{name}`")
        add()
        if not d or not d["rows"]:
            add("Строк в выборке нет — набор пропущен.")
            add()
            continue
        st = d["stat"]
        add(f"Касаний всего: **{st['всего касаний']}**, монет в выгрузке — "
            f"{len({r['key'][0] for r in d['rows']})}; «нога ставится» — **{st['нога ставится']}** "
            f"(выбыло: без тика стены — {st['без тика стены в выгрузке']}, тиков полосы нет — "
            f"{st['тиков полосы нет']}, все ноги через спред — {st['все ноги через спред']}, "
            f"размер ноги меньше лота — {st['размер ноги меньше лота']}). "
            f"Нога исполнилась бы — **{st['нога исполнилась бы']}** "
            f"({pct(st['нога исполнилась бы'] / max(1, st['нога ставится']))}); "
            f"в стену продавали — **{st['в стену продавали']}** "
            f"({pct(st['в стену продавали'] / max(1, st['нога ставится']))}). "
            f"Тиков полосы: 1 — {st['тиков полосы: 1']}, 2–3 — {st['тиков полосы: 2–3']}, "
            f"4+ — {st['тиков полосы: 4+']}.")
        add()
        add("**A — сумма `sold_touch` по тикам полосы / размер стены** (зависит от размера ноги)")
        add()
        add(HEAD)
        add(SEP)
        for u in usds:
            add(qrow("A", "все", [v for v in (a_of(r, u) for r in sel(name)) if v is not None], u))
            add(qrow("A", "исп.", [v for v in (a_of(r, u) for r in sel(name, only_filled=True)) if v is not None], u))
            add(qrow("A", "сделки", [v for v in (a_of(r, u) for r in sel(name, only_traded=True)) if v is not None], u))
        add()
        add("**B — `sold_touch` на тике стены / размер стены** (от размера ноги не зависит)")
        add()
        add(HEAD)
        add(SEP)
        add(qrow("B", "все", [r["b_wall"] for r in sel(name) if r["b_wall"] is not None], main_usd))
        add(qrow("B", "исп.", [r["b_wall"] for r in sel(name, only_filled=True) if r["b_wall"] is not None], main_usd))
        add(qrow("B", "сделки", [r["b_wall"] for r in sel(name, only_traded=True) if r["b_wall"] is not None], main_usd))
        add()
        add("**B(h) — `sold_post<h>` на тике стены / размер стены** (подвыборка «все»; "
            "верхняя оценка — горизонт не знает о смерти стены)")
        add()
        add(HEAD)
        add(SEP)
        for h in HORIZONS:
            add(qrow(f"B({h} с)", "все", [r["b_h"][h] for r in sel(name) if h in r["b_h"]], main_usd))
        add()

    # --- 2. Сводные срезы -----------------------------------------------------
    add(f"## 2. Квантили по срезам (размер ноги ${main_usd:.0f})")
    add()
    add("Срезы не пересекаются внутри таблицы по смыслу набора; «все наборы» — сумма.")
    add()
    for label, pred in groups:
        add(f"### {label}")
        add()
        add(HEAD)
        add(SEP)
        add(qrow("A", "все", [v for v in (a_of(r, main_usd) for r in rows_of(pred)) if v is not None], main_usd))
        add(qrow("A", "исп.", [v for v in (a_of(r, main_usd) for r in rows_of(pred, only_filled=True)) if v is not None], main_usd))
        add(qrow("B", "все", [r["b_wall"] for r in rows_of(pred) if r["b_wall"] is not None], main_usd))
        add(qrow("B", "исп.", [r["b_wall"] for r in rows_of(pred, only_filled=True) if r["b_wall"] is not None], main_usd))
        for h in (10, 300, 1800):
            add(qrow(f"B({h} с)", "все", [r["b_h"][h] for r in rows_of(pred) if h in r["b_h"]], main_usd))
        for h in (300, 1800):
            add(qrow(f"B({h} с)", "сделки", [r["b_h"][h] for r in rows_of(pred, only_traded=True) if h in r["b_h"]], main_usd))
        add()

    # --- 3. Markout по корзинам ----------------------------------------------
    add("## 3. Markout по корзинам доли съеденного (0 / 0–10 / 10–20 / 20–50 / 50+ %)")
    add()
    add("Выборка «нога ставится»; корзина — по доле **A** (максимум по исполнившимся ногам, если нога "
        "исполнилась; иначе по всей полосе) при данном размере ноги; и та же таблица по доле **B(300 с)** "
        "(рабочая форма порога). Markout — `touches-*.csv`, `m_1h`/`m_2h`; «модуль» — в знаке «в сторону "
        "отскока», «лонг» — то же в знаке сделки лонг (аск-стена × −1). Пустой срез — отдельный счётчик.")
    add()
    misses = collections.Counter()
    for u in usds:
        for form in ("A", "B(300 с)"):
            buckets = collections.defaultdict(list)
            for name in sets:
                for r in sel(name):
                    if form == "A":
                        ratio = a_of(r, u)
                    else:
                        ratio = r["b_h"].get(300)
                    if ratio is None:
                        continue
                    v = mk.get(r["key"])
                    if v is None:
                        if mk:
                            misses[f"{form}: касания нет в кэше touches"] += 1
                        continue
                    m1, m2 = v
                    if m1 is None and m2 is None:
                        misses[f"{form}: нет среза m_1h/m_2h"] += 1
                        continue
                    if m1 is None:
                        misses[f"{form}: m_1h пуст"] += 1
                    if m2 is None:
                        misses[f"{form}: m_2h пуст"] += 1
                    sgn = 1.0 if r["side"] == "bid" else -1.0
                    buckets[bucket(ratio)].append((m1, m2, sgn, r["side"]))
            add(f"### Доля {form}, размер ноги ${u:.0f}")
            add()
            add("| корзина | n | m_1h модуль | m_1h σ | m_1h медиана | m_2h модуль | m_2h σ | m_1h лонг | m_2h лонг | bid n | ask n |")
            add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
            for b in BUCKETS:
                xs = buckets.get(b, [])
                if not xs:
                    add(f"| {b} | 0 | — | — | — | — | — | — | — | 0 | 0 |")
                    continue
                m1 = [x[0] for x in xs if x[0] is not None]
                m2 = [x[1] for x in xs if x[1] is not None]
                lg1 = [x[0] * x[2] for x in xs if x[0] is not None]
                lg2 = [x[1] * x[2] for x in xs if x[1] is not None]
                nb = sum(1 for x in xs if x[3] == "bid")
                na = len(xs) - nb
                add(f"| {b} | {len(xs)} | {fmt1(statistics.mean(m1) if m1 else None)} | {fmt1(sd(m1))} | "
                    f"{fmt1(statistics.median(m1) if m1 else None)} | "
                    f"{fmt1(statistics.mean(m2) if m2 else None)} | {fmt1(sd(m2))} | "
                    f"{fmt1(statistics.mean(lg1) if lg1 else None)} | {fmt1(statistics.mean(lg2) if lg2 else None)} | "
                    f"{nb} | {na} |")
            add()
    if misses:
        add("Не в счёт: " + "; ".join(f"{k} — {v}" for k, v in sorted(misses.items())) + ".")
        add()

    # --- 4. «Сняли» -----------------------------------------------------------
    add("## 4. «Сняли» (В-75б): объясняется ли падение размера сделками")
    add()
    add("Тик стены, горизонты `post` от `t0` (`sold_post*` — наторговано против нас с `t0`). "
        "«Очередь выбрана сделками» = `sold_post<h> ≥ q_t0` — падение объяснимо сделками; "
        "«НЕ выбрана» = сделок меньше очереди — остаток ушёл снятием. Горизонт не знает о смерти "
        "стены, поэтому это верхняя граница. Выборка — «нога ставится».")
    add()
    add("| горизонт | касаний с известной очередью | очередь выбрана сделками | НЕ выбрана (снятие) | сделок не было вовсе | очередь t0 неизвестна |")
    add("|---|---:|---:|---:|---:|---:|")
    for h in HORIZONS:
        c = snyali[h]
        n = c.get("касаний с очередью", 0)
        if not n:
            add(f"| post{h} | 0 | — | — | — | {c.get('очередь t0 неизвестна', 0)} |")
            continue
        e = c.get("очередь выбрана сделками", 0)
        ne = c.get("очередь НЕ выбрана (снятие)", 0)
        z = c.get("сделок не было вовсе", 0)
        add(f"| post{h} | {n} | {e} ({pct(e / n)}) | {ne} ({pct(ne / n)}) | {z} | "
            f"{c.get('очередь t0 неизвестна', 0)} |")
    add()

    # --- 5. Ширина выборки ----------------------------------------------------
    add("## 5. Ширина выборки: сколько касаний даёт исполнившуюся ногу")
    add()
    add("| набор | нога ставится | исполнилась бы | доля | в стену продавали | исполнено $ медиана | исполнено $ максимум |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for name in sets:
        d = data.get(name)
        if not d or not d["rows"]:
            add(f"| {name} | 0 | 0 | — | 0 | — | — |")
            continue
        rows = d["rows"]
        fills = []
        for r in rows:
            st = fill_stats(r["ticks"], r["placeable"], main_usd)
            if st is not None:
                fills.append(st[2])
        n = len(rows)
        n_fill = sum(1 for r in rows if r["n_fill"] > 0)
        n_tr = sum(1 for r in rows if r["b_wall"] is not None and r["b_wall"] > 0)
        med = statistics.median(fills) if fills else None
        add(f"| {name} | {n} | {n_fill} | {pct(n_fill / n) if n else '—'} | {n_tr} | "
            f"{'—' if med is None else f'{med:.0f}'} | {'—' if not fills else f'{max(fills):.0f}'} |")
    # Итог по всем наборам вместе (наборы пересекаются по монетам и суткам, поэтому строки
    # «всего» и «исполнилась бы» здесь — сумма по наборам, а не по монетам).
    n = sum(len(data[name]["rows"]) for name in sets if name in data)
    n_fill = sum(sum(1 for r in data[name]["rows"] if r["n_fill"] > 0) for name in sets if name in data)
    n_tr = sum(sum(1 for r in data[name]["rows"] if r["b_wall"] is not None and r["b_wall"] > 0)
               for name in sets if name in data)
    add(f"| **все наборы** | {n} | {n_fill} | {pct(n_fill / n) if n else '—'} | {n_tr} | — | — |")
    add()

    add("## Чего в данных нет")
    add()
    add("- Размер стены **в динамике** в `capacity-*.csv` не выгружен: есть `size_at_touch` (на момент "
        "касания), `clear_ms` (момент, когда наторговано больше очереди `t0`) и "
        "`best_after_clear`/`opp_after_clear` (книга на первом кадре после). Поэтому «сняли» считается "
        "**через очередь** (`sold_post<h>` против `q_t0`), а не через падение размера: сколько именно "
        "стояло на стене в момент снятия и как размер падал по кадрам — не выводимо. Знаменатель всех "
        "долей — `size_at_touch`; размер стены к концу горизонта мог измениться, в данных это не видно.")
    add("- Сделки на тиках **выше** стены (полоса ноги) записаны только за окно касания (`sold_touch`); "
        "сделок полосы после конца касания нет (`sold_post*` ведётся для тиков, попавших в полосу, но "
        "не отделяет «стена стоит» от «стену проели»). Доля A и B — за окно касания; B(h) — "
        "наторгованное в стену с `t0` за `h` секунд, независимо от того, жива ли стена.")
    add("- `m_1h`/`m_2h` есть не у всех касаний (пустая строка — нет среза): такие посчитаны отдельным "
        "счётчиком в разделе 3. Касание — прокси входа: это момент, когда стена стала лучшей ценой, "
        "а не момент постановки ноги (замеров подходов F1/F2 здесь ещё нет).")
    add("- Полоса `--band` ограничена выгрузкой (`band=70` bps) и шагом тика монеты: у монет с крупным "
        "тиком в 2–10 bps может не попасть ни одного тика, и касание выпадает из выборки; у монет с "
        "мелким тиком в полосе десятки тиков, и доля A — сумма по ним. Самый частый повод выбывания — "
        "«все ноги через спред»: у мелкотиковых монет тик у фронтранера равен лучшему аску, и нога в "
        "полосе пересекла бы спред.")
    add("- «Нога исполнилась бы» — подвыборка единиц касаний (раздел 5): квантили на ней приведены для "
        "полноты буквального требования F9. Безусловные доли вырождены в ноль: в стену за касание "
        "обычно не продают вовсе, поэтому рабочие квантили — по B(h) и по подвыборке «в стену продавали».")
    add()

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
