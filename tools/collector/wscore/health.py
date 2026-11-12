"""Здоровье коллектора: фрагменты шардов -> health.json (схема 1, docs/findings/collector-health-format-2026-11-11.md; страница — TK-529).
Делает: write_fragment (шард), aggregate/write_health (процесс-родитель), атомарная запись. Не знает биржу и соединения; шард, молчащий дольше 3 периодов, живым не считается.
"""
import json
import os
import time

from wscore import stats
from wscore.stats import percentile


def write_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def write_fragment(root, shard, frag):
    write_atomic(os.path.join(root, "frag", f"{shard}.json"), frag)


def aggregate(frags, now_ms, pool, exchange):
    """Фрагменты шардов -> health.json."""

    def pct(xs):
        return {"p50": percentile(xs, 0.5), "p99": percentile(xs, 0.99), "window_s": stats.WINDOW_S}

    pings = [v for f in frags for v in f["ping_ms"]]
    with_data = {s for f in frags for s in f["with_data"]} & {x.lower() for x in pool}
    hour, day = now_ms - 3600_000, now_ms - 24 * 3600_000
    tos = [t for f in frags for t in f["timeouts"]]
    gps = [t for f in frags for t in f.get("gaps", [])]
    rcs = [t for f in frags for t in f["reconnects"]]
    return {
        "schema": 1, "exchange": exchange, "ts_ms": now_ms,
        "started_ms": min((f["started_ms"] for f in frags), default=now_ms),
        "symbols": {"subscribed": len(pool), "with_data_1m": len(with_data)},
        "ws": {
            "connections": sum(f["connected"] for f in frags),
            "ping_ms": {"p50": percentile(pings, 0.5), "max": max(pings) if pings else None},
            "timeouts_1h": sum(t > hour for t in tos), "timeouts_24h": sum(t > day for t in tos),
            "gaps_1h": sum(t > hour for t in gps), "gaps_24h": sum(t > day for t in gps),
            "reconnects_1h": sum(t > hour for t in rcs), "reconnects_24h": sum(t > day for t in rcs),
        },
        "lag_in_ms": pct([v for f in frags for v in f["lag_in"]]),
        "lag_out_ms": pct([v for f in frags for v in f["lag_out"]]),
        "last_frame_ms": max((f["last_frame_ms"] for f in frags), default=0),
        "last_write_ms": max((f["last_write_ms"] for f in frags), default=0),
    }


def read_fragments(root, now_ms):
    fd = os.path.join(root, "frag")
    out = []
    for n in sorted(os.listdir(fd)) if os.path.isdir(fd) else []:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(fd, n)) as f:
                fr = json.load(f)
        except (OSError, ValueError):
            continue
        if now_ms - fr["ts_ms"] < 3 * stats.STATS_PERIOD_S * 1000:
            out.append(fr)
    return out


def write_health(root, health_path, pool, exchange):
    now_ms = int(time.time() * 1000)
    write_atomic(health_path, aggregate(read_fragments(root, now_ms), now_ms, pool, exchange))
