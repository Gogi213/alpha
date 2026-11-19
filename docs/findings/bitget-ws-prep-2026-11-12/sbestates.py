#!/usr/bin/env python3
"""SBE books1 Bitget: расшифровка верха книги и сверка с JSON v3 books1 на общих (символ, seq).
  sbestates.py frames/S1-A-sbe100.jsonl.zst frames/S1-B-v3json100.jsonl.zst > results/S1-sbe-vs-json.json
Печатает: записей и разных состояний верха (bid, bsz, ask, asz) в SBE и в JSON (на одних символах; первые 5 с пропущены), число общих (символ, seq), расхождений значений на общих, длину кадра SBE, blockLength, число символов."""
import collections, decimal, json, os, struct, sys

sys.path.insert(0, "/data/bitget-probe/g022-prep-3-tools")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "g022-prep-3-tools"))
import stats

D = decimal.Decimal


def sbe(path):
    out, n, last, cnt, lens, bls = {}, 0, {}, 0, collections.Counter(), collections.Counter()
    hdr, it = stats.lines(path)
    first = None
    for d in it:
        fr = d.get("f", "")
        if not fr.startswith("hex:"):
            continue
        first = first or d["rx_ns"]
        if d["rx_ns"] - first < 5e9:
            continue
        b = bytes.fromhex(fr[4:])
        bl, tid, sid, ver = struct.unpack_from("<HHHH", b, 0)
        ts, bp, bs, ap, asz = struct.unpack_from("<Qqqqq", b, 8)
        pe, se = struct.unpack_from("<bb", b, 48)
        seq, sts = struct.unpack_from("<QQ", b, 50)
        sl = b[8 + bl]
        sym = b[8 + bl + 1 : 8 + bl + 1 + sl].decode()
        st = (D(bp).scaleb(pe), D(bs).scaleb(se), D(ap).scaleb(pe), D(asz).scaleb(se))
        n += 1
        lens[len(b)] += 1
        bls[(bl, ver, sid)] += 1
        if last.get(sym) != st:
            cnt += 1
        last[sym] = st
        out[(sym, seq)] = st
    return out, n, cnt, lens, bls, len(last)


def js(path, syms):
    out, n, last, cnt = {}, 0, {}, 0
    hdr, it = stats.lines(path)
    first = None
    for d in it:
        fr = d.get("f", "")
        if not fr.startswith("{"):
            continue
        j = json.loads(fr)
        if "data" not in j or j["arg"].get("topic") != "books1":
            continue
        first = first or d["rx_ns"]
        if d["rx_ns"] - first < 5e9:
            continue
        sym = j["arg"]["symbol"]
        for x in j["data"]:
            st = (D(x["b"][0][0]), D(x["b"][0][1]), D(x["a"][0][0]), D(x["a"][0][1]))
            n += 1
            if last.get(sym) != st:
                cnt += 1
            last[sym] = st
            out[(sym, x["seq"])] = st
    return out, n, cnt


s, ns, cs, lens, bls, nsym = sbe(sys.argv[1])
j, nj, cj = js(sys.argv[2], None)
common = s.keys() & j.keys()
bad = sum(1 for k in common if s[k] != j[k])
print(json.dumps({"sbe": {"file": sys.argv[1], "records": ns, "distinct_top_states": cs, "symbols": nsym, "frame_len": dict(lens.most_common(5)), "blockLength_version_schema": {str(k): v for k, v in bls.items()}},
                  "json_v3": {"file": sys.argv[2], "records": nj, "distinct_top_states": cj}, "common_symbol_seq": len(common), "value_mismatch_on_common": bad, "only_sbe": len(s.keys() - j.keys()), "only_json": len(j.keys() - s.keys())}))
