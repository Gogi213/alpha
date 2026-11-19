#!/usr/bin/env python3
"""Разбор кадров пробы cap.py для Bitget / HTX / BingX:
  stats.py one EX F.jsonl.zst [...]      — по каналам: кадров/с, КБ/с, записей/с, лаг rx−E p50/p99/max (первые GRACE_S после подписки не считаются),
                                           символов с данными, монотонность ключа (seq/id) по символу, повторы ключа, события/обрывы
  stats.py cmp EX REF.jsonl.zst OTHER... — те же (канал, символ, ключ) на разных соединениях в общем интервале приёма: кто и насколько позже, чего нет на одном из них
EX: bitget | htx | bingx. Для каждой биржи — функция parse(j) -> [(канал, символ, E_мс|None, ключ|None, метка)]."""
import collections, io, json, sys
import zstandard

GRACE_S = 5.0


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def lines(path):
    f = open(path, "rb")
    r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True), encoding="utf-8")
    hdr = json.loads(r.readline())
    return hdr, (json.loads(ln) for ln in r)


def parse_bitget(j):
    """-> [(канал, символ, E_мс, ключ, метка, P_мс)]; P — время кадра (push). Сделки внутри кадра идут от новой к старой — сортируются по tradeId по возрастанию."""
    out = []
    if "arg" not in j or "data" not in j:
        return out
    ch, sym, P = j["arg"]["channel"], j["arg"]["instId"], j.get("ts")
    data = sorted(j["data"], key=lambda x: int(x["tradeId"])) if ch == "trade" else j["data"]
    for x in data:
        if ch.startswith("books"):
            out.append((ch, sym, int(x["ts"]), x["seq"], "snap" if j.get("action") == "snapshot" else "upd", P))
        elif ch == "trade":
            out.append((ch, sym, int(x["ts"]), int(x["tradeId"]), j.get("action", "?") + "/" + x["side"], P))
        elif ch == "ticker":
            out.append((ch, sym, int(x["ts"]), int(x["ts"]), "tick", P))
        else:
            out.append((ch, sym, int(x["ts"]) if "ts" in x else None, None, "?", P))
    return out


def parse_bitget3(j):
    """Bitget UTA v3 (/v3/ws/public): arg.topic / arg.symbol; books1 {a,b,seq,pseq,ts}; ticker (без ts в записи — время кадра); publicTrade {i,p,v,S,T,L,isRPI}."""
    out = []
    if "arg" not in j or "data" not in j:
        return out
    ch, sym, P = j["arg"]["topic"], j["arg"]["symbol"], j.get("ts")
    data = sorted(j["data"], key=lambda x: int(x["i"])) if ch == "publicTrade" else j["data"]
    for x in data:
        if ch.startswith("books"):
            out.append((ch, sym, int(x["ts"]), x["seq"], "snap" if j.get("action") == "snapshot" else "upd", P))
        elif ch == "publicTrade":
            out.append((ch, sym, int(x["T"]), int(x["i"]), j.get("action", "?") + "/" + x["S"] + "/rpi=" + str(x.get("isRPI")), P))
        elif ch == "ticker":
            out.append((ch, sym, int(P), int(P), "tick", P))
        else:
            out.append((ch, sym, None, None, "?", P))
    return out


def parse_bitget_sbe(fr):
    """Bitget UTA SBE books1 (templateId 1002): hex-кадр -> [(books1, символ, ts_мс, seq, 'sbe', sts_мс)]. Раскладка — по blockLength кадра: ts@0, bid1P@8, bid1S@16, ask1P@24, ask1S@32, pExp@40, sExp@41, seq@42; sts@50 при blockLength ≥ 58."""
    import struct
    b = bytes.fromhex(fr[4:])
    bl, tid, sid, ver = struct.unpack_from("<HHHH", b, 0)
    if tid != 1002:
        return []
    ts, = struct.unpack_from("<Q", b, 8)
    seq, = struct.unpack_from("<Q", b, 8 + 42)
    sts = struct.unpack_from("<Q", b, 8 + 50)[0] // 1000 if bl >= 58 else None
    sl = b[8 + bl]
    sym = b[8 + bl + 1 : 8 + bl + 1 + sl].decode()
    return [("books1", sym, ts // 1000, seq, "sbe" + str(bl), sts)]


def _json_frame(f):
    return lambda fr: f(json.loads(fr)) if fr.startswith("{") else []


def parse_htx(j):
    """HTX linear swap: рынок {"ch":"market.SYM.kind","ts","tick"}; уведомления {"op":"notify","topic":"public.SYM.funding_rate","ts","data":[…]}.
    -> [(вид, символ, E_мс, ключ, метка, P_мс)]; P = ts кадра. bbo: E = tick.ts, ключ = tick.version; trade.detail: E = data.ts, ключ = data.id (по символу), метка = direction;
    depth.*: E = tick.ts, ключ = tick.version/mrid; detail/mark_price/index: E = ts кадра, ключ = ts кадра."""
    out = []
    if "ch" in j and "tick" in j:
        parts = j["ch"].split(".")
        sym, kind, t, P = parts[1], ".".join(parts[2:]), j["tick"], j["ts"]
        if kind == "bbo":
            out.append((kind, sym, t["ts"], t.get("version"), "bbo" + ("" if t.get("bid") and t.get("ask") else "-onesided"), P))
        elif kind == "trade.detail":
            for x in sorted(t["data"], key=lambda x: x["id"]):
                out.append((kind, sym, x["ts"], x["id"], x["direction"], P))
        elif kind.startswith("depth"):
            out.append((kind, sym, t["ts"], t.get("version") or t.get("mrid"), t.get("event", "snap"), P))
        else:
            out.append((kind, sym, P, P, "tick", P))
    elif j.get("op") == "notify" and "topic" in j:
        parts = j["topic"].split(".")
        for x in j.get("data", []):
            out.append((parts[2], parts[1], j["ts"], j["ts"], "notify", j["ts"]))
    return out


def parse_bingx(j):
    """BingX USDT-M swap-market: {"code":0,"dataType":"SYM@kind[@…]","data":…}. -> [(вид, символ, E_мс, ключ, метка, T_мс)]; для сравнения «время записи» E = data.E (событие), T = data.T (матчинг).
    bookTicker {e,u,E,T,s,b,B,a,A}: ключ u; trade [{t,q,p,T,m,s}]: E = T, ключ t (id сделки), метка m; markPrice/lastPrice/ticker {…,E,s,p|c}: ключ E; incrDepth {action,lastUpdateId,time}: ключ lastUpdateId; depthN@…: E = ts кадра."""
    out = []
    dt, d = j.get("dataType", ""), j.get("data")
    if not dt or d is None:
        return out
    sym, _, kind = dt.partition("@")
    k0 = kind.split("@")[0]
    if k0 == "bookTicker":
        out.append((k0, sym, d["E"], d["u"], "bt", d.get("T")))
    elif k0 == "trade":
        for x in sorted(d, key=lambda x: int(x["t"])):
            out.append((k0, sym, x["T"], int(x["t"]), "taker-sell" if x["m"] else "taker-buy", x["T"]))
    elif k0 in ("markPrice", "lastPrice", "ticker"):
        out.append((k0, sym, d["E"], d["E"], k0, d.get("T") or d.get("C")))
    elif k0 == "incrDepth":
        out.append((k0, sym, d["time"], d["lastUpdateId"], d["action"], d["time"]))
    elif k0.startswith("depth"):
        out.append((kind, sym, j.get("ts"), None, "snap", j.get("ts")))
    return out


PARSE = {"bingx": _json_frame(parse_bingx), "htx": _json_frame(parse_htx), "bitget": _json_frame(parse_bitget), "bitget3": _json_frame(parse_bitget3), "bitget-sbe": lambda fr: parse_bitget_sbe(fr) if fr.startswith("hex:") else []}


def one(ex, path):
    parse = PARSE[ex]
    ch = collections.defaultdict(lambda: {"frames": 0, "bytes": 0, "recs": 0, "lag": [], "syms": set(), "key_back": 0, "key_dup": 0, "flags": collections.Counter(), "dlen": collections.Counter(), "buck": collections.defaultdict(list), "push": []})
    ev, first, last, closed = collections.Counter(), None, None, None
    lastkey = {}
    other = collections.Counter()
    hdr, it = lines(path)
    for d in it:
        if "closed" in d or "error" in d:
            closed = d
            continue
        rx, fr = d["rx_ns"], d["f"]
        if not (fr.startswith("{") or fr.startswith("hex:")):
            ev["text:" + fr[:12]] += 1
            continue
        j = json.loads(fr) if fr.startswith("{") else {}
        recs = parse(fr)
        if not recs:
            ev[("event:" + str(j.get("event") or j.get("status") or j.get("op") or j.get("code") or j.get("ping") and "ping" or "other"))] += 1
            if len(other) < 5:
                other[fr[:200]] += 1
            continue
        first = first or rx
        last = rx
        c = ch[recs[0][0]]
        c["frames"] += 1
        c["bytes"] += d.get("z") or len(fr)
        c["dlen"][len(recs)] += 1
        for (a, sym, e, key, flag, P) in recs:
            c = ch[a]
            c["recs"] += 1
            c["syms"].add(sym)
            c["flags"][flag] += 1
            if e is not None and rx - first >= GRACE_S * 1e9:
                c["lag"].append(rx // 1_000_000 - e)
                c["buck"][int((rx - first) / 1e10)].append(rx // 1_000_000 - e > 250)
                if P is not None:
                    c["push"].append(int(P) - e)
            if key is not None:
                p = lastkey.get((a, sym))
                if p is not None:
                    if key == p:
                        c["key_dup"] += 1
                    elif key < p:
                        c["key_back"] += 1
                lastkey[(a, sym)] = key
    dur = (last - first) / 1e9 if first else 0
    out = {"file": path, "hdr": hdr, "dur_s": round(dur, 1), "events": dict(ev), "closed": closed, "channels": {}, "other_frames_sample": list(other)}
    for a, c in sorted(ch.items()):
        out["channels"][a] = {"frames": c["frames"], "recs": c["recs"], "frames_per_s": round(c["frames"] / dur, 1) if dur else None, "recs_per_s": round(c["recs"] / dur, 1) if dur else None, "kb_per_s": round(c["bytes"] / dur / 1000, 1) if dur else None,
                              "symbols": len(c["syms"]), "lag_n": len(c["lag"]), "lag_p50": pct(c["lag"], 0.5), "lag_p99": pct(c["lag"], 0.99), "lag_max": max(c["lag"]) if c["lag"] else None, "lag_gt250_pct": round(100 * sum(1 for x in c["lag"] if x > 250) / len(c["lag"]), 2) if c["lag"] else None,
                              "push_minus_E_p50": pct(c["push"], 0.5), "push_minus_E_p99": pct(c["push"], 0.99), "gt250_pct_by_10s": [round(100 * sum(v) / len(v)) for k, v in sorted(c["buck"].items())],
                              "key_dup": c["key_dup"], "key_back": c["key_back"], "data_len": dict(c["dlen"].most_common(4)), "flags": dict(c["flags"].most_common(6))}
    out["total_frames_per_s"] = round(sum(c["frames"] for c in ch.values()) / dur, 1) if dur else None
    out["total_kb_per_s"] = round(sum(c["bytes"] for c in ch.values()) / dur / 1000, 1) if dur else None
    return out


SKIP_KEY = {}  # ключ ticker = ts кадра, на разных соединениях разный (кадры по таймеру) — в cmp не участвует; см. cmp-ticker


def keyed(ex, path, only=None):
    parse = PARSE[ex]
    m, lo, hi = {}, None, None
    hdr, it = lines(path)
    for d in it:
        if "rx_ns" not in d or "f" not in d:
            continue
        recs = parse(d["f"])
        if not recs:
            continue
        lo = lo or d["rx_ns"]
        hi = d["rx_ns"]
        for (a, sym, e, key, flag, P) in recs:
            if key is not None and a not in SKIP_KEY.get(ex, ()) and (only is None or a in only):
                m.setdefault((a, sym, key), d["rx_ns"])
    return hdr, m, lo, hi


def cmp(ex, paths, margin_s=2.0, only=None):
    data = [keyed(ex, p, only) for p in paths]
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


if __name__ == "__main__":
    if sys.argv[1] == "one":
        for p in sys.argv[3:]:
            print(json.dumps(one(sys.argv[2], p), ensure_ascii=False))
    elif sys.argv[1] == "cmp-only":  # stats.py cmp-only EX канал1,канал2 REF OTHER… — ключи только этих каналов (включая SKIP_KEY)
        SKIP_KEY.clear()
        for r in cmp(sys.argv[2], sys.argv[4:], only=set(sys.argv[3].split(","))):
            print(json.dumps(r))
    else:
        for r in cmp(sys.argv[2], sys.argv[3:]):
            print(json.dumps(r))
