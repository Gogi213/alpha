#!/usr/bin/env python3
"""G-022 BingX: lag events of a ladder, from the recorded files and events (the answer to «who was late: the exchange, or one line / path?»).

  lagevents.py <ladder dir> [--late-ms 2000] --out lag.json

Per window (`<tag>-nN-wK`), per shard (book/trade/mark connection pair = two lines: line 0 = session `…<start>`, line 1 = session `…+1_000_000_000`):
  late      — records of the shard (rx − E > late-ms, after the writer's de-duplication) by kind: count, symbols, first/last E (seconds from the window start), worst lag;
  events    — `timeout` (no pong for 10 s: tcp_info of the socket: retransmits, backoff, rto, unacked, last_data_recv), `ping_degraded`, `entrance_switch` (reason),
              `path_switch` / `path_upgrade` / `rotated` / `error`, each with the line and the address family of the session (from its `connected` event);
  ping      — `g` rows of the shard with rtt > 1 s (a pong that came back after a stall: the stall length);
  stats     — the 10 s stats rows of the shard with lag p99 > late-ms.
The files hold ONE copy of every record (the writer drops the later duplicate), so a late record is a state that the FASTER line did not have — not proof that both lines were late.
Which line delivered it comes from the events (stalled session) and from `capture-*.tsv` runs (`lanecmp.py`, `lanelate.py`).
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "collector"))
from accept_ws import read_lines  # noqa: E402
from wscore import codec  # noqa: E402


def sessions(root):
    """shard -> {session: {"line", "family", "ip", "rtt_ms"}} from `connected` events. Session numbers: line k = run id + k * 1_000_000, a replacement adds 1 (so line = (session - lowest) // 1_000_000)."""
    out = collections.defaultdict(dict)
    for p in glob.glob(os.path.join(root, "events-*.jsonl")):
        shard = os.path.basename(p)[len("events-"):-len(".jsonl")]
        evs = [json.loads(ln) for ln in open(p, encoding="utf-8")]
        base = min((e["session"] for e in evs if e["ev"] == "connected"), default=0)
        for e in evs:
            if e["ev"] == "connected":
                ip = e.get("ip", "")
                out[shard][e["session"]] = {"family": "v6" if ":" in ip else "v4", "ip": ip, "rtt_ms": e.get("rtt_ms"), "line": (e["session"] - base) // 1_000_000}
    return out


def window(wdir, late_ms):
    root = os.path.join(wdir, "data")
    t0 = int(open(os.path.join(wdir, "t0-ms.txt")).read())
    sess = sessions(root)
    res = {"t0_ms": t0, "shards": {}}
    late = collections.defaultdict(lambda: collections.defaultdict(lambda: {"n": 0, "syms": set(), "first": None, "last": None, "worst_ms": 0}))
    pings = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        shard = os.path.basename(p).rsplit("-", 1)[0]
        for r in codec.decode(read_lines(p)):
            rx = r["rx_ns"] // 1_000_000
            if r["t"] == "g":
                if r["rtt_us"] > 1_000_000:
                    pings[shard].append([round((rx - t0) / 1000, 1), round(r["rtt_us"] / 1000)])
            elif r["t"] in ("b", "t", "m"):
                lag = rx - r["E"]
                if lag > late_ms:
                    d = late[shard][r["t"]]
                    d["n"] += 1
                    d["syms"].add(r["s"])
                    e = (r["E"] - t0) / 1000
                    d["first"] = e if d["first"] is None else min(d["first"], e)
                    d["last"] = e if d["last"] is None else max(d["last"], e)
                    d["worst_ms"] = max(d["worst_ms"], lag)
    shards = set(late) | set(pings) | set(sess)
    for shard in sorted(shards):
        ev = []
        evp = os.path.join(root, f"events-{shard}.jsonl")
        if os.path.exists(evp):
            for ln in open(evp, encoding="utf-8"):
                e = json.loads(ln)
                if e["ev"] in ("timeout", "ping_degraded", "entrance_switch", "path_switch", "path_upgrade", "rotated", "error", "path_rejected", "path_rejected_rtt", "notice", "bad_frame"):
                    s = e.get("session", e.get("old_session"))
                    ev.append({"t": round((e["ts_ms"] - t0) / 1000, 1), **{k: v for k, v in e.items() if k not in ("ts_ms", "shard")}, "session_info": sess[shard].get(s)})
        st = []
        sp = os.path.join(root, f"stats-{shard}.jsonl")
        if os.path.exists(sp):
            for ln in open(sp, encoding="utf-8"):
                r = json.loads(ln)
                if (r.get("lag_p99_ms") or 0) > late_ms:
                    st.append([round((r["ts_ms"] - t0) / 1000), r.get("lag_p50_ms"), r.get("lag_p99_ms"), r.get("max_gap_ms")])
        if late.get(shard) or pings.get(shard) or ev or st:
            res["shards"][shard] = {"late": {k: {"n": v["n"], "symbols": len(v["syms"]), "first_E_s": round(v["first"], 1), "last_E_s": round(v["last"], 1), "worst_ms": v["worst_ms"]} for k, v in late.get(shard, {}).items()},
                                    "events": ev, "ping_over_1s": pings.get(shard, [])[:20], "stats_lag_p99_over": st[:20]}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ladder")
    ap.add_argument("--late-ms", type=int, default=2000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = {}
    for w in sorted(glob.glob(os.path.join(a.ladder, "*-n*-w[0-9]")), key=lambda p: (int(re.search(r"-n(\d+|full)-", p).group(1).replace("full", "9999")), p)):
        if os.path.exists(os.path.join(w, "t0-ms.txt")) and os.path.isdir(os.path.join(w, "data")):
            out[os.path.basename(w)] = window(w, a.late_ms)
            print(os.path.basename(w), "done", flush=True)
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
