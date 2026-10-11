#!/usr/bin/env python3
"""Ёмкость исполнения входа у стены (S10 шаг 1, side-plan §S10) — читатель `lob fill-capacity`.

    python3 tools/compute/fill-capacity.py --dir study/capacity/a45-bid \\
            --usd 200,500,1000 --ladder 1@fr --ladder 1@0 --ladder 3@20..70 --ladder 2@fr..50 \\
            [--slot t0 --slot pre60] [--top 12] [--out fill-capacity.md]

Правило исполнения — консервативное, объёмом (модуль `lob::capacity`): нога на тике `k`, поставленная
в слот `s`, исполнена на `clamp(sold − queue, 0, нога)`, где для `t0` `sold = sold_touch`, `queue = q_t0`,
для `pre<с>` — `sold = sold_pre + sold_touch`, `queue = q_pre`, для `post<с>` (постановка в `t0`, заявка
живёт `с` секунд от старта касания независимо от его конца) — `sold = sold_post`, `queue = q_t0`. Снятия чужих заявок впереди нас не
помогают (обратное тому, что делает `RiskAdverseQueueModel`, — `EXPERIMENTS.md` M13).

Лестница `N@from..to` — `N` ног равными долями `$X / N` по тикам от `from` до `to` bps от стены
(`fr` — тик первого фронтранера касания, `frontrun_off`; касание без фронтрана при `fr` выбывает;
`mkt` — последний тик перед лучшей ценой другой стороны на момент постановки, то есть самая дальняя
от стены нога, которую тогда можно было поставить мейкером); `1@0` — одна нога в упор; ноги, попавшие
на один тик, складываются. Нога по нашу сторону от лучшей
цены **другой** стороны на момент постановки пересекла бы спред — считается «не поставлена»
(пост-онли отверг бы), её доля из размера выпадает; доля таких ног печатается. Очередь `-1` (книги
на момент нет) — касание в этом слоте выбывает и считается отдельно.

Печатает markdown: (1) слот × лестница × размер — касаний, исполнено хоть что-то (%), исполнено
целиком (≥ 99 %, %), средняя и медианная доля размера, медиана исполненного $, доля ног через спред;
(2) по монетам для `--focus` (по умолчанию первая лестница, первый слот, последний размер) — касаний,
хоть что-то (%), средняя доля; (3) по дням. Интервалов не строит — это срез ёмкости, не вердикт.
"""
import argparse
import collections
import csv
import glob
import math
import os
import re
import statistics
import sys


def parse_ladder(spec):
    """`N@a..b` | `N@a` | `N@fr..b` | `N@fr` → (n, from, to), где from/to — число bps или "fr"."""
    m = re.fullmatch(r"(\d+)@(fr|mkt|-?\d+(?:\.\d+)?)(?:\.\.(fr|mkt|-?\d+(?:\.\d+)?))?", spec)
    if not m:
        raise SystemExit(f"--ladder {spec!r}: ожидается N@from[..to], from/to — bps, fr или mkt")
    n = int(m.group(1))
    if n < 1:
        raise SystemExit(f"--ladder {spec!r}: ног не меньше одной")
    a = m.group(2)
    b = m.group(3) if m.group(3) is not None else a
    conv = lambda x: x if x in ("fr", "mkt") else float(x)
    return n, conv(a), conv(b)


def leg_offsets(ladder, price_tick, frontrun_off, mkt_off):
    """Смещения ног в тиках от стены (могут совпадать); None — лестница на этом касании не строится
    (нет фронтрана при `fr`, нет книги при `mkt`)."""
    n, a, b = ladder

    def off(x):
        if x == "fr":
            return None if frontrun_off is None else frontrun_off
        if x == "mkt":
            return None if mkt_off is None else max(0, mkt_off)
        # bps → тики, ближайший целый; не ближе стены (нога за стеной — уже стоп-зона).
        return max(0, int(round(price_tick * x / 10_000.0)))

    oa, ob = off(a), off(b)
    if oa is None or ob is None:
        return None
    if n == 1:
        return [oa]
    return [int(round(oa + (ob - oa) * i / (n - 1))) for i in range(n)]


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def touches_of(rows):
    """Строки одного файла → {(day, side, price_tick, start_ms): {off: row, ...}} с порядком файла."""
    out = collections.OrderedDict()
    for r in rows:
        key = (r["day"], r["side"], int(r["price_tick"]), int(r["start_ms"]))
        out.setdefault(key, {})[int(r["off"])] = r
    return out


def slot_cols(header):
    slots = ["t0"]
    for h in header:
        m = re.fullmatch(r"q_(pre|post)(\d+)", h)
        if m:
            slots.append(f"{m.group(1)}{m.group(2)}")
    return slots


def fill_for(ticks, slot, ladder, usd, tick_px, lot_qty):
    """Исполнение лестницы на касании: (доля размера, исполнено $, доля ног через спред) или None
    (нет книги на момент / нет фронтрана). `ticks` — {off: row} касания."""
    any_row = next(iter(ticks.values()))
    price_tick = int(any_row["price_tick"])
    fr = any_row["frontrun_off"]
    frontrun_off = int(fr) if fr not in ("", None) else None
    side = any_row["side"]
    is_post = slot.startswith("post")
    # У `post` очередь и лучшие цены — снимок `t0` (постановка в `t0`), сделки — своё окно.
    q_col = "q_t0" if slot == "t0" or is_post else f"q_{slot}"
    opp_col = "opp_t0" if slot == "t0" or is_post else f"opp_{slot}"
    sold_col = f"sold_{slot}" if is_post else "sold_touch"
    sold_pre_col = None if slot == "t0" or is_post else f"sold_{slot}"
    opp_at = int(any_row[opp_col])
    sgn = 1 if side == "bid" else -1
    mkt_off = None if opp_at < 0 else (opp_at - price_tick) * sgn - 1
    offs = leg_offsets(ladder, price_tick, frontrun_off, mkt_off)
    if offs is None:
        return None
    n = len(offs)
    leg_usd = usd / n
    per_tick = collections.Counter(offs)
    filled_usd = 0.0
    crossed = 0
    placed_usd = 0.0
    for off, legs in per_tick.items():
        row = ticks.get(off)
        if row is None:
            # Нога дальше полосы записи — данных нет: считаем как не поставленную (сообщается долей).
            crossed += legs
            continue
        q = int(row[q_col])
        if q < 0:
            return None
        tick = int(row["tick"])
        opp = int(row[opp_col])
        # Пересечение спреда: у бида нога не ниже лучшего аска, у аска — не выше лучшего бида.
        if opp >= 0 and ((side == "bid" and tick >= opp) or (side == "ask" and tick <= opp)):
            crossed += legs
            continue
        px = tick * tick_px
        want_usd = leg_usd * legs
        want_lots = math.floor(want_usd / (px * lot_qty))
        if want_lots <= 0:
            continue
        placed_usd += want_lots * px * lot_qty
        sold = int(row[sold_col]) + (int(row[sold_pre_col]) if sold_pre_col else 0)
        got = max(0, min(want_lots, sold - q))
        filled_usd += got * px * lot_qty
    frac = filled_usd / usd if usd > 0 else 0.0
    return frac, filled_usd, crossed / n


def fmt_pct(x):
    return f"{100 * x:.0f} %"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="каталог набора: capacity-<SYMBOL>.csv")
    ap.add_argument("--usd", default="200,500,1000", help="размеры позиции, $ через запятую")
    ap.add_argument("--ladder", action="append", default=[], help="лестница N@from[..to] (повторяемый)")
    ap.add_argument("--slot", action="append", default=[], help="слоты t0|pre<с> (повторяемый; пусто — все из файла)")
    ap.add_argument("--focus", default=None, help="разрез по монетам/дням: `<лестница>|<слот>|<usd>`")
    ap.add_argument("--top", type=int, default=12, help="строк в срезе по монетам")
    ap.add_argument("--out", default=None, help="куда писать markdown (пусто — stdout)")
    args = ap.parse_args()
    usds = [float(x) for x in args.usd.split(",") if x]
    ladders = args.ladder or ["1@fr", "1@0", "3@20..70", "2@fr..50"]
    parsed = [(l, parse_ladder(l)) for l in ladders]

    files = sorted(glob.glob(os.path.join(args.dir, "capacity-*.csv")))
    if not files:
        raise SystemExit(f"{args.dir}: файлов capacity-*.csv нет")
    slots = None
    # (slot, ladder, usd) → список (frac, usd_filled, crossed_share); плюс счётчики выбывших.
    res = collections.defaultdict(list)
    dropped = collections.Counter()
    per_coin = collections.defaultdict(lambda: collections.defaultdict(list))
    per_day = collections.defaultdict(lambda: collections.defaultdict(list))
    n_touches = 0
    for path in files:
        rows = read_rows(path)
        if not rows:
            continue
        header = list(rows[0].keys())
        file_slots = slot_cols(header)
        if slots is None:
            slots = [s for s in (args.slot or file_slots) if s in file_slots]
            missing = [s for s in args.slot if s not in file_slots]
            if missing:
                raise SystemExit(f"{path}: слотов {missing} в файле нет, есть {file_slots}")
        symbol = rows[0]["symbol"]
        tick_px = float(rows[0]["tick_px"])
        lot_qty = float(rows[0]["lot_qty"])
        for key, ticks in touches_of(rows).items():
            n_touches += 1
            day = key[0]
            for slot in slots:
                for name, lad in parsed:
                    for usd in usds:
                        r = fill_for(ticks, slot, lad, usd, tick_px, lot_qty)
                        k = (slot, name, usd)
                        if r is None:
                            dropped[k] += 1
                            continue
                        res[k].append(r)
                        per_coin[k][symbol].append(r[0])
                        per_day[k][day].append(r[0])

    out = []
    out.append(f"# Ёмкость исполнения: `{args.dir}` — касаний {n_touches}, файлов {len(files)}, "
               f"слоты {', '.join(slots or [])}, лестницы {', '.join(ladders)}, размеры {args.usd} $")
    out.append("")
    out.append("Правило: исполнено = clamp(наторговано на тике против нас − очередь впереди на момент постановки, 0, нога); "
               "снятия чужих заявок не помогают. «Целиком» — ≥ 99 % размера. «Через спред» — средняя доля ног, "
               "которые на момент постановки лежали бы по нашу сторону от лучшей цены другой стороны (не поставлены).")
    out.append("")
    out.append("| слот | лестница | $ | касаний | выбыло | хоть что-то | целиком | доля ср. | доля мед. | исполнено $ мед. | через спред |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for slot in slots or []:
        for name, _ in parsed:
            for usd in usds:
                k = (slot, name, usd)
                xs = res.get(k, [])
                d = dropped.get(k, 0)
                if not xs:
                    out.append(f"| {slot} | {name} | {usd:.0f} | 0 | {d} | — | — | — | — | — | — |")
                    continue
                fr = [x[0] for x in xs]
                any_ = sum(1 for f in fr if f > 0) / len(fr)
                full = sum(1 for f in fr if f >= 0.99) / len(fr)
                cross = statistics.mean(x[2] for x in xs)
                out.append(
                    f"| {slot} | {name} | {usd:.0f} | {len(xs)} | {d} | {fmt_pct(any_)} | {fmt_pct(full)} | "
                    f"{fmt_pct(statistics.mean(fr))} | {fmt_pct(statistics.median(fr))} | "
                    f"{statistics.median(x[1] for x in xs):.0f} | {fmt_pct(cross)} |"
                )
    # Разрез по монетам и дням.
    if args.focus:
        parts = args.focus.split("|")
        if len(parts) != 3:
            raise SystemExit("--focus: `<лестница>|<слот>|<usd>`")
        focus = (parts[1], parts[0], float(parts[2]))
    else:
        focus = ((slots or ["t0"])[0], ladders[0], usds[-1])
    if focus in per_coin:
        out.append("")
        out.append(f"## По монетам — {focus[1]}, слот {focus[0]}, ${focus[2]:.0f} (топ по касаниям)")
        out.append("")
        out.append("| монета | касаний | хоть что-то | целиком | доля ср. |")
        out.append("|---|---:|---:|---:|---:|")
        coins = sorted(per_coin[focus].items(), key=lambda kv: -len(kv[1]))[: args.top]
        for sym, fr in coins:
            out.append(
                f"| {sym} | {len(fr)} | {fmt_pct(sum(1 for f in fr if f > 0) / len(fr))} | "
                f"{fmt_pct(sum(1 for f in fr if f >= 0.99) / len(fr))} | {fmt_pct(statistics.mean(fr))} |"
            )
        out.append("")
        out.append(f"## По дням — {focus[1]}, слот {focus[0]}, ${focus[2]:.0f}")
        out.append("")
        out.append("| сутки | касаний | хоть что-то | целиком | доля ср. |")
        out.append("|---|---:|---:|---:|---:|")
        for day, fr in sorted(per_day[focus].items()):
            out.append(
                f"| {day} | {len(fr)} | {fmt_pct(sum(1 for f in fr if f > 0) / len(fr))} | "
                f"{fmt_pct(sum(1 for f in fr if f >= 0.99) / len(fr))} | {fmt_pct(statistics.mean(fr))} |"
            )
    text = "\n".join(out) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"записано {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
