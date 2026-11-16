#!/usr/bin/env python3
"""Разбор происшествия на боевой бирже за окно времени (collector-perf, 14.11): какие события, на каких шардах и линиях, класс (мы / биржа / сеть), состояние нашей стороны в момент закрытия (`diag`).
usage: incident.py <биржа> <с epoch> <по epoch> [--detail]; читает `events-*.jsonl` и `series-minutes.jsonl` боевого каталога, ничего не пишет."""
import collections
import glob
import json
import os
import sys
import time

EX, T0, T1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
DETAIL = "--detail" in sys.argv
ROOT = os.environ.get("INCIDENT_ROOT") or {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data", "bingx": "/data/collect/bingx/data"}[EX]
hm = lambda t: time.strftime("%H:%M:%S", time.gmtime(t))
ev = []
for f in glob.glob(ROOT + "/events-*.jsonl"):
    shard = f.split("events-")[1][:-6]
    for ln in open(f, errors="replace"):
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        t = j.get("ts_ms", 0) / 1000
        if T0 <= t <= T1:
            j["_sh"], j["_t"] = shard, t
            ev.append(j)
ev.sort(key=lambda j: j["_t"])
print(f"{EX} {hm(T0)}–{hm(T1)} UTC: событий {len(ev)}")
by = collections.Counter(j["ev"] for j in ev)
print("по типам:", dict(by.most_common()))
print("шарды (события, кроме connect_wait/path_ok/subscribed):")
sh = collections.defaultdict(collections.Counter)
for j in ev:
    if j["ev"] not in ("connect_wait", "path_ok", "subscribed"):
        sh[j["_sh"]][j["ev"]] += 1
for s, c in sorted(sh.items()):
    print(f"  {s}: {dict(c)}")
per_min = collections.Counter()
for j in ev:
    if j["ev"] in ("ws_end", "ws_close", "error", "timeout", "gap", "connected"):
        per_min[(time.strftime("%H:%M", time.gmtime(j["_t"])), j["ev"])] += 1
mins = sorted({m for m, _ in per_min})
print("по минутам (connected/ws_end+ws_close/error/timeout/gap):")
for m in mins:
    print(f"  {m}: " + " ".join(f"{e}={per_min[(m, e)]}" for e in ("connected", "ws_end", "ws_close", "error", "timeout", "gap") if per_min[(m, e)]))
print("концы линий и таймауты (diag):")
for j in ev:
    if j["ev"] in ("ws_end", "ws_close", "timeout", "ping_degraded", "path_switch", "replace_forced", "rotated", "gap", "gap_end", "entrance_switch"):
        d, t = j.get("diag") or {}, j.get("tcp") or {}
        extra = ""
        if d:
            extra = (f" возраст {d.get('age_s', 0) / 60:.0f} мин, с кадра {d.get('since_frame_ms')} мс, с пинга бирж. {d.get('since_srv_ping_ms')} мс, с нашего pong {d.get('since_pong_sent_ms')} мс (макс. задержка {d.get('pong_lat_max_us')} мкс), "
                     f"наш пинг {d.get('since_our_ping_ms')} / pong бирж. {d.get('since_our_pong_ms')} мс; recvq {t.get('recvq')}, unacked {t.get('unacked')}, ретр. {t.get('total_retrans')}, rtt {t.get('rtt_ms')}")
        key = {k: j.get(k) for k in ("how", "kind", "data_alive", "code", "reason", "gap_ms", "gap_total_ms", "old_session", "overlap_ms") if j.get(k) is not None}
        if DETAIL or j["ev"] != "gap_end":
            print(f"  {hm(j['_t'])} {j['_sh']:8s} {j['ev']:14s} сессия {j.get('session', '')} {key}{extra}")
print("series-minutes (потеряно мс, поздних [book,trade,mark,raw]) за окно:")
for ln in open(ROOT + "/series-minutes.jsonl"):
    j = json.loads(ln)
    if "t" in j and T0 - 60 <= j["t"] <= T1:
        # поздняя строка потери (`lost_ids_late` / `lost_ms_late`, 16.11) несёт только `t` и эти поля — без `late` и `lost_ms`
        late = j.get("late") or [0, 0, 0, 0]
        if j.get("lost_ms") or j.get("lost_ids_late") or j.get("lost_ms_late") or sum(late) or j.get("to", [0, 0]) != [0, 0]:
            print(f"  {hm(j['t'])} lost_ms {j.get('lost_ms')} late {late} таймауты {j.get('to')} gaps {j.get('gaps')} lost_ids_late {j.get('lost_ids_late')} lost_ms_late {j.get('lost_ms_late')}")
