#!/usr/bin/env python3
"""TK-532: разбор пробы входов (файлы entryprobe.py). Режимы:
  entrycmp.py summary DIR                    — по входам: соединения, POP/адрес, записей, поздних (lag > 2 с), макс. лаг, опоздание цикла событий
  entrycmp.py same DIR [--ref com] [--sec 60] [--from-ms T] — тот же рынок: совпадение записей входа с опорным по ключу, цене, E, T (первые sec секунд общего интервала)
  entrycmp.py episodes DIR [--gap 30] [--ref com] — эпизоды (поздние записи любого входа, разрыв > gap с) и по входам: кто опоздал, у кого та же запись пришла вовремя
Лаг записи = rx_ms − E (E — время биржи в кадре); «вовремя» — не более ON_TIME_MS.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

LATE_MS = 2000
ON_TIME_MS = 300


def entrances(d):
    return sorted(os.path.basename(p)[4:-8] for p in glob.glob(os.path.join(d, "rec-*.tsv.zst")))


def read_recs(d, name, t0=None, t1=None):
    """Записи входа с rx_ms в [t0, t1]: (rx, kind, sym, key, E, T, px, conn). Файл идёт по rx; читаем потоком."""
    import zstandard

    with open(os.path.join(d, f"rec-{name}.tsv.zst"), "rb") as f:
        r = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True)
        buf = b""
        while True:
            try:
                chunk = r.read(1 << 20)
            except zstandard.ZstdError:  # файл пишется: хвост без закрытия блока
                break
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                p = ln.decode().split("\t")
                if len(p) != 8:
                    continue
                rx = int(p[0])
                if t0 is not None and rx < t0:
                    continue
                if t1 is not None and rx > t1:
                    return
                yield rx, p[1], p[2], int(p[3]), int(p[4]), p[5], p[6], p[7]


def jl(path):
    try:
        return [json.loads(x) for x in open(path) if x.strip()]
    except OSError:
        return []


def cmd_summary(d, a):
    out = {}
    for n in entrances(d):
        st, ev, late = jl(os.path.join(d, f"stats-{n}.jsonl")), jl(os.path.join(d, f"events-{n}.jsonl")), jl(os.path.join(d, f"late-{n}.jsonl"))
        con = [e for e in ev if e["ev"] == "connected"]
        out[n] = {
            "connected_events": len(con), "errors": sum(e["ev"] == "error" for e in ev), "ping_timeouts": sum(e["ev"] == "ping_timeout" for e in ev),
            "ips": sorted({e["ip"] for e in con}), "pops": sorted({str(e.get("pop")) for e in con}),
            "first_ms": st[0]["ts_ms"] if st else None, "last_ms": st[-1]["ts_ms"] if st else None, "recs": sum(s["recs"] for s in st),
            "late_recs": len(late), "late_in_stats": sum(s["late"] for s in st), "lag_max_ms": max((s["lag_max_ms"] for s in st if s["lag_max_ms"] is not None), default=None),
            "loop_late_max_ms": max((s["loop_late_ms"] for s in st), default=None), "conns_min": min((s["conns"] for s in st), default=None),
        }
    print(json.dumps(out, indent=1))


def cmd_same(d, a):
    names = entrances(d)
    ref = a.ref
    starts = {n: next((r[0] for r in read_recs(d, n)), None) for n in names}
    t0 = a.from_ms or max(v for v in starts.values() if v is not None)
    t1 = t0 + a.sec * 1000
    data = {n: {(r[1], r[2], r[3]): r for r in read_recs(d, n, t0, t1)} for n in names}
    print(f"интервал rx_ms {t0}..{t1}, опорный вход {ref}: {len(data[ref])} записей")
    for n in names:
        if n == ref:
            continue
        com = set(data[n]) & set(data[ref])
        by = defaultdict(lambda: {"common": 0, "ref_only": 0, "this_only": 0, "E_eq": 0, "T_eq": 0, "px_eq": 0})
        for k in set(data[n]) | set(data[ref]):
            b = by[k[0]]
            if k in com:
                x, y = data[n][k], data[ref][k]
                b["common"] += 1
                b["E_eq"] += x[4] == y[4]
                b["T_eq"] += x[5] == y[5]
                b["px_eq"] += x[6] == y[6]
            elif k in data[ref]:
                b["ref_only"] += 1
            else:
                b["this_only"] += 1
        for kind, b in sorted(by.items()):
            c = b["common"] or 1
            print(f"  {n:6s} {kind}: записей входа {sum(1 for k in data[n] if k[0] == kind):5d}  общих с опорным {b['common']:5d}  только у опорного {b['ref_only']:4d}  только у входа {b['this_only']:4d}  E равно {b['E_eq'] / c:.3f}  T равно {b['T_eq'] / c:.3f}  цена равна {b['px_eq'] / c:.3f}")


def cmd_episodes(d, a):
    names = entrances(d)
    late = sorted((x["rx_ms"], n) for n in names for x in jl(os.path.join(d, f"late-{n}.jsonl")))
    eps, cur = [], None
    for rx, n in late:
        if cur is None or rx - cur[1] > a.gap * 1000:
            cur = [rx, rx]
            eps.append(cur)
        cur[1] = rx
    print(f"episodes {len(eps)} (gap {a.gap} s) entrances {names}")
    for i, (t0, t1) in enumerate(eps, 1):
        w0, w1 = t0 - 15_000, t1 + 15_000
        data = {n: defaultdict(list) for n in names}
        for n in names:
            for r in read_recs(d, n, w0, w1):
                data[n][(r[1], r[2], r[3])].append(r)
        lag = {n: {k: min(r[0] - r[4] for r in v) for k, v in data[n].items()} for n in names}  # лаг первой пришедшей копии ключа на входе
        late_keys = {n: {k for k, v in lag[n].items() if v > LATE_MS} for n in names}
        allk = set().union(*late_keys.values())
        rescued = {n: sum(1 for k in late_keys[n] if any(m != n and lag[m].get(k, 10**9) <= ON_TIME_MS for m in names)) for n in names}
        all_late = sum(1 for k in allk if all(k not in lag[n] or lag[n][k] > LATE_MS for n in names) and sum(k in lag[n] for n in names) >= 2)
        print(f"\n# эпизод {i}: rx_ms {t0}..{t1} ({(t1 - t0) / 1000:.1f} с), окно записей ±15 с")
        for n in names:
            lm = max(lag[n].values(), default=None)
            lk = [data[n][k][0] for k in late_keys[n]]
            span = f"  E поздних {min(r[4] for r in lk)}..{max(r[4] for r in lk)}, символов {len({r[2] for r in lk})}, соединений {len({r[7] for r in lk})}, приём {min(r[0] for r in lk)}..{max(r[0] for r in lk)}" if lk else ""
            print(f"  {n:6s} ключей в окне {len(lag[n]):6d}  поздних {len(late_keys[n]):5d}  макс. лаг {lm} мс  вовремя на другом входе: {rescued[n]}{span}")
        for n in names:
            if late_keys[n]:
                by = {m: sum(1 for k in late_keys[n] if lag[m].get(k, 10**9) <= ON_TIME_MS) for m in names if m != n and lag[m]}
                print(f"  поздних на {n} {len(late_keys[n])}: вовремя (≤ {ON_TIME_MS} мс) на входе: " + ", ".join(f"{m} {v}" for m, v in by.items()))
        print(f"  ключей поздних на всех входах, где они есть (>=2 входов): {all_late} из {len(allk)} поздних ключей")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("summary", "same", "episodes"))
    ap.add_argument("dir")
    ap.add_argument("--ref", default="com")
    ap.add_argument("--sec", type=int, default=60)
    ap.add_argument("--from-ms", type=int, help="same: начало интервала сравнения (rx_ms); по умолчанию — общее начало всех входов")
    ap.add_argument("--gap", type=int, default=30)
    a = ap.parse_args()
    {"summary": cmd_summary, "same": cmd_same, "episodes": cmd_episodes}[a.mode](a.dir, a)


if __name__ == "__main__":
    sys.exit(main())
