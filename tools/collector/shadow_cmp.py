#!/usr/bin/env python3
"""TK-531: сравнение теневых процессов коллектора с боевым по строкам статистики шардов (stats-book*.jsonl, окно 10 с).
Делает: находит всплески лага книги на боевом (p50 > SPIKE_MS хотя бы на одном шарде) и сравнивает в те же 10-с корзины лаг p50/p99 и нагрузку на соединение (сообщ/с) боевого и теней;
`--table` — зависимость лага от нагрузки на соединение по корзинам объёма. Только чтение. Запуск: shadow_cmp.py --prod /data/bnws:2 --shadow A=/data/bnws-sh-a:1 [--shadow B=...] [--since-ms T] [--json out.json]
Каталог:N — N линий на шард (сообщ/с на соединение = msgs / N / 10).
"""
import argparse
import glob
import json
import os
import statistics
import sys

BUCKET_MS = 10_000
SPIKE_MS = 1000  # признак всплеска (координатор): p50 лага книги выше 1 с хотя бы на одном боевом шарде
ELEVATED_MS = 300  # «повышенный лаг» — меньше всплеска, тоже сравнивается
GAP_MS = 120_000  # корзины всплеска ближе этого — один эпизод
MARGIN_MS = 30_000  # сравнение берёт эпизод с запасом по краям
VOL_BINS = [0, 200, 300, 400, 600, 800, 1200, 1800, 2500, 1e9]  # сообщ/с на соединение


def load(spec, kind="book", since_ms=0):
    """spec 'имя=каталог:линий' или 'каталог:линий' -> (имя, {корзина: [(шард, p50, p99, сообщ/с на соединение, cpu)]})."""
    name, _, rest = spec.partition("=") if "=" in spec else ("prod", "", spec)
    path, _, lanes = rest.rpartition(":")
    lanes = int(lanes)
    out = {}
    for f in sorted(glob.glob(os.path.join(path, f"stats-{kind}*.jsonl"))):
        shard = os.path.basename(f)[6:-6]
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d["ts_ms"] < since_ms or d.get("lag_p50_ms") is None:
                continue
            out.setdefault(round(d["ts_ms"] / BUCKET_MS), []).append(
                (shard, d["lag_p50_ms"], d["lag_p99_ms"], d["msgs"] / lanes / (BUCKET_MS / 1000), d.get("cpu_frac")))
    return name, out


def episodes(prod, thr=SPIKE_MS):
    """Всплески боевого: [(начало_мс, конец_мс, корзин, max p50)] — корзины с p50 > thr хотя бы на одном шарде, слитые при разрыве < GAP_MS."""
    bad = sorted((b, max(r[1] for r in rows)) for b, rows in prod.items() if max(r[1] for r in rows) > thr)
    eps = []
    for b, mx in bad:
        t = b * BUCKET_MS
        if eps and t - eps[-1][1] < GAP_MS:
            eps[-1][1], eps[-1][2], eps[-1][3] = t, eps[-1][2] + 1, max(eps[-1][3], mx)
        else:
            eps.append([t, t, 1, mx])
    return eps


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def summarize(data, t0, t1):
    """Корзины [t0, t1] одного процесса: p50/p99 лага по шардам-корзинам и нагрузка на соединение."""
    rows = [r for b, rs in data.items() if t0 <= b * BUCKET_MS <= t1 for r in rs]
    if not rows:
        return None
    p50 = [r[1] for r in rows]
    p99 = [r[2] for r in rows]
    vol = [r[3] for r in rows]
    cpu = [r[4] for r in rows if r[4] is not None]
    worst = {}  # по корзинам — худший шард
    for b, rs in data.items():
        if t0 <= b * BUCKET_MS <= t1:
            worst[b] = max(r[1] for r in rs)
    return {
        "корзин": len(worst), "шардов": len({r[0] for r in rows}),
        "p50 лага: медиана / p90 / max, мс": [round(statistics.median(p50)), round(q(p50, 0.9)), round(max(p50))],
        "худший шард корзины p50: медиана / max, мс": [round(statistics.median(worst.values())), round(max(worst.values()))],
        "p99 лага: медиана / p90 / max, мс": [round(statistics.median(p99)), round(q(p99, 0.9)), round(max(p99))],
        "сообщ/с на соединение: медиана / max": [round(statistics.median(vol)), round(max(vol))],
        "CPU шарда: медиана / max": [round(statistics.median(cpu), 2), round(max(cpu), 2)] if cpu else None,
    }


def vol_table(datas):
    """Лаг по корзинам нагрузки на соединение: имя -> [(диапазон, n, p50 медиана, p50 p90, p99 медиана)]."""
    res = {}
    for name, data in datas.items():
        rows = [r for rs in data.values() for r in rs]
        t = []
        for lo, hi in zip(VOL_BINS, VOL_BINS[1:]):
            sel = [r for r in rows if lo <= r[3] < hi]
            if sel:
                t.append((f"{lo:g}–{hi:g}" if hi < 1e8 else f"≥{lo:g}", len(sel), round(statistics.median(r[1] for r in sel)),
                          round(q([r[1] for r in sel], 0.9)), round(statistics.median(r[2] for r in sel))))
        res[name] = t
    return res


def tails(path, lanes, kind, since_ms, until_ms):
    """Хвосты и события процесса за период: доли окон шард-10 с с p50 > ELEVATED_MS / SPIKE_MS и p99 > 1 с, события пути на соединение-час."""
    n = e300 = e1000 = p99_1s = 0
    shards = set()
    for f in sorted(glob.glob(os.path.join(path, f"stats-{kind}*.jsonl"))):
        shards.add(os.path.basename(f)[6:-6])
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not since_ms <= d["ts_ms"] < until_ms or d.get("lag_p50_ms") is None:
                continue
            n += 1
            e300 += d["lag_p50_ms"] > ELEVATED_MS
            e1000 += d["lag_p50_ms"] > SPIKE_MS
            p99_1s += d["lag_p99_ms"] > 1000
    ev = {}
    for f in sorted(glob.glob(os.path.join(path, f"events-{kind}*.jsonl"))):
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if since_ms <= d["ts_ms"] < until_ms and d["ev"] in ("timeout", "ping_degraded", "path_switch", "path_rejected", "path_rejected_rtt", "error", "rotated", "closed"):
                ev[d["ev"]] = ev.get(d["ev"], 0) + 1
    conn_h = len(shards) * lanes * (until_ms - since_ms) / 3.6e6
    return {"шардов": len(shards), "соединений": len(shards) * lanes, "окон шард-10 с": n,
            f"доля окон p50 > {ELEVATED_MS} мс, %": round(100 * e300 / max(n, 1), 3), f"доля окон p50 > {SPIKE_MS} мс, %": round(100 * e1000 / max(n, 1), 3),
            "доля окон p99 > 1 с, %": round(100 * p99_1s / max(n, 1), 2), "события": ev,
            "событий пути (ping_degraded) на соединение-час": round(ev.get("ping_degraded", 0) / conn_h, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", required=True, help="каталог:линий боевого")
    ap.add_argument("--shadow", action="append", default=[], help="имя=каталог:линий")
    ap.add_argument("--since-ms", type=int, default=0)
    ap.add_argument("--until-ms", type=int, default=1 << 62)
    ap.add_argument("--kind", default="book")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--json", help="сохранить результат")
    ap.add_argument("--tails", action="store_true", help="доли окон с лагом выше порогов и события пути на соединение-час за период")
    ap.add_argument("--brief", action="store_true", help="одна строка: всплески, повышенный лаг, покрытие и медианы за период")
    a = ap.parse_args()
    _, prod = load(a.prod, a.kind, a.since_ms)
    prod = {b: r for b, r in prod.items() if b * BUCKET_MS <= a.until_ms}
    shadows = dict(load(s, a.kind, a.since_ms) for s in a.shadow)
    if not prod:
        print("боевой: нет строк за период")
        return 1
    span = (min(prod) * BUCKET_MS, max(prod) * BUCKET_MS)
    res = {"период_мс": span, "всплески": [], "повышенный лаг": []}
    for key, thr in (("всплески", SPIKE_MS), ("повышенный лаг", ELEVATED_MS)):
        for t0, t1, n, mx in episodes(prod, thr):
            if key == "повышенный лаг" and any(e["от_мс"] <= t0 <= e["до_мс"] for e in res["всплески"]):
                continue
            ep = {"от_мс": t0, "до_мс": t1, "корзин": n, "max p50 боевого, мс": round(mx), "процессы": {}}
            for name, data in [("prod", prod)] + list(shadows.items()):
                ep["процессы"][name] = summarize(data, t0 - MARGIN_MS, t1 + MARGIN_MS)
            res[key].append(ep)
    if a.table:
        res["лаг по нагрузке"] = vol_table({"prod": prod, **shadows})
    if a.tails:
        res["хвосты"] = {}
        for spec in [a.prod] + [s.partition("=")[2] for s in a.shadow]:
            path, _, lanes = spec.rpartition(":")
            name = "prod" if spec == a.prod else next(s.partition("=")[0] for s in a.shadow if s.partition("=")[2] == spec)
            res["хвосты"][name] = tails(path, int(lanes), a.kind, a.since_ms or span[0], min(a.until_ms, span[1] + BUCKET_MS))
    for name, data in shadows.items():
        res.setdefault("покрытие", {})[name] = f"{len(data)} корзин из {len(prod)} боевого"
    txt = json.dumps(res, ensure_ascii=False, indent=1)
    if a.brief:
        full = {n: summarize(d, span[0], span[1]) for n, d in [("prod", prod)] + list(shadows.items())}
        print(f"всплесков {len(res['всплески'])}, повышенный лаг (>{ELEVATED_MS} мс) {len(res['повышенный лаг'])}; " + "; ".join(
            f"{n}: p50 мед {v['p50 лага: медиана / p90 / max, мс'][0]} p99 мед {v['p99 лага: медиана / p90 / max, мс'][0]} max p50 {v['p50 лага: медиана / p90 / max, мс'][2]} {v['сообщ/с на соединение: медиана / max'][0]} сообщ/с, корзин {v['корзин']}"
            for n, v in full.items() if v))
    else:
        print(txt)
    if a.json:
        with open(a.json, "w") as f:
            f.write(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
