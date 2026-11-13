#!/usr/bin/env python3
"""G-022 turbo: completeness and fidelity of recorded trades against the exchange's own REST history (the independent oracle).

Binance: for chosen symbols fetch /fapi/v1/aggTrades for the window and compare with the recorded files (`a`, price, quantity, first/last id, side):
  missing = REST id absent from the files, extra = recorded id absent from REST, mismatch = same id, different field.
Bybit: /v5/market/recent-trade (last 1000 per symbol, UUID execId) against `capture-*.tsv` (ALPHA_COLLECT_CAPTURE=1): missing/extra by execId within the
time span both cover; mismatch on price/size/side. The recorded Bybit key `a` (seq*1000+i) carries no UUID, so identity comes from the capture.
OKX: /api/v5/market/history-trades (every individual public trade, paged back by tradeId, `source` included) against the recorded aggregates: a recorded `t` row covers the id range
f..l (`tradeId - count + 1 .. tradeId`); missing = a REST trade whose id lies in no recorded range, mismatch = a range whose price / side / summed size / ELP flag (`x` = E <=> REST source 1)
differs from the REST trades inside it, extra = a recorded range (not at the window edges) with no REST trade at all.
BingX: REST has no trade history (/quote/trades = the last 1000 trades of a symbol, no time/id parameters), so the oracle is a snapshot taken at the window end (`bxsnap.py`, every symbol of the pool):
  missing = a REST trade in the window absent from the files (id), mismatch = same id, different price / size / side / time, extra = a recorded trade with an id inside the REST id range that REST lacks.
  Second, exchange-intrinsic oracle over the WHOLE pool: BingX trade ids are contiguous per symbol (REST and WS share `fillId`; measured 12.11: 0 gaps in 33 385 trades), so a recorded id range with a missing id is a lost
  trade: `pool_ids.gaps_after_covered` (a gap counts when the later trade is at/after --covered-ms).
Usage (srv): restcheck.py binance <data root> <t0_ms> <t1_ms> --symbols 25 --out restcheck.json
             restcheck.py bybit <data root with capture-*.tsv> <t0_ms> <t1_ms> --symbols 25 --out restcheck.json
             restcheck.py okx <data root> <t0_ms> <t1_ms> --symbols 25 --spread --covered-ms <ms> --out restcheck.json
             restcheck.py bingx <data root> <t0_ms> <t1_ms> --snap <rest-snap.json.gz> --covered-ms <ms> --out restcheck.json
"""
import argparse
import bisect
import concurrent.futures
import glob
import gzip
import io
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from decimal import Decimal

import zstandard

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))
from wscore import codec  # noqa: E402


def get(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.parse.quote(url, safe=":/?&=%"), timeout=20) as r:  # symbols with non-ASCII names exist (Binance)
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def read_lines(path):
    with open(path, "rb") as f:
        prev = None
        for ln in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8", errors="replace", newline="\n"):
            if prev is not None:
                yield prev
            prev = ln
        if prev is not None and prev.endswith("\n"):
            yield prev


def file_trades(root, t0, t1):
    """symbol -> {a: (p, q, f, l, m, T)} from recorded files, trade time T in [t0, t1]."""
    out = defaultdict(dict)
    for p in run_files(root):
        for r in codec.decode(read_lines(p)):
            if r["t"] == "t" and t0 <= r["T"] <= t1:
                out[r["s"]][r["a"]] = (r["p"], r["q"], r["f"], r["l"], r["m"], r["T"])
    return out


MIN_RUN = 0  # --min-run: only files of runs started at/after this epoch second (a soak root holds hours of earlier runs)


def run_files(root):
    out = []
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        try:
            run = int(os.path.basename(p).rsplit("-", 1)[1].split(".")[0])
        except (IndexError, ValueError):
            run = 0
        if run >= MIN_RUN:
            out.append(p)
    return out


def pick(counts, n, spread):
    """Symbols to check: the n busiest, or (spread) n evenly spaced over the ranking by trade count, so every shard is represented
    (a shard that never connected would otherwise hide among symbols nobody checks)."""
    ranked = [s for s, c in sorted(counts.items(), key=lambda x: -x[1]) if c >= 5]
    if not spread or len(ranked) <= n:
        return ranked[:n]
    step = len(ranked) / n
    return [ranked[int(i * step)] for i in range(n)]


def file_trades_bybit(root, t0, t1):
    """symbol -> {execId: (price, size, side, T, block, rpi)} from recorded files: execId is the trailing column `i` of `t` rows (since 12.11 build)."""
    out = defaultdict(dict)
    for p in run_files(root):
        lines = [ln for ln in read_lines(p) if ln.strip()]
        for ln, r in zip(lines, codec.decode(iter(lines))):
            if r["t"] != "t" or not t0 <= r["T"] <= t1:
                continue
            f = ln.rstrip("\n").split("\t")
            if len(f) > 13 and f[13]:
                x = f[12]
                out[r["s"]][f[13]] = (r["p"], r["q"], "Sell" if r["m"] else "Buy", r["T"], "B" in x, "R" in x)
    return out


def binance(a, t0, t1):
    rec = file_trades(a.root, t0, t1)
    syms = pick({s: len(v) for s, v in rec.items()}, a.symbols, a.spread)
    res = {}
    for s in syms:
        rest, start = {}, t0
        url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={s}&startTime={t0}&endTime={min(t1, t0 + 3_599_000)}&limit=1000"
        while True:
            page = get(url)
            for x in page:
                rest[x["a"]] = (x["p"], x["q"], x["f"], x["l"], bool(x["m"]), x["T"])
            if len(page) < 1000 or page[-1]["T"] >= t1:
                break
            url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol={s}&fromId={page[-1]['a'] + 1}&limit=1000"
            time.sleep(0.3)
        rest = {k: v for k, v in rest.items() if t0 <= v[5] <= t1}
        mine = rec[s]
        missing = sorted(set(rest) - set(mine))
        extra = sorted(set(mine) - set(rest))
        mism = [k for k in set(rest) & set(mine) if rest[k][:5] != mine[k][:5]]
        late = [k for k in missing if rest[k][5] >= a.covered_ms]
        res[s] = {"rest": len(rest), "file": len(mine), "missing_in_file": len(missing), "extra_in_file": len(extra), "mismatch": len(mism),
                  "rest_after_covered": sum(1 for v in rest.values() if v[5] >= a.covered_ms), "missing_after_covered": len(late),
                  "missing_examples": missing[:5], "extra_examples": extra[:5], "mismatch_examples": [(k, rest[k], mine[k]) for k in mism[:3]]}
        time.sleep(0.4)
    return res


def bybit(a, t0, t1):
    cap = defaultdict(dict)  # symbol -> {execId: (price, size, side, time)}
    if not glob.glob(os.path.join(a.root, "capture-*.tsv")):
        cap = file_trades_bybit(a.root, t0, t1)  # no capture: execId comes from the recorded files themselves
    for p in sorted(glob.glob(os.path.join(a.root, "capture-*.tsv"))):
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t", 2)
                if len(parts) != 3 or not parts[2].startswith('{"topic":"publicTrade.'):
                    continue
                try:
                    d = json.loads(parts[2])
                except ValueError:
                    continue
                for x in d["data"]:
                    cap[x["s"]][x["i"]] = (x["p"], x["v"], x["S"], x["T"], bool(x.get("BT")), bool(x.get("RPI")))
    syms = pick({s: len(v) for s, v in cap.items()}, a.symbols, a.spread)
    res = {}
    for s in syms:
        r = get(f"https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={s}&limit=1000")["result"]["list"]
        rest = {x["execId"]: (x["price"], x["size"], x["side"], int(x["time"]), bool(x.get("isBlockTrade")), bool(x.get("isRPITrade", False))) for x in r}
        if not rest:
            continue
        lo = max(min(v[3] for v in rest.values()), t0)  # REST covers only the last 1000 trades
        hi = t1
        rs = {k: v for k, v in rest.items() if lo <= v[3] <= hi}
        cs = {k: v for k, v in cap[s].items() if lo <= v[3] <= hi}
        missing = sorted(set(rs) - set(cs))
        extra = sorted(set(cs) - set(rs))
        mism = [k for k in set(rs) & set(cs) if rs[k][:3] != cs[k][:3]]
        late = [k for k in missing if rs[k][3] >= a.covered_ms]
        res[s] = {"rest": len(rs), "capture": len(cs), "span_ms": [lo, hi], "missing_in_capture": len(missing), "extra_in_capture": len(extra), "mismatch": len(mism),
                  "rest_after_covered": sum(1 for v in rs.values() if v[3] >= a.covered_ms), "missing_after_covered": len(late),
                  "missing_examples": [(k, rs[k]) for k in missing[:3]], "extra_examples": [(k, cs[k]) for k in extra[:3]], "mismatch_examples": [(k, rs[k], cs[k]) for k in mism[:3]]}
        time.sleep(0.3)
    return res


OKX_UA = {"User-Agent": "alpha-collector/okx-check"}  # without it Cloudflare answers 403 error code 1010


_RATE = threading.Lock()
_NEXT = [0.0]


def _pace(gap=0.115):
    """Global request pacing (OKX: 20 requests / 2 s per IP): one request start per `gap` s over all threads."""
    with _RATE:
        now = time.monotonic()
        at = max(now, _NEXT[0])
        _NEXT[0] = at + gap
    if at > now:
        time.sleep(at - now)


def get_okx(url, retries=4):
    for i in range(retries):
        try:
            _pace()
            with urllib.request.urlopen(urllib.request.Request(url, headers=OKX_UA), timeout=20) as r:
                j = json.load(r)
            if j.get("code") != "0":
                raise RuntimeError(f"okx code {j.get('code')} {j.get('msg')}")
            return j["data"]
        except Exception:  # noqa: BLE001
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def file_trades_okx(root, t0, t1):
    """symbol -> [(f, l, price, size, taker sells, T, x)] of recorded `t` rows with trade time T in [t0, t1]; x = the trailing flag column (E = ELP)."""
    out = defaultdict(dict)
    for p in run_files(root):
        lines = [ln for ln in read_lines(p) if ln.strip()]
        for ln, r in zip(lines, codec.decode(iter(lines))):
            if r["t"] == "t" and t0 <= r["T"] <= t1:
                f = ln.rstrip("\n").split("\t")
                out[r["s"]][r["l"]] = (r["f"], r["l"], r["p"], r["q"], r["m"], r["T"], f[12] if len(f) > 12 else "")
    return {s: sorted(v.values()) for s, v in out.items()}


def okx_rest_trades(inst, t0, t1):
    """tradeId -> (price, size, side, ts, source) of every public trade of inst with ts in [t0, t1]: history-trades pages (100, newest first), the first by time just after t1, the rest walked back by tradeId."""
    rows, after = {}, None
    while True:
        # first page: jump to the window end by time (type 2, so the walk does not start at "now": a pass hours later would page through hours of trades);
        # then by tradeId (type 1, exact: ids are contiguous, no same-millisecond loss)
        page = get_okx(f"https://www.okx.com/api/v5/market/history-trades?instId={inst}&limit=100" + (f"&type=1&after={after}" if after else f"&type=2&after={t1 + 3000}"))
        if not page:
            break
        for x in page:
            rows[int(x["tradeId"])] = (x["px"], x["sz"], x["side"], int(x["ts"]), x["source"])
        oldest = min(page, key=lambda x: int(x["tradeId"]))
        if int(oldest["ts"]) < t0 or len(page) < 100:
            break
        after = oldest["tradeId"]
    return {k: v for k, v in rows.items() if t0 <= v[3] <= t1}


def okx(a, t0, t1):
    rec = file_trades_okx(a.root, t0, t1)
    syms = pick({s: len(v) for s, v in rec.items()}, a.symbols, a.spread)
    def one(s):
        rest = okx_rest_trades(s, t0, t1)
        ranges = rec[s]
        starts = [x[0] for x in ranges]
        inside, missing = defaultdict(list), []
        for tid, v in rest.items():
            i = bisect.bisect_right(starts, tid) - 1
            if i >= 0 and ranges[i][1] >= tid:
                inside[i].append((tid, v))
            else:
                missing.append(tid)
        mism = []
        for i, items in inside.items():
            f, l, p, q, m, _, x = ranges[i]
            ok = all(v[0] == p and (v[2] == "sell") == m and (v[4] == "1") == (x == "E") for _, v in items)
            if len(items) == l - f + 1:  # the REST page holds the whole range: summed size must match too
                ok = ok and Decimal(q) == sum(Decimal(v[1]) for _, v in items)
            if not ok:
                mism.append((f, l, p, q, m, x, sorted(items)[:3]))
        edge = 5000  # a range cut by the window edge has REST trades outside the checked span
        extra = [r for i, r in enumerate(ranges) if i not in inside and t0 + edge <= r[5] <= t1 - edge]
        late = [k for k in missing if rest[k][3] >= a.covered_ms]
        return {"rest": len(rest), "file": len(ranges), "missing_in_file": len(missing), "extra_in_file": len(extra), "mismatch": len(mism),
                  "rest_after_covered": sum(1 for v in rest.values() if v[3] >= a.covered_ms), "missing_after_covered": len(late),
                  "missing_examples": [(k, rest[k]) for k in sorted(missing)[:5]], "extra_examples": extra[:3], "mismatch_examples": mism[:3]}
    with concurrent.futures.ThreadPoolExecutor(4) as ex:  # REST latency ~0.3 s: four in flight keep the pacing at the limit
        res = dict(zip(syms, ex.map(one, syms)))
    return res


def file_trades_bingx(root, t0, t1):
    """symbol -> {id: (price, size, buyer is maker, T)} of recorded `t` rows with trade time T in [t0, t1]; the id is the column `a` (= f = l)."""
    out = defaultdict(dict)
    for p in run_files(root):
        for r in codec.decode(read_lines(p)):
            if r["t"] == "t" and t0 <= r["T"] <= t1:
                out[r["s"]][r["a"]] = (r["p"], r["q"], bool(r["m"]), r["T"])
    return out


def bingx(a, t0, t1):
    rec = file_trades_bingx(a.root, t0, t1)
    snap = json.load(gzip.open(a.snap, "rt"))
    res = {}
    for sym, s in snap["symbols"].items():
        rest = {x[0]: (x[1], x[2], x[3], x[4]) for x in s["trades"]}
        mine = rec.get(sym, {})
        # the REST list is the last 1000 trades: complete for the window only if it reaches back to t0 (or holds fewer than 1000 trades)
        truncated = s["n"] >= 1000 and s["oldest"] is not None and s["oldest"] > t0
        lo = (s["oldest"] if truncated else t0)
        missing = sorted(set(rest) - set(mine))
        # a recorded trade is "extra" only inside the id range REST shows in the window (REST ids are contiguous, so every id of the range must be there)
        rmin, rmax = min(rest, default=None), max(rest, default=None)
        extra = sorted(k for k in mine if rmin is not None and rmin <= k <= rmax and k not in rest)
        mism = [k for k in set(rest) & set(mine)
                if not (Decimal(rest[k][0]) == Decimal(mine[k][0]) and Decimal(rest[k][1]) == Decimal(mine[k][1]) and rest[k][2] == mine[k][2] and rest[k][3] == mine[k][3])]
        late = [k for k in missing if rest[k][3] >= a.covered_ms]
        res[sym] = {"rest": len(rest), "file": len(mine), "missing_in_file": len(missing), "extra_in_file": len(extra), "mismatch": len(mism),
                    "rest_after_covered": sum(1 for v in rest.values() if v[3] >= a.covered_ms), "missing_after_covered": len(late), "truncated": int(truncated),
                    "checked_from_ms": float(lo), "missing_examples": [(k, rest[k]) for k in missing[:5]], "extra_examples": [(k, mine[k]) for k in extra[:5]],
                    "mismatch_examples": [(k, rest[k], mine[k]) for k in mism[:3]]}
    # ids are contiguous per symbol: every missing id between two recorded ids is a lost trade (whole pool, not only the REST symbols)
    gaps, gap_syms, examples, trades = 0, 0, [], 0
    for sym, v in rec.items():
        ordered = sorted(v.items())
        trades += len(ordered)
        g = 0
        for (i0, _), (i1, x1) in zip(ordered, ordered[1:]):
            if i1 - i0 > 1 and x1[3] >= a.covered_ms:
                g += i1 - i0 - 1
                if len(examples) < 8:
                    examples.append((sym, i0, i1, x1[3]))
        gaps += g
        gap_syms += g > 0
    pool_ids = {"symbols": len(rec), "trades": trades, "gaps_after_covered": gaps, "symbols_with_gaps": gap_syms, "examples": examples}
    return res, pool_ids, {k: snap.get(k) for k in ("fetched_ms", "took_s", "errors")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exchange", choices=["binance", "bybit", "okx", "bingx"])
    ap.add_argument("root")
    ap.add_argument("t0", type=int)
    ap.add_argument("t1", type=int)
    ap.add_argument("--symbols", type=int, default=25)
    ap.add_argument("--covered-ms", type=int, default=0, help="from when every shard had a live session (cold start): missing_after_covered counts only trades from then on")
    ap.add_argument("--min-run", type=int, default=0, help="only files of runs started at/after this epoch second")
    ap.add_argument("--spread", action="store_true", help="symbols evenly spaced over the trade-count ranking instead of the busiest")
    ap.add_argument("--snap", help="bingx: REST snapshot of the window (bxsnap.py)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    global MIN_RUN
    MIN_RUN = a.min_run
    extra = {}
    if a.exchange == "bingx":
        res, pool_ids, snap_info = bingx(a, a.t0, a.t1)
        extra = {"pool_ids": pool_ids, "snapshot": snap_info}
    else:
        res = {"binance": binance, "bybit": bybit, "okx": okx}[a.exchange](a, a.t0, a.t1)
    tot = Counter()
    for v in res.values():
        for k, x in v.items():
            if isinstance(x, int):
                tot[k] += x
    out = {"exchange": a.exchange, "window_ms": [a.t0, a.t1], "symbols": len(res), "total": dict(tot), "per_symbol": res, **extra}
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps({**out["total"], **({"id_gaps_after_covered": extra["pool_ids"]["gaps_after_covered"]} if extra else {})}))


if __name__ == "__main__":
    main()
