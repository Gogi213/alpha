#!/usr/bin/env python3
"""Прогон правила режима «стоянка» (src/collector/storm.rs) по записанным событиям `events-*.jsonl` коллекторов.

Модель на событиях (те же данные, что видел бы процесс; сам режим в записанном запуске не работал — обратной связи нет):
  у линии (seq // 1 000 000) одна текущая сессия: первая `connected`, дальше — `new_session` события `path_switch|rotated|path_upgrade`;
  сессия открыта с `connected` до `ws_end` / `closed` (тот же seq переподключается новым `connected`);
  сессия испорчена с `ping_degraded` или `replace_*` / `storm_hold` с degraded=true до закрытия сессии;
  замена, ещё не ставшая текущей, поток не несёт (идёт проверка пути);
  пауза данных — интервал `gap_end` (gap_start_ms .. +gap_total_ms);
  первые 2 мин после запуска процесса не оцениваются; пока процесс стоит (`stopped` .. следующий `started`), оценки нет.
Правила шарда «испорчен»:
  old — пауза, или нет открытой текущей сессии, или испорчена хоть одна открытая текущая (до правки Б1);
  new — пауза, или нет текущей сессии, открытой и неиспорченной (шард несёт поток, пока жива хоть одна здоровая линия).
Режим: включён, когда испорченных больше половины шардов и шардов ≥ 4; выключается через 60 с после последнего момента «больше половины».

Использование: stormsim.py <с, сек эпохи> [<до, сек эпохи>] [--list]
"""
import glob
import json
import os
import sys
import time

ROOTS = [
    ("binance", "/data/collect/binance/data"),
    ("bybit", "/data/turbo-probe/soak-bybit/data"),
    ("okx", "/data/collect/okx/data"),
    ("bingx", "/data/collect/bingx/data"),
    ("gate", "/data/collect/gate/data"),
]
WARMUP_MS = 120_000
CLEAR_MS = 60_000
MIN_SHARDS = 4
CLOSE = ("ws_end", "closed")
SWAP = ("path_switch", "rotated", "path_upgrade")


def load(root, since, until):
    """Список (t_мс, порядок, шард, тип, данные) по всем шардам биржи."""
    out = []
    n = 0
    for f in sorted(glob.glob(root + "/events-*.jsonl")):
        sh = os.path.basename(f)[7:-6]
        for ln in open(f, errors="replace"):
            if '"ts_ms"' not in ln:
                continue
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            t = v.get("ts_ms", 0)
            if t < since or t >= until:
                continue
            e = v.get("ev")
            if e == "gap_end":
                a = v["gap_start_ms"]
                out.append((a, n, sh, "gap_on", None))
                out.append((a + v["gap_total_ms"], n + 1, sh, "gap_off", None))
                n += 2
            elif e == "started" or e == "stopped":
                out.append((t, n, sh, e, None))
                n += 1
            elif e == "connected":
                out.append((t, n, sh, "open", v.get("session")))
                n += 1
            elif e in CLOSE:
                out.append((t, n, sh, "close", v.get("session")))
                n += 1
            elif e in SWAP:
                out.append((t, n, sh, "swap", (v.get("old_session"), v.get("new_session"))))
                n += 1
            elif e == "ping_degraded" or (e in ("replace_deferred", "replace_forced", "replace_capped", "storm_hold") and v.get("degraded")):
                out.append((t, n, sh, "degrade", v.get("session")))
                n += 1
    out.sort()
    return out


class Shard:
    __slots__ = ("up", "gap", "open", "deg", "cur")

    def __init__(self):
        self.up = False
        self.gap = False
        self.reset()

    def reset(self):
        self.open = {}  # session -> соединение открыто
        self.deg = {}  # session -> испорчена
        self.cur = {}  # линия -> текущая сессия

    def lines(self):
        """[(открыта, испорчена)] по текущим сессиям линий."""
        return [(self.open.get(s, False), self.deg.get(s, False)) for s in self.cur.values()]

    def impaired(self, rule):
        if self.gap:
            return True
        ls = self.lines()
        if rule == "old":
            return not any(o for o, _ in ls) or any(o and d for o, d in ls)
        return not any(o and not d for o, d in ls)


def run(ev, rule):
    """Эпизоды режима: [(начало, конец, макс. испорченных, шардов)]."""
    shards = {}
    on = False
    over_prev = False
    last_over = 0
    start = 0
    peak = 0
    eps = []
    warm_until = 0
    active_prev = 0
    for t, _, sh, kind, arg in ev:
        # выключение по таймеру 60 с, пока шард не менял состояние
        if on and not over_prev and t - last_over >= CLEAR_MS:
            eps.append((start, last_over + CLEAR_MS, peak, total))
            on = False
        s = shards.setdefault(sh, Shard())
        if kind == "started":
            s.up, s.gap = True, False
            s.reset()
        elif kind == "stopped":
            s.up, s.gap = False, False
            s.reset()
        elif kind == "gap_on":
            s.gap = True
        elif kind == "gap_off":
            s.gap = False
        elif kind == "open":
            s.open[arg], s.deg[arg] = True, False
            s.cur.setdefault(arg // 1_000_000, arg)
        elif kind == "close":
            s.open[arg], s.deg[arg] = False, False
        elif kind == "degrade":
            if s.open.get(arg):
                s.deg[arg] = True
        elif kind == "swap":
            old, new = arg
            if new is not None:
                s.cur[new // 1_000_000] = new
            if old is not None:
                s.open[old], s.deg[old] = False, False
        active = sum(1 for x in shards.values() if x.up)
        if active_prev == 0 and active > 0:
            warm_until = t + WARMUP_MS
        active_prev = active
        if active == 0:
            if on:
                eps.append((start, t, peak, total))
                on = False
            over_prev = False
            continue
        if t < warm_until:
            continue
        total = active
        imp = sum(1 for x in shards.values() if x.up and x.impaired(rule))
        over = total >= MIN_SHARDS and imp * 2 > total
        if over:
            if not on:
                on, start, peak = True, t, 0
            peak = max(peak, imp)
        elif over_prev:
            last_over = t
        over_prev = over
        if over:
            last_over = t
    if on:
        eps.append((start, last_over + CLEAR_MS, peak, total))
    return eps


def fmt(t):
    return time.strftime("%m-%d %H:%M:%S", time.gmtime(t / 1000))


def main():
    since = int(sys.argv[1]) * 1000
    until = int(sys.argv[2]) * 1000 if len(sys.argv) > 2 and sys.argv[2].isdigit() else 10**15
    show = "--list" in sys.argv
    for ex, root in ROOTS:
        ev = load(root, since, until)
        if not ev:
            print(ex, "no events")
            continue
        for rule in ("old", "new"):
            eps = run(ev, rule)
            dur = sum(b - a for a, b, _, _ in eps) / 1000
            longest = max((b - a for a, b, _, _ in eps), default=0) / 1000
            print(f"{ex:8s} rule={rule}  episodes={len(eps):3d}  total_s={dur:9.0f}  longest_s={longest:8.0f}")
            if show:
                for a, b, p, tot in eps:
                    print(f"           {fmt(a)}  {(b - a) / 1000:8.0f} s  impaired_peak={p}/{tot}")


if __name__ == "__main__":
    main()
