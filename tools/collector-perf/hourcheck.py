#!/usr/bin/env python3
"""G-022 collector-perf: проверка «провалы на круглом часе»: что на srv происходит в HH:00 (netwatch, события коллекторов за 24 ч), раздельно потери данных (gap_end) и события без потерь.
usage: hourcheck.py <часов> [HH:MM:SS UTC начало окна для подробного списка событий] [HH:MM:SS конец]"""
import collections
import datetime
import glob
import json
import sys
import time

H = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
now = time.time()
t0 = now - H * 3600
ROOTS = {
    "binance": "/data/collect/binance/data",
    "bybit": "/data/turbo-probe/soak-bybit/data",
    "okx": "/data/collect/okx/data",
    "bingx": "/data/collect/bingx/data",
}


def utc(t):
    return datetime.datetime.fromtimestamp(t, datetime.UTC)


# 1. netwatch: потери пингов до шлюза/нейтрального узла и RTT бирж > 500 мс, по часам и по минуте часа
per_hour = collections.defaultdict(lambda: collections.Counter())
per_min = collections.Counter()
for f in sorted(glob.glob("/data/netwatch/20*.jsonl")):
    for ln in open(f, errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if "ts" not in r or r["ts"] < t0:
            continue
        rtt, n = r.get("rtt"), r["n"]
        bad = (rtt is None) if n in ("gateway", "neutral") else (rtt is None or rtt > (3000 if n == "binance" else 500))
        if n == "binance" and rtt is not None and rtt < 1500:
            bad = False
        if not bad:
            continue
        t = utc(r["ts"])
        per_hour[t.strftime("%m-%d %H")][("m00" if t.minute == 0 else "other")] += 1
        per_min[t.minute] += 1
print("== netwatch: аномалии (потеря пинга шлюза/нейтрального; RTT бирж > 0,5 с, Binance > 1,5 с) за", H, "ч; по часам: минута :00 / остальные 59 минут")
for h in sorted(per_hour):
    print(f"  {h}:00  :00 → {per_hour[h]['m00']:3d}   остальные → {per_hour[h]['other']:3d}")
print("  по минуте часа (всего):", " ".join(f"{m}:{per_min[m]}" for m in sorted(per_min, key=lambda m: -per_min[m])[:8]))

# 2. события коллекторов: gap_end (потеря данных) и прочие по минуте часа
print("\n== события коллекторов за", H, "ч")
for ex, root in ROOTS.items():
    gaps, other = [], collections.Counter()
    othermin = collections.defaultdict(collections.Counter)
    for f in glob.glob(f"{root}/events-*.jsonl"):
        sh = f.split("events-")[1][:-6]
        for ln in open(f, errors="replace"):
            try:
                j = json.loads(ln)
            except Exception:
                continue
            ts = j.get("ts_ms")
            if ts is None or ts / 1000 < t0:
                continue
            ev = j.get("ev") or j.get("type") or "?"
            if ev == "gap_end":
                st = j.get("gap_start_ms") or j.get("data", {}).get("gap_start_ms")
                d = j.get("gap_total_ms") or j.get("data", {}).get("gap_total_ms")
                if st is not None and d is not None:
                    gaps.append((st, d, sh))
            elif ev in ("closed", "timeout", "path_rejected_rtt", "entrance_switch", "reconnect", "disconnected", "gap_start", "watchdog", "ping_timeout"):
                m = utc(ts / 1000).minute
                other[ev] += 1
                othermin[ev][m] += 1
    by_min = collections.Counter(utc(st / 1000).minute for st, d, sh in gaps)
    tot = sum(d for st, d, sh in gaps) / 1000
    print(f"{ex}: gap_end {len(gaps)} (сумма {tot:.0f} с); по минуте часа: {dict(sorted(by_min.items()))}")
    for st, d, sh in sorted(gaps)[-12:]:
        print(f"     потеря {utc(st / 1000).strftime('%m-%d %H:%M:%S')} {d / 1000:.1f} с  {sh}")
    print("     прочие события:", dict(other), "| из них в минуте :00:", {ev: othermin[ev].get(0, 0) for ev in other})

# 3. подробный список событий в заданном окне (все четыре коллектора)
if len(sys.argv) > 3:
    a = datetime.datetime.combine(utc(now).date(), datetime.time.fromisoformat(sys.argv[2]), tzinfo=datetime.UTC).timestamp()
    b = datetime.datetime.combine(utc(now).date(), datetime.time.fromisoformat(sys.argv[3]), tzinfo=datetime.UTC).timestamp()
    print(f"\n== события {sys.argv[2]}–{sys.argv[3]} UTC (сегодня)")
    for ex, root in ROOTS.items():
        rows = []
        for f in glob.glob(f"{root}/events-*.jsonl"):
            for ln in open(f, errors="replace"):
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                ts = j.get("ts_ms")
                if ts is not None and a <= ts / 1000 <= b and j.get("ev") not in ("path_ok", "subscribed", "connected"):
                    rows.append((ts, json.dumps(j, ensure_ascii=False)[:230]))
        print(f"-- {ex}: {len(rows)} событий")
        for ts, s in sorted(rows)[:25]:
            print("   ", utc(ts / 1000).strftime("%H:%M:%S"), s)
