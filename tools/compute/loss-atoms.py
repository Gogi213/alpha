#!/usr/bin/env python3
"""Атомы сделок сетки: одна строка = одна сделка формы, колонки — контекст, стена, судьба стены,
ход цены, вход/выход, поток-прокси (план `docs/plan/loss-plan-2026-09-22.md`, шаг 1; M18).

    python3 loss-atoms.py --grid-dir b5/f10fix-D20-5d/a45-bid --form <имя формы> \
        --touches study/touches --approaches study/approaches/D20 --regime study/regime \
        --root root --out study/loss-atoms-5d.csv

Источники и поля (все — только чтение кэшей):

| атом | источник | поле |
|---|---|---|
| ключ | `<grid-dir>/rounds.csv` | symbol, day_utc, t0_ns, dir, entry_px, exit_px, net_bps, reason, exit_ns, fill_frac, entry_vwap, legs_filled |
| A1 рынок | `<regime>/<day>.csv` по `minute_ms = floor(t0_ms/60000)*60000` | btc_ret_1h_bps, btc_ret_4h_bps, pool_ret_1h_bps, pool_ret_4h_bps, eth_ret_4h_bps |
| A2 стена | `<approaches>/<day>/approaches-<SYM>.csv`, строка `side=bid`, `arm_ms ≈ t0_ms` | age_ms, size_at_arm, arm_dist_bps, strength_w20_pct, flow_1h_lots; `wall_size_usd = size_at_arm × price_tick × tick_size` |
| A3 судьба | `<touches>/<day>/touches-<SYM>.csv`, та же `price_tick`, `start_ms ≥ arm_ms` | ended_by_death, traded_during, size_max_before, swept_lots, size_at_touch |
| A4 ход цены | `<touches>/<day>/mids1m-<SYM>.csv` (`mid2x` = 2×цена в тиках) | окна 5/15/60 мин от взвода, тейк 1:1 при стопе `--stop-pct` |
| A5 вход | `rounds.csv` + стена | `entry_vs_wall_bps`; времени входа в кругах нет — `arm_to_entry_min` пусто (счётчик) |
| A7 поток | первая версия — пусто; прокси из касания-предшественника | flow_proxy_* |
| A8 время | `t0_ns` | hour_utc |

Соглашения: `adverse_*`/`favour_*` — положительные bps **против** и **в пользу** позиции;
`pnl_usd = net_bps/1e4 × --order-usd`, **без** множителя `fill_frac` (В-83, полный лот; доля — колонкой).
Отсутствие строки в кэше — пустая ячейка и счётчик `missing_*` в stderr, не падение.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import os
import sys
from collections import Counter, defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

COLUMNS = [
    # ключ и деньги
    "symbol", "day", "t0_ns", "dir", "entry_px", "exit_px", "net_bps", "pnl_usd", "reason",
    "exit_ns", "hold_min", "legs_filled", "fill_frac",
    # A1 рынок
    "btc_1h", "btc_4h", "pool_1h", "pool_4h", "eth_4h",
    # A2 стена на входе
    "wall_age_min", "wall_size_lots", "wall_size_usd", "arm_dist_bps", "strength_w20",
    "flow_1h_lots", "repeat_before",
    # A3 судьба стены
    "wall_fate", "wall_fate_min", "size_at_exit_ratio",
    # A4 ход цены
    "adverse_5m", "adverse_15m", "adverse_60m", "favour_5m", "favour_15m", "favour_60m",
    "adverse_hold", "favour_hold", "reached_take", "mid_at_deadline_bps", "mid_at_fate_bps",
    # A5 вход
    "arm_to_entry_min", "entry_vs_wall_bps",
    # A7 поток (первая версия — пусто + прокси)
    "sell_15m_lots", "buy_15m_lots", "imbalance_15m",
    "flow_proxy_traded_during", "flow_proxy_frontrun_lots", "flow_proxy_swept_lots",
    # A8 время/монета
    "hour_utc",
]

MISSES = Counter()


def miss(reason: str, n: int = 1) -> None:
    MISSES[reason] += n


def fnum(v) -> float | None:
    """Число из строки CSV или из уже посчитанного значения (None/пусто → None)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def inum(v: str) -> int | None:
    f = fnum(v)
    return None if f is None else int(f)


def read_csv(path: str) -> list[dict]:
    """CSV с комментариями `#` в шапке — как пишет сетка."""
    if not os.path.exists(path):
        miss("file_missing")
        return []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return [r for r in csv.DictReader(l for l in f if not l.startswith("#"))]


# --- загрузчики -----------------------------------------------------------------


def load_rounds(grid_dir: str, form: str) -> list[dict]:
    rows = [r for r in read_csv(os.path.join(grid_dir, "rounds.csv")) if r.get("form") == form]
    rows.sort(key=lambda r: int(r["t0_ns"]))
    return rows


def load_tick_sizes(root: str) -> dict[str, float]:
    out = {}
    for r in read_csv(os.path.join(root, "instruments.csv")):
        t = fnum(r.get("tick_size"))
        if t:
            out[r["symbol"]] = t
    return out


def load_regime(regime_dir: str, day: str) -> dict[int, dict]:
    return {inum(r["minute_ms"]): r for r in read_csv(os.path.join(regime_dir, f"{day}.csv"))
            if inum(r["minute_ms"]) is not None}


def load_approaches(approaches_dir: str, day: str, sym: str) -> list[dict]:
    path = os.path.join(approaches_dir, day, f"approaches-{sym}.csv")
    return [r for r in read_csv(path) if r.get("side") == "bid"]


def load_touches(touches_dir: str, day: str, sym: str) -> dict[tuple[str, int], list[dict]]:
    """Индекс касаний по (side, price_tick), внутри — по возрастанию start_ms."""
    idx: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in read_csv(os.path.join(touches_dir, day, f"touches-{sym}.csv")):
        pt = inum(r.get("price_tick"))
        if pt is not None:
            idx[(r.get("side", ""), pt)].append(r)
    for v in idx.values():
        v.sort(key=lambda r: int(r["start_ms"]))
    return idx


def load_mids(touches_dir: str, day: str, sym: str) -> tuple[list[int], list[int]]:
    mins, mids = [], []
    for r in read_csv(os.path.join(touches_dir, day, f"mids1m-{sym}.csv")):
        m, v = inum(r.get("minute_ms")), inum(r.get("mid2x"))
        if m is not None and v is not None:
            mins.append(m)
            mids.append(v)
    return mins, mids


# --- атомы ----------------------------------------------------------------------


def pick_wall(cands: list[dict], entry: float, tick: float, min_age_ms: float | None = None) -> dict | None:
    """При нескольких стенах на одном взводе берём ту, что ближе к цене входа (16 кругов из 128).

    Аудит дизайна 22.09 §5 Т4: кандидаты сперва отбираются по правилу набора (возраст стены ≥
    `min_age_ms`, как `--set …:age=`) — иначе ближайшей к входу оказывалась чужая молодая стена
    того же взвода, и 9 сделкам набора «возраст ≥ 45 мин» приписывался возраст ≈ 0 вместе с её
    размером и судьбой. Если правилу не отвечает ни один кандидат — старое поведение и пометка.
    """
    if not cands:
        return None
    if min_age_ms is not None:
        ok = [a for a in cands if (fnum(a.get("age_ms")) or 0.0) >= min_age_ms]
        if ok:
            cands = ok
        else:
            miss("wall_below_set_age")
    if len(cands) > 1:
        miss("wall_ambiguous")
    return min(cands, key=lambda a: abs(int(a["price_tick"]) * tick - entry))


def wall_fate(touches: list[dict], arm_ms: int, exit_ms: int) -> tuple[str, float | None, float | None, dict | None]:
    """Что стена сделала после взвода: стояла / сняли / съели / пробили; минуты до события."""
    after = [t for t in touches if int(t["start_ms"]) >= arm_ms]
    if not after:
        return "нет_касаний", None, None, None
    t = after[0]
    start = int(t["start_ms"])
    if start > exit_ms:
        return "стояла", (start - arm_ms) / 60000.0, None, t
    size_max = fnum(t.get("size_max_before")) or 0.0
    ratio = (fnum(t.get("traded_during")) or 0.0) / size_max if size_max else 0.0
    if (t.get("ended_by_death") or "").lower() == "true":
        return ("съели" if ratio >= 0.5 else "сняли"), (start - arm_ms) / 60000.0, ratio, t
    swept, size_touch = fnum(t.get("swept_lots")) or 0.0, fnum(t.get("size_at_touch")) or 0.0
    if size_touch and swept >= size_touch:
        return "пробили", (start - arm_ms) / 60000.0, ratio, t
    return "жила", (start - arm_ms) / 60000.0, ratio, t


def path_atoms(mins: list[int], mids: list[int], t0_ms: int, exit_ms: int, entry: float,
               dir_: int, stop_pct: float, tick: float) -> dict:
    """Ход цены от взвода: против/в пользу за окна, дошла ли до тейка, где была к дедлайну.

    `mid2x` в кэше — 2×цена в тиках, поэтому цена = `mid2x / 2 × tick_size`; отношение к входу
    считается от цены, иначе bps завышаются в 1/tick раз.
    """
    out = {k: None for k in ("adverse_5m", "adverse_15m", "adverse_60m",
                             "favour_5m", "favour_15m", "favour_60m",
                             "adverse_hold", "favour_hold",
                             "reached_take", "mid_at_deadline_bps")}
    if not mins or not entry:
        miss("no_mids")
        return out
    i = bisect.bisect_right(mins, t0_ms) - 1
    if i < 0:
        miss("no_mid_anchor")
        return out

    def to_bps(mid2x: int) -> float:
        return (mid2x / 2.0 * tick / entry - 1.0) * 1e4 * dir_

    for w in (5, 15, 60):
        j = bisect.bisect_right(mins, t0_ms + w * 60000) - 1
        vals = [to_bps(m) for m in mids[i:j + 1]]
        out[f"adverse_{w}m"] = round(max(0.0, -min(vals)), 4)
        out[f"favour_{w}m"] = round(max(0.0, max(vals)), 4)
    j = bisect.bisect_right(mins, exit_ms) - 1
    if j >= i:
        held = [to_bps(m) for m in mids[i:j + 1]]
        out["adverse_hold"] = round(max(0.0, -min(held)), 4)
        out["favour_hold"] = round(max(0.0, max(held)), 4)
        out["reached_take"] = int(max(held) >= stop_pct * 100.0)
        out["mid_at_deadline_bps"] = round(to_bps(mids[j]), 4)
    return out


def mid_at(mins: list[int], mids: list[int], at_ms: int, entry: float, dir_: int, tick: float) -> float | None:
    """Цена середины в момент `at_ms` в bps от входа — для «что дал бы выход по снятию стены»."""
    if not mins or not entry:
        return None
    i = bisect.bisect_right(mins, at_ms) - 1
    if i < 0:
        return None
    return round((mids[i] / 2.0 * tick / entry - 1.0) * 1e4 * dir_, 4)


def atoms_for_round(r: dict, ctx: dict) -> dict:
    day, sym = r["day_utc"], r["symbol"]
    t0_ms = int(r["t0_ns"]) // 1_000_000
    exit_ms = int(r["exit_ns"]) // 1_000_000
    dir_ = int(r["dir"])
    tick = ctx["ticks"].get(sym)
    entry = fnum(r.get("entry_vwap")) or fnum(r.get("entry_px")) or 0.0
    net = float(r["net_bps"])
    fill_frac = fnum(r.get("fill_frac"))
    row = {c: "" for c in COLUMNS}
    row.update(
        symbol=sym, day=day, t0_ns=int(r["t0_ns"]), dir=dir_,
        entry_px=fnum(r.get("entry_px")), exit_px=fnum(r.get("exit_px")),
        net_bps=net, pnl_usd=round(net / 1e4 * ctx["order_usd"], 4),
        reason=r.get("reason", ""), exit_ns=int(r["exit_ns"]),
        hold_min=round((int(r["exit_ns"]) - int(r["t0_ns"])) / 6e10, 4),
        legs_filled=inum(r.get("legs_filled")), fill_frac=fill_frac,
        hour_utc=(t0_ms // 3_600_000) % 24,
    )

    # A1 контекст рынка по минуте взвода
    reg = ctx["regime"].get(day, {})
    minute = (t0_ms // 60000) * 60000
    rr = reg.get(minute)
    if rr is None:
        miss("missing_regime")
    else:
        for col, key in (("btc_1h", "btc_ret_1h_bps"), ("btc_4h", "btc_ret_4h_bps"),
                         ("pool_1h", "pool_ret_1h_bps"), ("pool_4h", "pool_ret_4h_bps"),
                         ("eth_4h", "eth_ret_4h_bps")):
            v = fnum(rr.get(key))
            if v is None:
                miss("regime_empty")
            else:
                row[col] = v

    if tick is None:
        miss("no_tick_size")
        tick = 0.0
    wall = None
    if tick:
        cands = [a for a in ctx["approaches"].get((day, sym), [])
                 if abs(int(a["arm_ms"]) - t0_ms) <= 1]
        wall = pick_wall(cands, entry, tick, ctx.get("min_age_ms"))
    if wall is None:
        miss("missing_approach")
        touches_idx = ctx["touches"].get((day, sym), {})
        row.update(wall_fate="нет_стены")
    else:
        pt = int(wall["price_tick"])
        wall_px = pt * tick
        row.update(
            wall_age_min=round((fnum(wall.get("age_ms")) or 0.0) / 60000.0, 4),
            wall_size_lots=fnum(wall.get("size_at_arm")),
            wall_size_usd=round((fnum(wall.get("size_at_arm")) or 0.0) * wall_px, 4),
            arm_dist_bps=fnum(wall.get("arm_dist_bps")),
            strength_w20=fnum(wall.get("strength_w20_pct")),
            flow_1h_lots=fnum(wall.get("flow_1h_lots")),
            entry_vs_wall_bps=round((entry - wall_px) / wall_px * 1e4, 4) if wall_px else "",
        )
        touches = ctx["touches"].get((day, sym), {}).get(("bid", pt), [])
        before = [t for t in touches if int(t["start_ms"]) < t0_ms]
        row["repeat_before"] = len(before)
        fate, fate_min, ratio, ev = wall_fate(touches, t0_ms, exit_ms)
        row.update(wall_fate=fate, wall_fate_min=None if fate_min is None else round(fate_min, 4),
                   size_at_exit_ratio=None if ratio is None else round(ratio, 4))
        if before:
            p = before[-1]
            row.update(flow_proxy_traded_during=fnum(p.get("traded_during")),
                       flow_proxy_frontrun_lots=fnum(p.get("frontrun_lots")),
                       flow_proxy_swept_lots=fnum(p.get("swept_lots")))
        _ = ev

    # A4 ход цены
    mins, mids = ctx["mids"].get((day, sym), ([], []))
    row.update(path_atoms(mins, mids, t0_ms, exit_ms, entry, dir_, ctx["stop_pct"], tick))
    # цена в момент события со стеной — «что дал бы выход по снятию стены» (план шаг 2, п. 3)
    fm = fnum(row.get("wall_fate_min"))
    if fm is not None and tick and row.get("wall_fate") not in ("", None, "нет_касаний", "нет_стены"):
        row["mid_at_fate_bps"] = mid_at(mins, mids, t0_ms + int(fm * 60000), entry, dir_, tick)
    miss("no_arm_to_entry_time")  # в rounds.csv времени входа нет — честно пусто
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Атомы сделок сетки (M18, loss-plan шаг 1)")
    ap.add_argument("--grid-dir", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--touches", default="study/touches")
    ap.add_argument("--approaches", default="study/approaches/D20")
    ap.add_argument("--regime", default="study/regime")
    ap.add_argument("--root", default="root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--order-usd", type=float, default=1000.0)
    ap.add_argument("--stop-pct", type=float, default=2.0)
    ap.add_argument("--min-age-secs", type=float, default=None,
                    help="возраст стены набора (как --set …:age=): отбор стены взвода по правилу набора")
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать, печатать сводку")
    a = ap.parse_args(argv)

    rounds = load_rounds(a.grid_dir, a.form)
    if not rounds:
        print(f"loss-atoms: в {a.grid_dir}/rounds.csv нет строк формы {a.form}", file=sys.stderr)
        return 1

    ctx = {
        "ticks": load_tick_sizes(a.root),
        "order_usd": a.order_usd,
        "stop_pct": a.stop_pct,
        "min_age_ms": a.min_age_secs * 1000.0 if a.min_age_secs is not None else None,
        "regime": {},
        "approaches": {},
        "touches": {},
        "mids": {},
    }
    for day in sorted({r["day_utc"] for r in rounds}):
        ctx["regime"][day] = load_regime(a.regime, day)
    for day, sym in sorted({(r["day_utc"], r["symbol"]) for r in rounds}):
        ctx["approaches"][(day, sym)] = load_approaches(a.approaches, day, sym)
        ctx["touches"][(day, sym)] = load_touches(a.touches, day, sym)
        ctx["mids"][(day, sym)] = load_mids(a.touches, day, sym)

    rows = [atoms_for_round(r, ctx) for r in rounds]
    pnl = sum(float(x["pnl_usd"]) for x in rows)
    print(f"loss-atoms: сделок {len(rows)} · P&L полный лот ${pnl:.2f} · "
          f"минус {sum(1 for x in rows if float(x['pnl_usd']) < 0)} · "
          f"stop {sum(1 for x in rows if x['reason'] == 'stop')} · "
          f"deadline {sum(1 for x in rows if x['reason'] == 'deadline')}", file=sys.stderr)
    if MISSES:
        print("loss-atoms miss: " + ", ".join(f"{k}={v}" for k, v in sorted(MISSES.items())),
              file=sys.stderr)
    if a.dry_run:
        print("loss-atoms: dry-run, файл не пишется", file=sys.stderr)
        return 0
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"loss-atoms: {a.out} ({len(rows)} строк)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
