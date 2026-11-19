#!/usr/bin/env python3
"""G-022 pool-fix: итог `poolsmoke.sh` — что сделал живой пересмотр пула копии на бирже.

  poolsmoke.py <каталог метки> <биржа>      # JSON в stdout

По файлам копии (`<метка>/data`) и пулу запуска (`pool-<день>-<запуск>.json`):
  added   — символы, добавленные пересмотром: момент изменения (ts пула), первая запись символа в файлах (rx), задержка от изменения до первой записи, число записей;
  removed — символы, снятые по гистерезису: момент снятия, последняя запись символа (rx), записей после снятия + 5 с (должно быть 0);
  acks    — ответы биржи на подписку и отписку (строки `r` файлов, кадр как пришёл): число и первые образцы;
  events  — `pool_delta` по шардам (сколько потоков добавлено / снято на соединении), новые шарды (`started` у шардов, которых не было в начальном плане), ошибки подписки.
Только чтение; `zstd -dc` на каждый файл.
"""
import collections
import glob
import json
import os
import subprocess
import sys

TYPES = {"b": 4, "t": 4}   # индекс столбца символа: [тип, dns, lag, ET, sym…]; `m` не берём: у Binance один поток mark на все символы, у остальных он идёт и без пересмотра пула


def rows(path):
    p = subprocess.run(["zstd", "-dc", path], capture_output=True)
    for ln in p.stdout.decode("utf-8", "replace").split("\n"):
        if ln:
            yield ln.split("\t")


def main():
    d, ex = sys.argv[1], sys.argv[2]
    root = os.path.join(d, "data")
    pf = sorted(glob.glob(os.path.join(root, "pool-*-*.json")), key=os.path.getmtime)[-1]
    pool = json.load(open(pf))
    changes = pool.get("changes", [])
    first, last, cnt = {}, {}, collections.Counter()
    removed_syms = {c["symbol"] for c in changes if c["op"] == "remove"}
    times = collections.defaultdict(list)   # время записей снятых символов: после снятия до возможного повторного входа (оборот вернулся выше порога) записей быть не должно
    acks = collections.Counter()
    ack_samples = {}
    for f in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        t = 0
        for r in rows(f):
            try:
                t += int(r[1])
            except (ValueError, IndexError):
                continue
            k = r[0]
            ms = t // 1_000_000
            if k in TYPES and len(r) > TYPES[k]:
                s = r[TYPES[k]]
                first[s] = min(first.get(s, ms), ms)
                last[s] = max(last.get(s, ms), ms)
                cnt[s] += 1
                if s in removed_syms:
                    times[s].append(ms)
            elif k == "r" and len(r) > 2:
                fr = r[2]
                low = fr.lower()
                kind = None
                if "unsubscribe" in low or "unsub" in low:
                    kind = "unsubscribe"
                elif '"result":null' in low or "subscribe" in low or '"success"' in low or '"code":0' in low or '"code": 0' in low:
                    kind = "subscribe_or_other"
                if kind:
                    acks[kind] += 1
                    ack_samples.setdefault(kind, [])
                    if len(ack_samples[kind]) < 3:
                        ack_samples[kind].append(fr[:240])
    added, removed = [], []
    for c in changes:
        s, ts = c["symbol"], c["ts_ms"]
        if c["op"] == "add":
            f = first.get(s)
            added.append({"symbol": s, "changed_ms": ts, "first_record_ms": f, "latency_s": None if f is None else round((f - ts) / 1000, 1), "records": cnt.get(s, 0)})
        else:
            l = last.get(s)
            readd = min((x["ts_ms"] for x in changes if x["symbol"] == s and x["op"] == "add" and x["ts_ms"] > ts), default=None)
            after = sum(1 for m in times.get(s, ()) if m > ts + 5000 and (readd is None or m < readd))
            removed.append({"symbol": s, "changed_ms": ts, "last_record_ms": l, "last_minus_change_s": None if l is None else round((l - ts) / 1000, 1),
                            "readded_ms": readd, "records_after_5s": after})
    ev = collections.Counter()
    deltas = []
    started = collections.Counter()
    for p in glob.glob(os.path.join(root, "events-*.jsonl")):
        shard = os.path.basename(p)[len("events-"):-len(".jsonl")]
        for ln in open(p, encoding="utf-8"):
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            ev[e["ev"]] += 1
            if e["ev"] == "pool_delta":
                deltas.append({"shard": shard, "ts_ms": e["ts_ms"], "added": e.get("added"), "removed": e.get("removed")})
            if e["ev"] == "started":
                started[shard] += 1
    plan_shards = set(s["shard"] for s in deltas)
    out = {"exchange": ex, "pool_file": os.path.basename(pf), "start_pool": len(pool.get("pool", [])), "pool_now": len(pool.get("pool_now", [])), "changes": len(changes),
           "added": added, "removed": removed,
           "added_with_data": sum(1 for a in added if a["first_record_ms"] is not None), "added_total": len(added),
           "added_latency_s_max": max((a["latency_s"] for a in added if a["latency_s"] is not None), default=None),
           "removed_records_after_5s": sum(r["records_after_5s"] or 0 for r in removed), "removed_total": len(removed),
           "acks": dict(acks), "ack_samples": ack_samples,
           "events": {k: v for k, v in ev.items() if k in ("pool_delta", "connected", "subscribed", "error", "path_rejected", "path_rejected_rtt", "replace_capped", "path_reject_capped", "closed", "switch_timeout")},
           "pool_delta": sorted(deltas, key=lambda x: x["ts_ms"]), "shards_started": dict(started), "shards_with_delta": sorted(plan_shards)}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
