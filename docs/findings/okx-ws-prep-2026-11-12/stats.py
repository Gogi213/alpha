#!/usr/bin/env python3
"""Разбор кадров пробы OKX (cap.py):
  stats.py one F.jsonl.zst [...]   — по каналам: кадров/с, КБ/с, лаг rx−ts p50/p99/max (первые GRACE_S после подписки не считаются), формы data, непрерывность seqId/tradeId
  stats.py cmp REF.jsonl.zst OTHER.jsonl.zst [...] — те же (канал, символ, seqId) на разных соединениях в общем интервале приёма: кто и насколько позже, чего нет на одном из них"""
import collections, io, json, sys
import zstandard

GRACE_S = 5.0


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def lines(path):
    """Заголовок и поток записей {rx_ns, f} файла cap.py."""
    f = open(path, "rb")
    r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8")
    hdr = json.loads(r.readline())
    return hdr, (json.loads(ln) for ln in r)


def one(path):
    ch = collections.defaultdict(lambda: {"frames": 0, "bytes": 0, "recs": 0, "lag": [], "keys": collections.Counter(), "dlen": collections.Counter(), "empty": 0, "seq_bad": 0, "ts_dup": 0, "ts_dup_diff": 0})
    ev, first, last = collections.Counter(), None, None
    seq, tid, trade = {}, {}, collections.Counter()
    seen_ts = {}  # (канал, символ, ts) -> данные: повтор ключа ts у mark-price / index-tickers / funding-rate ломает снятие дублей по E
    gaps = collections.Counter()
    hdr, it = lines(path)
    for d in it:
        rx, fr = d["rx_ns"], d["f"]
        if fr == "pong":
            ev["pong"] += 1
            continue
        j = json.loads(fr)
        if "event" in j:
            ev[j["event"] + (":" + j["code"] if "code" in j else "")] += 1
            continue
        first = first or rx
        last = rx
        a = j["arg"]["channel"]
        c = ch[a]
        c["frames"] += 1
        c["bytes"] += len(fr)
        c["dlen"][len(j["data"])] += 1
        for x in j["data"]:
            c["recs"] += 1
            c["keys"][tuple(sorted(x))] += 1
            if rx - first >= GRACE_S * 1e9:
                c["lag"].append(rx // 1_000_000 - int(x["ts"]))
            iid = x.get("instId") or j["arg"]["instId"]
            if a == "bbo-tbt":
                if not x["asks"] or not x["bids"]:
                    c["empty"] += 1
                elif len(x["asks"]) != 1 or len(x["bids"]) != 1 or len(x["asks"][0]) != 4 or x["asks"][0][2] != "0":
                    c["seq_bad"] += 1000000  # форма не та: 1 уровень × 4 поля, третье «0»
            if a in ("mark-price", "index-tickers", "funding-rate"):
                kk = (a, iid, x["ts"])
                if kk in seen_ts:
                    c["ts_dup"] += 1
                    c["ts_dup_diff"] += seen_ts[kk] != x
                seen_ts[kk] = x
            if "seqId" in x and a in ("bbo-tbt", "books5"):
                p = seq.get((a, iid))
                if p is not None and x["seqId"] <= p:
                    c["seq_bad"] += 1
                seq[(a, iid)] = x["seqId"]
            if a == "trades":
                t, n = int(x["tradeId"]), int(x["count"])
                p = tid.get(iid)
                if p is not None:
                    gaps[t - p - n] += 1
                tid[iid] = t
                trade["source=" + x["source"]] += 1
                trade["side=" + x["side"]] += 1
                trade["count=1" if n == 1 else "count>1"] += 1
    dur = (last - first) / 1e9 if first else 0
    out = {"file": path, "hdr": hdr, "dur_s": round(dur, 1), "events": dict(ev), "trades": dict(trade), "trade_id_gap_dist": dict(gaps.most_common(8)), "channels": {}}
    for a, c in sorted(ch.items()):
        out["channels"][a] = {"frames": c["frames"], "recs": c["recs"], "frames_per_s": round(c["frames"] / dur, 1), "kb_per_s": round(c["bytes"] / dur / 1000, 1), "lag_n": len(c["lag"]), "lag_p50": pct(c["lag"], 0.5), "lag_p99": pct(c["lag"], 0.99), "lag_max": max(c["lag"]) if c["lag"] else None,
                              "data_len": dict(c["dlen"]), "empty_heartbeat": c["empty"], "seq_nonincreasing_or_shape_bad": c["seq_bad"], "same_ts_repeats": c["ts_dup"], "same_ts_different_payload": c["ts_dup_diff"], "key_sets": {",".join(k): n for k, n in c["keys"].most_common(4)}}
    out["total_frames_per_s"] = round(sum(c["frames"] for c in ch.values()) / dur, 1)
    out["total_kb_per_s"] = round(sum(c["bytes"] for c in ch.values()) / dur / 1000, 1)
    return out


def keyed(path):
    """(заголовок, {ключ: rx_ns}, первый rx, последний rx) по (канал, символ, seqId) каналов с seqId."""
    m, lo, hi = {}, None, None
    hdr, it = lines(path)
    for d in it:
        if d["f"] == "pong":
            continue
        j = json.loads(d["f"])
        if "arg" not in j or "data" not in j:
            continue
        lo = lo or d["rx_ns"]
        hi = d["rx_ns"]
        a = j["arg"]["channel"]
        if a not in ("bbo-tbt", "books5", "trades"):
            continue
        for x in j["data"]:
            m.setdefault((a, x.get("instId") or j["arg"]["instId"], x["seqId"]), d["rx_ns"])
    return hdr, m, lo, hi


def cmp(paths, margin_s=2.0):
    """Сравнение только по общему интервалу приёма (с отступом margin_s с каждого края): окна соединений сдвинуты на секунды, края — не потери."""
    data = [keyed(p) for p in paths]
    t0 = max(d[2] for d in data) + margin_s * 1e9
    t1 = min(d[3] for d in data) - margin_s * 1e9
    win = [{k: v for k, v in d[1].items() if t0 <= v <= t1} for d in data]
    ref = win[0]
    out = []
    for p, d, o in zip(paths[1:], data[1:], win[1:]):
        both = ref.keys() & o.keys()
        dl = [(o[k] - ref[k]) / 1e6 for k in both]
        out.append({"ref": paths[0], "other": p, "ref_peer": data[0][0]["peer"], "other_peer": d[0]["peer"], "window_s": round((t1 - t0) / 1e9, 1), "common": len(both), "only_ref": len(ref.keys() - o.keys()), "only_other": len(o.keys() - ref.keys()),
                    "delta_ms_p1": pct(dl, 0.01), "delta_ms_p50": pct(dl, 0.5), "delta_ms_p99": pct(dl, 0.99), "delta_ms_max": max(dl) if dl else None, "delta_ms_min": min(dl) if dl else None})
    return out


def cmp_ts(paths, margin_s=2.0):
    """Для каналов без seqId (mark-price, funding-rate, index-tickers): ключ (канал, символ, ts) одинаков на двух соединениях? Общий интервал приёма, как в cmp."""
    data = []
    for p in paths:
        hdr, it = lines(p)
        m, lo, hi = {}, None, None
        for d in it:
            if d["f"] == "pong":
                continue
            j = json.loads(d["f"])
            if "arg" not in j or "data" not in j:
                continue
            lo = lo or d["rx_ns"]
            hi = d["rx_ns"]
            for x in j["data"]:
                m.setdefault((j["arg"]["channel"], j["arg"]["instId"], x["ts"]), d["rx_ns"])
        data.append((m, lo, hi))
    t0 = max(d[1] for d in data) + margin_s * 1e9
    t1 = min(d[2] for d in data) - margin_s * 1e9
    win = [{k: v for k, v in d[0].items() if t0 <= v <= t1} for d in data]
    out = []
    for p, o in zip(paths[1:], win[1:]):
        both = win[0].keys() & o.keys()
        dl = [(o[k] - win[0][k]) / 1e6 for k in both]
        by = collections.Counter(k[0] for k in win[0])
        out.append({"other": p, "window_s": round((t1 - t0) / 1e9, 1), "ref_keys_by_channel": dict(by), "common": len(both), "only_ref": len(win[0].keys() - o.keys()), "only_other": len(o.keys() - win[0].keys()), "delta_ms_p50": pct(dl, 0.5), "delta_ms_p99": pct(dl, 0.99), "delta_ms_min": min(dl) if dl else None, "delta_ms_max": max(dl) if dl else None})
    return out


if __name__ == "__main__":
    if sys.argv[1] == "one":
        for p in sys.argv[2:]:
            print(json.dumps(one(p), ensure_ascii=False))
    elif sys.argv[1] == "cmp-ts":
        for r in cmp_ts(sys.argv[2:]):
            print(json.dumps(r))
    else:
        for r in cmp(sys.argv[2:]):
            print(json.dumps(r))
