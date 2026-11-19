#!/usr/bin/env python3
"""Доп. разборы BingX: extra.py MODE FILE
  rates   — bookTicker/incrDepth/depth5: записей/с на самых активных символах и число разных состояний верха (bid, bsz, ask, asz) / записей (первые 5 с пропущены)
  trades  — trade: порядок id (t) внутри символа в порядке прихода: сколько раз id уменьшился, примеры; дубли id; доля m=true; число сделок в кадре
  mark    — markPrice: интервал между кадрами символа, доля кадров с изменившейся ценой p
  frames  — по одному примеру кадра каждого вида"""
import collections, json, os, sys

sys.path.insert(0, "/data/bingx-probe/g022-prep-3-tools")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "g022-prep-3-tools"))
import stats

mode, path = sys.argv[1], sys.argv[2]
hdr, it = stats.lines(path)
t0 = hdr["rx_ns"]
if mode == "rates":
    cnt, last, st, tot = collections.Counter(), {}, collections.Counter(), collections.Counter()
    first = None
    for d in it:
        if "f" not in d or not d["f"].startswith("{"):
            continue
        j = json.loads(d["f"])
        dt, x = j.get("dataType", ""), j.get("data")
        if not dt or not isinstance(x, dict):
            continue
        first = first or d["rx_ns"]
        if d["rx_ns"] - first < 5e9:
            continue
        sym, _, kind = dt.partition("@")
        if kind == "bookTicker":
            s = (x["b"], x["B"], x["a"], x["A"])
        elif kind.startswith("depth5"):
            s = (tuple(x["bids"][0]), tuple(x["asks"][0]))
        elif kind == "incrDepth":
            continue
        else:
            continue
        cnt[(kind, sym)] += 1
        tot[kind] += 1
        if last.get((kind, sym)) != s:
            st[kind] += 1
        last[(kind, sym)] = s
    dur = (d["rx_ns"] - first) / 1e9 - 5
    out = {"records": dict(tot), "distinct_top_states": dict(st), "window_s": round(dur, 1)}
    for kind in tot:
        top = [(s, round(n / dur, 2)) for (k, s), n in cnt.most_common() if k == kind][:6]
        out["top_" + kind] = top
    print(json.dumps(out))
elif mode == "trades":
    last, back, dup, ex, n, mk, per = {}, 0, 0, [], 0, 0, collections.Counter()
    seen = set()
    for d in it:
        if "f" not in d or not d["f"].startswith("{"):
            continue
        j = json.loads(d["f"])
        if not j.get("dataType", "").endswith("@trade"):
            continue
        per[len(j["data"])] += 1
        for x in sorted(j["data"], key=lambda x: int(x["t"])):
            n += 1
            mk += bool(x["m"])
            k, t = x["s"], int(x["t"])
            if (k, t) in seen:
                dup += 1
            seen.add((k, t))
            p = last.get(k)
            if p is not None and t < p[0]:
                back += 1
                if len(ex) < 4:
                    ex.append({"sym": k, "prev_t": p[0], "prev_T": p[1], "t": t, "T": x["T"]})
            last[k] = (t, x["T"]) if p is None or t > p[0] else p
    print(json.dumps({"trades": n, "id_decreased_vs_max_seen": back, "dup_id": dup, "m_true_pct": round(100 * mk / n, 1), "trades_per_frame": dict(sorted(per.items())[:8]), "examples": ex}))
elif mode == "mark":
    last, lt, gaps, chg, n = {}, {}, [], 0, 0
    for d in it:
        if "f" not in d or not d["f"].startswith("{"):
            continue
        j = json.loads(d["f"])
        if not j.get("dataType", "").endswith("@markPrice"):
            continue
        x = j["data"]
        s = x["s"]
        if s in last:
            n += 1
            chg += x["p"] != last[s]
            gaps.append(x["E"] - lt[s])
        last[s], lt[s] = x["p"], x["E"]
    gaps.sort()
    print(json.dumps({"pairs": n, "p_changed_pct": round(100 * chg / n, 1), "gap_ms_p50": gaps[len(gaps) // 2], "gap_ms_p99": gaps[int(len(gaps) * 0.99)], "keys": sorted(x.keys())}))
elif mode == "frames":
    seen = set()
    for d in it:
        if "f" not in d:
            continue
        k = d["f"][:1] if not d["f"].startswith("{") else (json.loads(d["f"]).get("dataType", "").partition("@")[2][:12] or "ack")
        if k not in seen:
            seen.add(k)
            print(k, d["f"][:420])
