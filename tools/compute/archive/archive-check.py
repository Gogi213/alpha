#!/usr/bin/env python3
"""Сверка архива стакана Bybit с нашей записью (M21, шаг 0 задачи L11; 2026-09-22).

Архив: `https://quote-saver.bycsi.com/orderbook/linear/<SYM>/<день>_<SYM>_ob200.data.zip` (поток WS
`orderbook.200`: снимок + дельты) и сделки `https://public.bybit.com/trading/<SYM>/<SYM><день>.csv.gz`.
Наша запись — производные кэша ночи той же монеты-суток (из потока `.50`): минутные середины
`study/touches/<день>/mids1m-<SYM>.csv`, касания `touches-<SYM>.csv`, число сделок из сводки сверки
`root/verify-logs/<день>.log`.

Что сверяется:
1. целостность архива: номера обновлений `u` подряд, перекрёстный стакан (бид ≥ аск) в точках проверки;
2. середина на конец каждой минуты: архив (стакан, восстановленный по дельтам) против `mids1m` — в тиках;
3. каждое наше касание стены: стоит ли в архиве на этой цене уровень лучшей ценой в окне ±100 мс от
   `start_ms`, и его размер против нашего `size_at_touch` (в лотах);
4. сделки: число строк архива против `trades=` нашей сверки.

    python3 bin/archive-check.py --day 2026-09-21 --symbols WIFUSDT ATOMUSDT LTCUSDT ONDOUSDT [--keep]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import statistics as st
import sys
import urllib.request
import zipfile

OB = "https://quote-saver.bycsi.com/orderbook/linear/{s}/{d}_{s}_ob200.data.zip"
TR = "https://public.bybit.com/trading/{s}/{s}{d}.csv.gz"
WIN_MS = 100  # окно по времени касания: шаг потока .200 — 100 мс


def fetch(url: str, path: str) -> str:
    if not os.path.exists(path):
        urllib.request.urlretrieve(url, path)
    return path


def instruments(root: str) -> dict[str, dict]:
    with open(os.path.join(root, "instruments.csv"), encoding="utf-8") as f:
        return {r["symbol"]: r for r in csv.DictReader(f)}


def best(book: dict[float, float], bid: bool) -> float | None:
    if not book:
        return None
    return max(book) if bid else min(book)


def check(symbol: str, day: str, root: str, study: str, tmp: str) -> dict:
    ins = instruments(root)[symbol]
    tick = float(ins["tick_size"])
    step = float(ins["qty_step"])
    zp = fetch(OB.format(s=symbol, d=day), os.path.join(tmp, f"{symbol}-{day}-ob200.zip"))
    tp = fetch(TR.format(s=symbol, d=day), os.path.join(tmp, f"{symbol}-{day}-trades.csv.gz"))

    # наши точки проверки
    mids = {}
    with open(os.path.join(study, "touches", day, f"mids1m-{symbol}.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            mids[int(r["minute_ms"])] = int(r["mid2x"])
    touches = []
    with open(os.path.join(study, "touches", day, f"touches-{symbol}.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            touches.append((int(r["start_ms"]), r["side"], int(r["price_tick"]), int(r["size_at_touch"])))
    touches.sort()
    minute_ends = sorted(m + 60_000 for m in mids)

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    n_msg = n_snap = u_gaps = crossed = 0
    last_u = None
    arch_mid: dict[int, int] = {}
    # касание: [лучшая на этой цене в окне?, размер на цене в момент start]
    t_res: list[list] = [[False, None] for _ in touches]
    ti = 0  # первое касание, чьё окно ещё не закрыто
    mi = 0
    z = zipfile.ZipFile(zp)
    with z.open(z.namelist()[0]) as fh:
        for line in fh:
            d = json.loads(line)
            ts = d["ts"]
            data = d["data"]
            # минуты, закончившиеся до этого сообщения: состояние — до его применения
            while mi < len(minute_ends) and ts >= minute_ends[mi]:
                bb, ba = best(bids, True), best(asks, False)
                if bb is not None and ba is not None:
                    if bb >= ba:
                        crossed += 1
                    arch_mid[minute_ends[mi] - 60_000] = round(bb / tick) + round(ba / tick)
                mi += 1
            if d["type"] == "snapshot":
                n_snap += 1
                bids = {float(p): float(q) for p, q in data["b"]}
                asks = {float(p): float(q) for p, q in data["a"]}
            else:
                for side_book, rows in ((bids, data["b"]), (asks, data["a"])):
                    for p, q in rows:
                        fq = float(q)
                        if fq == 0:
                            side_book.pop(float(p), None)
                        else:
                            side_book[float(p)] = fq
            u = data.get("u")
            if last_u is not None and u is not None and d["type"] == "delta" and u != last_u + 1:
                u_gaps += 1
            last_u = u
            n_msg += 1
            # касания, чьё окно [start − WIN, start + WIN] покрывает это состояние
            while ti < len(touches) and touches[ti][0] + WIN_MS < ts:
                ti += 1
            j = ti
            while j < len(touches) and touches[j][0] - WIN_MS <= ts:
                start, side, pt, _ = touches[j]
                book = bids if side == "bid" else asks
                b = best(book, side == "bid")
                if b is not None and round(b / tick) == pt:
                    t_res[j][0] = True
                if ts <= start or t_res[j][1] is None:
                    q = book.get(round(pt * tick, 10), None)
                    if q is None:
                        # цена ключом float: ищем ближайший ключ в пределах полутика
                        for p0, q0 in book.items():
                            if abs(p0 / tick - pt) < 0.5:
                                q = q0
                                break
                    t_res[j][1] = round((q or 0.0) / step)
                j += 1

    # середина по минутам
    common = [m for m in mids if m in arch_mid]
    diffs = [abs(mids[m] - arch_mid[m]) / 2 for m in common]  # mid2x → тики середины
    eq = sum(1 for x in diffs if x == 0)
    le1 = sum(1 for x in diffs if x <= 1)
    # касания
    n_t = len(touches)
    at_best = sum(1 for r in t_res if r[0])
    ratios = [r[1] / t[3] for r, t in zip(t_res, touches) if t[3] > 0 and r[1] is not None]
    within20 = sum(1 for x in ratios if abs(x - 1) <= 0.2)
    # сделки
    with gzip.open(tp, "rt", encoding="utf-8") as f:
        n_trades = sum(1 for _ in f) - 1
    ours = None
    vlog = os.path.join(root, "verify-logs", f"{day}.log")
    if os.path.exists(vlog):
        with open(vlog, encoding="utf-8", errors="replace") as f:
            for l in f:
                m = re.match(rf"verify: {symbol} status=\S+ .*?trades=(\d+)", l)
                if m:
                    ours = int(m.group(1))
    return {
        "symbol": symbol, "messages": n_msg, "snapshots": n_snap, "u_gaps": u_gaps, "crossed_minutes": crossed,
        "minutes": len(common), "mid_equal": eq / len(common) if common else None,
        "mid_within_1tick": le1 / len(common) if common else None, "mid_max_diff_ticks": max(diffs) if diffs else None,
        "touches": n_t, "touch_at_best": at_best / n_t if n_t else None,
        "size_within_20pct": within20 / len(ratios) if ratios else None,
        "size_ratio_median": st.median(ratios) if ratios else None,
        "trades_archive": n_trades, "trades_ours": ours,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", required=True)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--root", default="root")
    ap.add_argument("--study", default="study")
    ap.add_argument("--tmp", default="tmp/archive")
    ap.add_argument("--keep", action="store_true", help="не удалять скачанное")
    a = ap.parse_args()
    os.makedirs(a.tmp, exist_ok=True)
    for s in a.symbols:
        r = check(s, a.day, a.root, a.study, a.tmp)
        print(json.dumps(r, ensure_ascii=False))
        if not a.keep:
            for f in os.listdir(a.tmp):
                if f.startswith(f"{s}-{a.day}"):
                    os.remove(os.path.join(a.tmp, f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
