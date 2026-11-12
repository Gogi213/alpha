#!/usr/bin/env python3
"""Доп. разборы HTX: extra.py outliers FILE | funding FILE | states FILE
  outliers — записи bbo с лагом > 5 с: сколько, по каким символам, через сколько секунд после старта; доля односторонних кадров
  funding  — канал funding_rate (уведомления): частота обновлений на символ, как часто реально меняется funding_rate/estimated_rate, размер первого кадра
  states   — число разных состояний верха (bid, bsz, ask, asz) по каналам bbo / depth.step6 / depth.size_20.high_freq / detail на одних символах (первые 5 с пропущены)"""
import collections, json, os, sys

sys.path.insert(0, "/data/htx-probe/g022-prep-3-tools")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "g022-prep-3-tools"))
import stats

mode, path = sys.argv[1], sys.argv[2]
hdr, it = stats.lines(path)
t0 = hdr["rx_ns"]
if mode == "outliers":
    out, n, one = [], 0, 0
    for d in it:
        if "f" not in d or not d["f"].startswith('{"ch"'):
            continue
        j = json.loads(d["f"])
        if not j["ch"].endswith(".bbo"):
            continue
        n += 1
        t = j["tick"]
        one += not (t.get("bid") and t.get("ask"))
        lag = d["rx_ns"] // 1_000_000 - t["ts"]
        if lag > 5000:
            out.append((round((d["rx_ns"] - t0) / 1e9, 1), j["ch"].split(".")[1], lag))
    c = collections.Counter(s for _, s, _ in out)
    print(json.dumps({"bbo_frames": n, "one_sided": one, "lag_gt5s": len(out), "symbols": len(c), "first": out[:5], "by_symbol_top": c.most_common(5), "at_s_hist": dict(collections.Counter(int(a // 10) * 10 for a, _, _ in out))}))
elif mode == "funding":
    per, first, chg, tot = collections.Counter(), None, collections.Counter(), 0
    last = {}
    for d in it:
        if "f" not in d or not d["f"].startswith('{"op":"notify"'):
            continue
        j = json.loads(d["f"])
        if first is None:
            first = len(j["data"])
        for x in j["data"]:
            s = x["contract_code"]
            per[s] += 1
            tot += 1
            p = last.get(s)
            if p is not None:
                chg["frames"] += 1
                chg["funding_rate"] += x["funding_rate"] != p["funding_rate"]
                chg["estimated_rate"] += x["estimated_rate"] != p["estimated_rate"]
                chg["funding_time"] += x["funding_time"] != p["funding_time"]
                chg["settlement_time"] += x["settlement_time"] != p["settlement_time"]
                chg["any_but_ts"] += {k: v for k, v in x.items() if k != "funding_time"} != {k: v for k, v in p.items() if k != "funding_time"}
            last[s] = x
    v = sorted(per.values())
    print(json.dumps({"records": tot, "symbols": len(per), "first_frame_symbols": first, "records_per_symbol_p50": v[len(v) // 2], "records_per_symbol_max": v[-1], "changes": dict(chg), "sample": list(last.values())[:2]}))
elif mode == "states":
    last, cnt, rec = {}, collections.Counter(), collections.Counter()
    first = None
    for d in it:
        if "f" not in d or not d["f"].startswith('{"ch"'):
            continue
        first = first or d["rx_ns"]
        if d["rx_ns"] - first < 5e9:
            continue
        j = json.loads(d["f"])
        parts = j["ch"].split(".")
        kind, sym, t = ".".join(parts[2:]), parts[1], j["tick"]
        if kind == "bbo":
            st = (tuple(t.get("bid") or ()), tuple(t.get("ask") or ()))
        elif kind == "detail":
            st = (tuple(t.get("bid") or ()), tuple(t.get("ask") or ()))
        elif kind.startswith("depth"):
            st = ((tuple(t["bids"][0]) if t.get("bids") else ()), (tuple(t["asks"][0]) if t.get("asks") else ()))
        else:
            continue
        rec[kind] += 1
        if last.get((kind, sym)) != st:
            cnt[kind] += 1
        last[(kind, sym)] = st
    print(json.dumps({"file": path, "records": dict(rec), "distinct_top_states": dict(cnt)}))
