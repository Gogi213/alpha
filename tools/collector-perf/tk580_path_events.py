#!/usr/bin/env python3
"""TK-580 B: чем были вызваны решения по пути BingX. usage: tk580_path_events.py <каталог с events-*.jsonl> [с какой метки UTC, по умолчанию начало процесса сборки 29d981c0: 2026-11-16T15:26:12]
Печатает: счётчики событий пути; p50/p99 всех path_ok и запас до порогов (350 / 1500 мс, path.rs); path_rejected; причины path_switch (есть ли ping_degraded на старой сессии)."""
import collections, datetime, glob, json, statistics, sys
D = sys.argv[1]
SINCE = (datetime.datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.datetime(2026, 11, 16, 15, 26, 12)).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000
rows = []
for f in glob.glob(f"{D}/events-*.jsonl"):
    for l in open(f, encoding="utf8", errors="replace"):
        try: d = json.loads(l)
        except ValueError: continue
        d["shard"] = d.get("shard") or f.split("events-")[1][:-6]; rows.append(d)
rows.sort(key=lambda r: r["ts_ms"])
T = lambda r: datetime.datetime.fromtimestamp(r["ts_ms"] / 1000, datetime.timezone.utc).strftime("%m-%d %H:%M:%S")
for lab, sel in (("весь журнал (13.11–16.11)", rows), ("с начала сборки 29d981c0", [r for r in rows if r["ts_ms"] >= SINCE])):
    C = collections.Counter(r["ev"] for r in sel)
    print(f"== {lab}: {T(sel[0])} … {T(sel[-1])}")
    print({k: v for k, v in sorted(C.items()) if k.startswith("path") or k in ("ping_degraded", "timeout", "replace_capped", "switch_timeout")})
    po = [r for r in sel if r["ev"] == "path_ok"]
    by = collections.defaultdict(list)
    for r in po: by[r["shard"].rstrip("0123456789")].append(r)
    for k, v in sorted(by.items()):
        p50 = sorted(r["p50_ms"] for r in v); p99 = sorted(r["p99_ms"] for r in v)
        print(f"  path_ok {k}: n {len(v)}, p50 медиана {statistics.median(p50):.0f} p90 {p50[int(len(p50)*.9)]:.0f} макс {p50[-1]:.0f} (порог 350, запас {350-p50[-1]:.0f}); p99 макс {p99[-1]:.0f} (порог 1500, запас {1500-p99[-1]:.0f})")
    for r in sel:
        if r["ev"] == "path_rejected": print("  path_rejected:", T(r), r["shard"], "p50", r["p50_ms"], "p99", r["p99_ms"], "n", r["n"])
    sw = [r for r in sel if r["ev"] == "path_switch"]
    pd = {(r["shard"], r["session"]) for r in rows if r["ev"] == "ping_degraded"}
    miss = [r for r in sw if (r["shard"], r["old_session"]) not in pd]
    print(f"  path_switch {len(sw)}: со ping_degraded на старой сессии {len(sw) - len(miss)}, без {len(miss)}", [(T(r), r["shard"]) for r in miss])
near = sorted((r for r in rows if r["ev"] == "path_ok" and r["p50_ms"] >= 300), key=lambda r: r["ts_ms"])
print("path_ok с p50 ≥ 300 мс (запас ≤ 50 мс):", [(T(r), r["shard"], r["p50_ms"]) for r in near])
