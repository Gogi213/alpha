#!/usr/bin/env python3
"""TK-582 v3: сверка записей книги / сделок боевого и копии по личности записи, только по символам пула копии, по часам события E (сырые файлы шардов, только чтение).
Личность записи: книга — (символ, u биржи), сделка — (символ, a биржи); оба восстанавливаются из разностей столбца (`docs/collector-ws-format.md`: `du = u − u предыдущей записи символа`, `da = a − (a предыдущей + 1)`; первая запись символа в файле — абсолютное).
  dump: python3 tk582_rec_recon.py dump --kind book --pool /data/tk582v3/pool.json --from-ms T --to-ms T --out x.pkl <файл.tsv.zst>…   (файлы — один процесс, по порядку суток; разжимает `zstd -dc`)
  cmp:  python3 tk582_rec_recon.py cmp --live live.pkl --copy copy.pkl [--json out.json]   (по часам: записей боевого / копии, общих, только боевого, только копии)
Строки с E вне [from, to) пропускаются; час — UTC по E. Запуск на srv под `nice -n 19` и `systemd-run`."""
import argparse
import array
import collections
import datetime
import json
import pickle
import subprocess
import sys


def dump(a):
    pool = set(s.encode() for s in json.load(open(a.pool))["pool"])
    ids = {}
    last = {}
    cur = collections.defaultdict(set)   # час -> ключи
    done = {}
    kind = b"b" if a.kind == "book" else b"t"

    def flush(hh):   # час мог прийти из прежнего файла (шарды и сутки идут по очереди)
        keys = cur.pop(hh)
        if hh in done:
            keys.update(done[hh])
        done[hh] = array.array("Q", sorted(keys))
    rows = kept = 0
    for path in a.files:
        rx = 0
        last.clear()   # разности — внутри файла
        p = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE)
        for ln in p.stdout:
            f = ln.split(b"\t", 8)
            if len(f) < 2:
                continue
            try:
                d = int(f[1])
            except ValueError:
                continue
            rx = d if d > 10**17 else rx + d   # dns есть у каждой строки файла (c, r, g тоже): пропуск любой уводит приём
            if f[0] != kind or len(f) < 6:
                continue
            s = f[4]
            if s not in pool:
                continue
            rows += 1
            x = int(f[5])
            if kind == b"b":
                u = last[s] + x if s in last else x
            else:
                u = last[s] + 1 + x if s in last else x
            last[s] = u
            e = rx // 1_000_000 - int(f[2])
            if e < a.from_ms or e >= a.to_ms:
                continue
            i = ids.get(s)
            if i is None:
                i = ids[s] = len(ids)
            h = e // 3_600_000
            cur[h].add((i << 48) | u)
            kept += 1
            if len(cur) > 3:   # E приходит почти по порядку; час закрыт, когда открыт на 2 часа новее
                for hh in sorted(cur)[:-2]:
                    flush(hh)
        p.wait()
        for hh in list(cur):
            flush(hh)
    names = sorted(ids, key=ids.get)
    pickle.dump({"hours": done, "syms": [s.decode() for s in names]}, open(a.out, "wb"))
    print("строк пула %d, в отрезке %d, часов %d, символов %d" % (rows, kept, len(done), len(ids)), file=sys.stderr)


def cmp_(a):
    L, C = pickle.load(open(a.live, "rb")), pickle.load(open(a.copy, "rb"))
    lid = {s: i for i, s in enumerate(L["syms"])}
    cid = {s: i for i, s in enumerate(C["syms"])}
    perm = {cid[s]: lid.get(s, 10**6 + cid[s]) for s in cid}   # номер символа копии -> номер у боевого
    tot = collections.Counter()
    res = {}
    print("час UTC | боевой | копия | общих | только боевой | только копия | доля копии от боевого")
    for h in sorted(set(L["hours"]) | set(C["hours"])):
        la = set(L["hours"].get(h, ()))
        cb = set(((perm[k >> 48] << 48) | (k & ((1 << 48) - 1))) for k in C["hours"].get(h, ()))
        both = len(la & cb)
        row = [len(la), len(cb), both, len(la) - both, len(cb) - both]
        for k, v in zip(("live", "copy", "common", "only_live", "only_copy"), row):
            tot[k] += v
        res[h] = row
        print(datetime.datetime.fromtimestamp(h * 3600, datetime.timezone.utc).strftime("%m-%d %H"), *row, "%.2f %%" % (100.0 * len(cb) / max(1, len(la))), sep=" | ")
    print("всего", *(tot[k] for k in ("live", "copy", "common", "only_live", "only_copy")), "%.2f %%" % (100.0 * tot["copy"] / max(1, tot["live"])), sep=" | ")
    if a.json:
        json.dump({"hours": res, "total": dict(tot)}, open(a.json, "w"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--kind", choices=("book", "trade"), required=True)
    d.add_argument("--pool", required=True)
    d.add_argument("--from-ms", type=int, required=True)
    d.add_argument("--to-ms", type=int, required=True)
    d.add_argument("--out", required=True)
    d.add_argument("files", nargs="+")
    c = sub.add_parser("cmp")
    c.add_argument("--live", required=True)
    c.add_argument("--copy", required=True)
    c.add_argument("--json")
    a = ap.parse_args()
    dump(a) if a.cmd == "dump" else cmp_(a)


if __name__ == "__main__":
    main()
