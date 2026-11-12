#!/usr/bin/env python3
"""G-022 (возврат TK-532 v2, п. 2): сравнение входов пробы на ОДНОМ интервале и выбор пары линий — по файлам `late-*.jsonl`, `events-*.jsonl`, `stats-*.jsonl` (entryprobe.py, каталог пробы).
  entrywin.py DIR [--json OUT] [--bucket-s 10]
Интервал — общий для всех входов (от самого позднего старта до самого раннего конца): входы стартовали не одновременно, числа за разные интервалы несравнимы.
По входу: поздние записи (rx − E > 2 с вне первых секунд подписки), макс. лаг, ошибки соединения, ping-timeout, число эпизодов (поздние записи с разрывом > EPISODE_GAP_S) и корзин BUCKET_S, где вход опоздал.
По паре входов (линия 0 + линия 1): корзины, где опоздали ОБА (в бою записанное опоздало бы — снимается первая пришедшая копия), и корзины, где опоздал один (вторая линия маскирует).
"""
import argparse
import glob
import itertools
import json
import os

EPISODE_GAP_S = 30


def jl(path):
    try:
        with open(path) as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--json")
    ap.add_argument("--bucket-s", type=int, default=10)
    a = ap.parse_args()
    names = sorted(os.path.basename(p)[6:-6] for p in glob.glob(os.path.join(a.dir, "stats-*.jsonl")))
    stats = {n: jl(os.path.join(a.dir, f"stats-{n}.jsonl")) for n in names}
    lo = max(min(r["ts_ms"] for r in rows) for rows in stats.values() if rows)
    hi = min(max(r["ts_ms"] for r in rows) for rows in stats.values() if rows)
    bms = a.bucket_s * 1000
    res = {"window_ms": [lo, hi], "window_h": round((hi - lo) / 3600_000, 2), "entrances": {}, "pairs": {}}
    late_b = {}
    for n in names:
        late = [x for x in jl(os.path.join(a.dir, f"late-{n}.jsonl")) if lo <= x["rx_ms"] <= hi]
        ev = [e for e in jl(os.path.join(a.dir, f"events-{n}.jsonl")) if lo <= e["ts_ms"] <= hi]
        st = [r for r in stats[n] if lo <= r["ts_ms"] <= hi]
        t = sorted(x["rx_ms"] for x in late)
        episodes = sum(1 for i, x in enumerate(t) if i == 0 or x - t[i - 1] > EPISODE_GAP_S * 1000)
        late_b[n] = {x // bms for x in t}
        res["entrances"][n] = {
            "late_records": len(late), "lag_max_ms": max((r["lag_max_ms"] for r in st if r["lag_max_ms"] is not None), default=None),
            "episodes": episodes, "late_buckets": len(late_b[n]), "errors": sum(e["ev"] == "error" for e in ev), "ping_timeouts": sum(e["ev"] == "ping_timeout" for e in ev),
            "windows": len(st),
        }
    for x, y in itertools.combinations(names, 2):
        both, only_x, only_y = late_b[x] & late_b[y], late_b[x] - late_b[y], late_b[y] - late_b[x]
        res["pairs"][f"{x}+{y}"] = {"both_late_buckets": len(both), f"only_{x}": len(only_x), f"only_{y}": len(only_y)}
    print(f"интервал {res['window_h']} ч, корзина {a.bucket_s} с")
    print(f"{'вход':6} {'поздних':>8} {'макс,с':>7} {'эпиз.':>6} {'корз.':>6} {'ошиб.':>6} {'ping-to':>8}")
    for n, v in res["entrances"].items():
        mx = "—" if v["lag_max_ms"] is None else f"{v['lag_max_ms'] / 1000:.1f}"
        print(f"{n:6} {v['late_records']:8d} {mx:>7} {v['episodes']:6d} {v['late_buckets']:6d} {v['errors']:6d} {v['ping_timeouts']:8d}")
    print("пара (линия 0 + линия 1): корзин, где опоздали оба / только первый / только второй")
    for k, v in sorted(res["pairs"].items(), key=lambda kv: (kv[1]["both_late_buckets"], kv[0])):
        x, y = k.split("+")
        print(f"{k:12} {v['both_late_buckets']:5d} {v['only_' + x]:5d} {v['only_' + y]:5d}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
