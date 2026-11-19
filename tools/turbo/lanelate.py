#!/usr/bin/env python3
"""G-022 BingX: who is late — the exchange (both lines at the same state) or one line / path? From a full frame capture (ALPHA_COLLECT_CAPTURE=1: `capture-<shard>.tsv` = session, rx ns, frame — every frame of every session BEFORE de-duplication).

  lanelate.py <dir with capture-*.tsv> --out lanelate.json [--late-ms 2000] [--warm-s 25]

Identity of a record = what the collector de-duplicates by: book (symbol, T), trade (symbol, id `t`), mark (symbol, E). For every identity: which sessions delivered it, and the lag (rx - E; trade: rx - T) of each delivery.
A delivery is "steady-state" when its session has been receiving for more than --warm-s seconds (the first frame of each topic after a subscribe carries a stale state: cold start, not an event).
A record whose FIRST delivery is late (lag > --late-ms) is classified by what its session was doing at that moment:
  session_wide  — the delivering session's median lag within +-2 s of the delivery is > 1 s (the whole connection is delayed: a path / TCP stall);
  both_late     — an isolated old record that BOTH sessions delivered late (the state itself is old when the exchange sends it: source side);
  one_line_only — an isolated old record delivered by one session only, the other session healthy at that time (the other line does not have that state: either the exchange sent it to one connection only,
                  or a short loss on one line — the two are told apart by `session_wide` of the neighbouring seconds).
Also: per session the seconds with a median lag > 1 s (stall episodes) and the worst lag.
"""
import argparse
import collections
import glob
import json
import os
import re
import statistics

BOOK = re.compile(rb'"dataType":"([^@"]+)@bookTicker","data":\{"e":"bookTicker","u":\d+,"E":(\d+),"T":(\d+),')
TRADE = re.compile(rb'\{"t":"(\d+)","q":"[^"]*","p":"[^"]*","T":(\d+),"m":(?:true|false),"s":"([^"]+)"\}')
MARK = re.compile(rb'"dataType":"([^@"]+)@markPrice","data":\{"e":"markPriceUpdate","E":(\d+),')


def frames(path):
    with open(path, "rb") as f:
        for ln in f:
            a = ln.split(b"\t", 2)
            if len(a) == 3:
                yield int(a[0]), int(a[1]) // 1_000_000, a[2]


def shard(path, args):
    # identity -> {session: rx_ms}; E per identity; per session per second: lags
    ids = {}
    first_rx = {}
    sec_lags = collections.defaultdict(lambda: collections.defaultdict(list))
    kind = os.path.basename(path)[len("capture-"):].split(".")[0]
    for sess, rx, fr in frames(path):
        first_rx.setdefault(sess, rx)
        out = []
        m = BOOK.search(fr)
        if m:
            out.append((("b", m.group(1).decode(), int(m.group(3))), int(m.group(2))))
        elif b"@markPrice" in fr[:80]:
            m = MARK.search(fr)
            if m:
                out.append((("m", m.group(1).decode(), int(m.group(2))), int(m.group(2))))
        elif b"@trade" in fr[:80]:
            for m in TRADE.finditer(fr):
                out.append((("t", m.group(3).decode(), int(m.group(1))), int(m.group(2))))
        for key, e in out:
            ids.setdefault(key, {}).setdefault(sess, (rx, e))
            sec_lags[sess][rx // 1000].append(rx - e)
    med = {s: {sec: statistics.median(v) for sec, v in d.items()} for s, d in sec_lags.items()}
    res = collections.Counter()
    examples = collections.defaultdict(list)
    for key, dl in ids.items():
        (sess0, (rx0, e0)) = min(dl.items(), key=lambda kv: kv[1][0])
        lag0 = rx0 - e0
        res["records"] += 1
        res["both_sessions"] += len(dl) > 1
        if lag0 <= args.late_ms or rx0 - first_rx[sess0] < args.warm_s * 1000:
            continue
        res["late_steady"] += 1
        near = [med[sess0].get(rx0 // 1000 + d) for d in (-2, -1, 0, 1, 2)]
        near = [x for x in near if x is not None]
        sess_med = statistics.median(near) if near else 0
        if sess_med > 1000:
            cls = "session_wide"
        elif len(dl) > 1 and all(r - e > args.late_ms for r, e in dl.values()):
            cls = "both_late"
        else:
            cls = "one_line_only" if len(dl) == 1 else "other_line_later"
        res[cls] += 1
        if len(examples[cls]) < 5:
            examples[cls].append({"id": list(key), "session": sess0, "lag_ms": lag0, "session_median_lag_ms": round(sess_med), "deliveries": {str(s): {"lag_ms": r - e, "rx_ms": r} for s, (r, e) in dl.items()}})
    stall = {str(s): sorted((sec, round(v)) for sec, v in d.items() if v > 1000) for s, d in med.items()}
    stall = {s: v[:30] for s, v in stall.items() if v}
    worst = {str(s): max(max(v) for v in d.values()) for s, d in sec_lags.items()}
    return kind, dict(res), dict(examples), stall, worst, {str(s): r for s, r in first_rx.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--late-ms", type=int, default=2000)
    ap.add_argument("--warm-s", type=float, default=25.0)
    a = ap.parse_args()
    out, tot = {}, collections.Counter()
    for p in sorted(glob.glob(os.path.join(a.dir, "capture-*.tsv"))):
        kind, res, ex, stall, worst, first = shard(p, a)
        out[kind] = {"counts": res, "examples": ex, "stall_seconds": stall, "worst_lag_ms": worst, "session_first_rx_ms": first}
        tot.update(res)
        print(kind, res, flush=True)
    out["_total"] = dict(tot)
    json.dump(out, open(a.out, "w"), indent=1)
    print("TOTAL", dict(tot))


if __name__ == "__main__":
    main()
