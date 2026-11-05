#!/usr/bin/env python3
"""Проверка выходных файлов xdl: python3 check_out.py <out>/<exchange> [<other out>/<exchange> для побайтного сравнения]"""
import sys, os, gzip, csv, glob, datetime, collections

def rd(p):
    with gzip.open(p, "rt") as f:
        return list(csv.reader(f))

def main():
    d = sys.argv[1]
    other = sys.argv[2] if len(sys.argv) > 2 else None
    tot = collections.Counter()
    for p in sorted(glob.glob(os.path.join(d, "candles", "*.csv.gz"))):
        sym = os.path.basename(p)[:-7]
        R = rd(p)
        hdr, rows = R[0], R[1:]
        assert hdr == ["t_open_ms", "o", "h", "l", "c", "v", "quote_v"], hdr
        ts = [int(r[0]) for r in rows]
        dup = len(ts) - len(set(ts))
        unsorted = sum(1 for i in range(1, len(ts)) if ts[i] <= ts[i - 1])
        notmin = sum(1 for t in ts if t % 60000)
        bad_pos = bad_hl = zero_v = neg = emptyq = 0
        for r in rows:
            o, h, l, c = map(float, r[1:5])
            v = float(r[5])
            if not (o > 0 and h > 0 and l > 0 and c > 0):
                bad_pos += 1
            if h < max(o, c) - 1e-12 or l > min(o, c) + 1e-12:
                bad_hl += 1
            if v == 0:
                zero_v += 1
            if v < 0:
                neg += 1
            if r[6] == "":
                emptyq += 1
        gaps = ((ts[-1] - ts[0]) // 60000 + 1 - len(ts)) if ts else 0
        f = rd(os.path.join(d, "funding", sym + ".csv.gz"))
        frows = f[1:]
        ih = collections.Counter(r[2] for r in frows)
        ft = [int(r[0]) for r in frows]
        fi = collections.Counter(round((ft[i] - ft[i - 1]) / 3.6e6, 2) for i in range(1, len(ft)))
        first = rows[0][0] if rows else None
        fmt = lambda t: datetime.datetime.fromtimestamp(int(t) / 1000, datetime.timezone.utc).strftime("%m-%d %H:%M")
        print("%-14s n=%5d dup=%d unsorted=%d notmin=%d gaps=%d bad_pos=%d bad_hl=%d zero_v=%d neg_v=%d emptyq=%d | %s..%s | c0=%s v0=%s qv0=%s | fund n=%d iv=%s diffs=%s" % (
            sym, len(rows), dup, unsorted, notmin, gaps, bad_pos, bad_hl, zero_v, neg, emptyq,
            fmt(ts[0]) if ts else "-", fmt(ts[-1]) if ts else "-", rows[0][4] if rows else "-", rows[0][5] if rows else "-", rows[0][6] if rows else "-",
            len(frows), dict(ih), dict(fi)))
        for k, v in (("dup", dup), ("unsorted", unsorted), ("notmin", notmin), ("bad_pos", bad_pos), ("bad_hl", bad_hl), ("zero_v", zero_v), ("neg", neg)):
            tot[k] += v
        tot["rows"] += len(rows)
        tot["files"] += 1
        if other:
            q = os.path.join(other, "candles", sym + ".csv.gz")
            if os.path.exists(q):
                same = rd(q) == R
                same_f = rd(os.path.join(other, "funding", sym + ".csv.gz")) == f
                print("    сравнение с %s: свечи %s, фандинг %s" % (other, "ИДЕНТИЧНЫ" if same else "РАЗЛИЧАЮТСЯ", "идентичен" if same_f else "РАЗЛИЧАЕТСЯ"))
                if not same:
                    A, B = {r[0]: r for r in R[1:]}, {r[0]: r for r in rd(q)[1:]}
                    print("      только тут %d, только там %d, значения отличаются %d" % (len(set(A) - set(B)), len(set(B) - set(A)), sum(1 for k in set(A) & set(B) if A[k] != B[k])))
    print("ИТОГО", dict(tot))

main()
