#!/usr/bin/env python3
"""G-022 турбо: таблица «Python (боевой) против Rust» по каталогу окна probe.sh: accept-*.json, winmon.json, health.
Запуск: summarize.py <каталог метки> > таблица.md   (читает accept-python.json, accept-rust.json, winmon.json, health-final.json)
"""
import json
import os
import sys


def load(d, name):
    try:
        return json.load(open(os.path.join(d, name)))
    except (OSError, ValueError):
        return None


def g(d, *path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)


def main():
    d = sys.argv[1]
    py, rs, wm = load(d, "accept-python.json"), load(d, "accept-rust.json"), load(d, "winmon.json")
    unit_py = next((u for u in (wm or {}).get("units", {}) if u.endswith("-prod.service")), None)
    unit_rs = next((u for u in (wm or {}).get("units", {}) if u.startswith("turbo-")), None)
    rows = []

    def add(name, p, r):
        rows.append((name, fmt(p), fmt(r)))

    add("окно, с", g(wm, "seconds"), g(wm, "seconds"))
    for t, nm in (("b", "книга"), ("t", "сделки"), ("m", "mark")):
        for k in ("p50", "p99", "max"):
            add(f"лаг записи {nm} ({k}), мс", g(py, "disk_stream", t, "lag_ms", k), g(rs, "disk_stream", t, "lag_ms", k))
        add(f"макс. пауза между записями {nm}, мс", g(py, "disk_stream", t, "max_pause_ms"), g(rs, "disk_stream", t, "max_pause_ms"))
        add(f"записей {nm}", g(py, "disk_stream", t, "n"), g(rs, "disk_stream", t, "n"))
    add("лаг p50 по 10-с окнам, макс, книга, мс", g(py, "disk_stream", "b", "lag_p50_10s_max"), g(rs, "disk_stream", "b", "lag_p50_10s_max"))
    for k in ("book_u_not_increasing", "trade_a_not_increasing", "trade_a_gaps", "trade_a_gap_ids", "mark_E_not_increasing"):
        add(f"последовательность: {k}", g(py, "sequence_checks", k), g(rs, "sequence_checks", k))
    ev_p, ev_r = (g(py, "events") or {}), (g(rs, "events") or {})
    for k in sorted(set(ev_p) | set(ev_r)):
        if k in ("connected", "subscribed"):
            continue
        add(f"события {k}", ev_p.get(k, 0), ev_r.get(k, 0))
    add("подключений (TCP connect) за окно", g(py, "dials"), g(rs, "dials"))
    add("макс. подключений в любом окне 5 мин", g(py, "dials_5min_max"), g(rs, "dials_5min_max"))
    add("пинг WS p50/p99, мс", f"{g(py, 'ping_ms', 'p50')}/{g(py, 'ping_ms', 'p99')}", f"{g(rs, 'ping_ms', 'p50')}/{g(rs, 'ping_ms', 'p99')}")
    for k in ("cpu_cores_mean", "cpu_cores_p95", "cpu_cores_max", "cpu_wait_some_frac_mean", "cpu_wait_some_frac_max", "recvq_max_bytes", "recvq_nonzero_pct", "recvq_mean_bytes", "conns_max"):
        add(f"юнит: {k}", g(wm, "units", unit_py, k), g(wm, "units", unit_rs, k))
    add("диск, байт за окно (accept)", g(py, "disk_bytes"), g(rs, "disk_bytes"))
    hf = load(d, "health-final.json") or load(d, "health.json")
    for k in ("late_1h",):
        for kind in ("book", "trade", "mark", "raw"):
            add(f"health fresh.{kind}.late_1h", g(py, "health", "fresh", "kinds", kind, k), g(hf, "fresh", "kinds", kind, k))
    add("health lag_out p50/p99, мс", f"{g(py, 'health', 'lag_out_ms', 'p50')}/{g(py, 'health', 'lag_out_ms', 'p99')}", f"{g(hf, 'lag_out_ms', 'p50')}/{g(hf, 'lag_out_ms', 'p99')}")
    add("health lag_in p50/p99, мс", f"{g(py, 'health', 'lag_in_ms', 'p50')}/{g(py, 'health', 'lag_in_ms', 'p99')}", f"{g(hf, 'lag_in_ms', 'p50')}/{g(hf, 'lag_in_ms', 'p99')}")
    bp = g(hf, "backpressure") or {}
    for k, v in (("очередь приёма: ёмкость / глубина макс", f"{g(bp, 'ring', 'cap')}/{g(bp, 'ring', 'depth_max')}"), ("очередь приёма: переполнений всего", g(bp, "ring", "full_events_total")),
                 ("ожидание кадра в очереди p50/p99, мс", f"{g(bp, 'queue_lag_ms', 'p50')}/{g(bp, 'queue_lag_ms', 'p99')}"), ("Recv-Q макс по замерам (поток), байт", g(bp, "recv_q", "max_bytes")),
                 ("занятость потока приёма макс, мкс", g(bp, "reader", "busy_max_us")), ("запись блока на диск макс, мс", g(bp, "sink", "write_max_ms"))):
        add("Rust health: " + k, None, v)
    pr = g(hf, "process") or {}
    add("Rust process: ядер / ожидание CPU / потоков / RSS МБ", None, f"{g(pr, 'cpu_cores')}/{g(pr, 'sched_wait_cores')}/{g(pr, 'threads')}/{g(pr, 'rss_mb')}")
    print("| метрика | Python (боевой) | Rust |\n|---|---|---|")
    for r in rows:
        print("| " + " | ".join(r) + " |")


if __name__ == "__main__":
    main()
