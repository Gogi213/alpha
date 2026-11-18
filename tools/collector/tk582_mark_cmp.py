#!/usr/bin/env python3
"""TK-582 v3: сравнение mark00 боевого и копии по кадрам (вход — JSON `tk582_mark_frames.py`, по одному файлу на сутки: `--live a.json b.json --copy c.json d.json`).
Печатает: строки `m` и кадры по сторонам, кадры только у одной стороны, неполные кадры (символов меньше максимума), повторы строк (строк > символов), расхождение по символам в общих кадрах, по часам.
Кадр — секунда события E; сравниваются маски символов."""
import argparse
import collections
import datetime
import json


def load(paths):
    syms, ids, frames = [], {}, {}
    for p in paths:
        d = json.load(open(p))
        names = d["syms"]
        gl = []
        for s in names:
            if s not in ids:
                ids[s] = len(syms)
                syms.append(s)
            gl.append(ids[s])
        ident = gl == list(range(len(gl)))
        for sec, (n, h) in d["frames"].items():
            m = int(h, 16)
            if not ident:
                m2, i = 0, 0
                while m:
                    if m & 1:
                        m2 |= 1 << gl[i]
                    m >>= 1
                    i += 1
                m = m2
            sec = int(sec)
            if sec in frames:   # кадр на границе суток
                frames[sec] = [frames[sec][0] + n, frames[sec][1] | m]
            else:
                frames[sec] = [n, m]
    return syms, frames


def remap(frames, src, dst):
    ids = {s: i for i, s in enumerate(dst)}
    perm = [ids[s] for s in src]
    if perm == list(range(len(perm))):
        return frames
    out = {}
    for sec, (n, m) in frames.items():
        m2, i = 0, 0
        while m:
            if m & 1:
                m2 |= 1 << perm[i]
            m >>= 1
            i += 1
        out[sec] = [n, m2]
    return out


def pc(x):
    return bin(x).count("1")


def hh(sec):
    return datetime.datetime.fromtimestamp(sec, datetime.timezone.utc).strftime("%m-%d %H")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", nargs="+", required=True)
    ap.add_argument("--copy", nargs="+", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    ls, lf = load(a.live)
    cs, cf = load(a.copy)
    allsyms = sorted(set(ls) | set(cs))
    lf, cf = remap(lf, ls, allsyms), remap(cf, cs, allsyms)
    print("символов в окне: боевой %d, копия %d, общих %d" % (len(ls), len(cs), len(set(ls) & set(cs))))
    full = {"боевой": max((pc(m) for _, m in lf.values()), default=0), "копия": max((pc(m) for _, m in cf.values()), default=0)}
    print("максимум символов в кадре:", full)
    res = {"symbols": {"live": len(ls), "copy": len(cs)}}
    for name, fr in (("боевой", lf), ("копия", cf)):
        rows = sum(v[0] for v in fr.values())
        dist = sum(pc(v[1]) for v in fr.values())
        part = sum(1 for v in fr.values() if pc(v[1]) < full[name])
        print("%s: кадров %d, строк m %d, различных (символ, кадр) %d, повторов строк %d, неполных кадров %d" % (name, len(fr), rows, dist, rows - dist, part))
        res[name] = {"frames": len(fr), "rows": rows, "distinct": dist, "dup_rows": rows - dist, "partial_frames": part}
    only_l, only_c = sorted(set(lf) - set(cf)), sorted(set(cf) - set(lf))
    both = sorted(set(lf) & set(cf))
    print("кадров только у боевого %d, только у копии %d, общих %d" % (len(only_l), len(only_c), len(both)))
    miss_c = sum(pc(lf[s][1] & ~cf[s][1]) for s in both)   # у боевого есть, у копии нет
    miss_l = sum(pc(cf[s][1] & ~lf[s][1]) for s in both)
    print("в общих кадрах: пары (символ, кадр) только у боевого %d, только у копии %d" % (miss_c, miss_l))
    res.update({"frames_only_live": len(only_l), "frames_only_copy": len(only_c), "frames_common": len(both), "pairs_only_live": miss_c, "pairs_only_copy": miss_l})
    per_hour = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for s in set(lf) | set(cf):
        h = per_hour[hh(s)]
        if s in lf:
            h[0] += pc(lf[s][1])
        if s in cf:
            h[1] += pc(cf[s][1])
        h[2] += s in lf and s not in cf
        h[3] += s in cf and s not in lf
        if s in lf and s in cf:
            h[4] += pc(lf[s][1] & ~cf[s][1])
            h[5] += pc(cf[s][1] & ~lf[s][1])
    print("\nчас UTC | пар боевой | пар копия | кадров только боевой | только копия | пар только боевой | только копия")
    for h in sorted(per_hour):
        print(h, *per_hour[h], sep=" | ")
    res["per_hour"] = {h: v for h, v in per_hour.items()}

    def runs(secs):
        out = []
        for s in secs:
            if out and s - out[-1][1] <= 1:
                out[-1][1] = s
            else:
                out.append([s, s])
        return out
    for nm, secs in (("кадры только у боевого", only_l), ("кадры только у копии", only_c)):
        rr = runs(secs)
        print("\n%s: %d кадров в %d отрезках; первые 20 (начало UTC, длина с):" % (nm, len(secs), len(rr)))
        for x, y in rr[:20]:
            print("  %s +%d" % (datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime("%m-%d %H:%M:%S"), y - x + 1))
        res[nm] = [[x, y - x + 1] for x, y in rr]
    if a.json:
        json.dump(res, open(a.json, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
